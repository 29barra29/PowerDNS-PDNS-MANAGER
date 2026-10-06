"""DynDNS-Token-Verwaltung (F9 9 test_dyndns_tokens + [S5]).

Service-Funktionen direkt und die Verwaltungs-Endpunkte per ``TestClient``; ``get_session_user``/
``get_admin_session_user`` sind durch feste Benutzer ersetzt, Zonen kommen aus zwei In-Memory-PowerDNS-Servern
(echter Zonen-Index), Zonenrechte aus einem Dict.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import get_admin_session_user, get_session_user  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import DynDnsToken, User  # noqa: E402
from app.routers import dyndns as dyndns_router  # noqa: E402
from app.services import dyndns, zone_index  # noqa: E402
from app.services import ptr as ptr_service  # noqa: E402
from fakes.pdns import FakeDB, FakeResult, fake_pdns, make_zone  # noqa: E402,F401 - Fixture


class TokenDB(FakeDB):
    """FakeDB mit ``delete`` (Hard-Delete der Tokens) und Zaehler fuer ``execute``."""

    def __init__(self):
        super().__init__()
        self.deleted = []
        self.rowcount = 0
        self.select_rows: list = []

    async def delete(self, obj):
        self.deleted.append(obj)

    async def execute(self, stmt, *a, **kw):
        self.executed.append(stmt)
        res = FakeResult(self.select_rows)
        res.rowcount = self.rowcount
        return res


class Acl:
    """Zonenrechte je Benutzer: ``{user_id: {zone: "manage"|"read"}}``; Admins duerfen alles."""

    def __init__(self):
        self.perms: dict[int, dict[str, str]] = {}

    async def __call__(self, db, user, zone, *, write=False):
        if user.role == "admin":
            return True
        p = self.perms.get(user.id, {}).get(zone)
        if p is None:
            return False
        return not (write and p == "read")


@pytest.fixture
def world(monkeypatch, fake_pdns):
    zone_index.invalidate()
    fake_pdns.ns1.add_zone(make_zone("example.com."))
    fake_pdns.ns1.add_zone(make_zone("home.example.com."))
    fake_pdns.ns2.add_zone(make_zone("example.com."))
    fake_pdns.ns2.add_zone(make_zone("secret.example."))
    acl = Acl()
    monkeypatch.setattr(dyndns, "has_zone_access", acl)
    settings = {}

    async def get_bool_setting(db, key, default):
        return settings.get(key, default)

    monkeypatch.setattr(dyndns, "get_bool_setting", get_bool_setting)
    yield SimpleNamespace(pdns=fake_pdns, acl=acl, settings=settings)
    zone_index.invalidate()


def _user(uid=5, role="user", name="bob"):
    return User(id=uid, username=name, role=role, is_active=True, hashed_password="x")


# ---------------------------------------------------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------------------------------------------------
async def test_create_token_stores_hash_only(world):
    db = TokenDB()
    t, plain = await dyndns.create_token(db, user=_user(), name="  Router ", hostnames=["Home.Example.com"],
                                         allowed_types=["AAAA", "A"], ttl=120, update_ptr=True)
    assert plain.startswith(dyndns.TOKEN_PREFIX) and t.token_hash == dyndns.hash_token(plain)
    assert len(t.token_prefix) == 16 and plain.startswith(t.token_prefix)
    assert t.hostnames == ["home.example.com."] and t.allowed_types == ["A", "AAAA"] and t.name == "Router"
    assert db.added == [t] and t.is_active is True and t.created_at is not None
    ser = dyndns.serialize_token(t)
    assert "token_hash" not in ser and plain not in str(ser) and ser["hostnames"] == ["home.example.com."]


async def test_validate_hostnames_duplicate_and_format(world):
    admin = _user(1, "admin", "root")
    with pytest.raises(HTTPException) as ei:
        await dyndns.validate_hostnames_for_user(FakeDB(), admin, ["a.example.com", "A.example.com."])
    assert ei.value.status_code == 400 and "doppelt" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        await dyndns.validate_hostnames_for_user(FakeDB(), admin, ["*.example.com"])
    assert ei.value.status_code == 400 and "Wildcard" in ei.value.detail
    with pytest.raises(HTTPException) as ei:
        await dyndns.validate_hostnames_for_user(FakeDB(), admin, ["nur_unterstrich.example.com"])
    assert ei.value.status_code == 400 and "Ungueltiger Hostname" in ei.value.detail


async def test_validate_unknown_zone_is_400(world):
    with pytest.raises(HTTPException) as ei:
        await dyndns.validate_hostnames_for_user(FakeDB(), _user(1, "admin"), ["x.nirgendwo.test"])
    assert ei.value.status_code == 400 and "keiner vom Panel verwalteten Zone" in ei.value.detail


async def test_validate_foreign_zone_is_403_without_zone_name(world):
    """[S5] Ohne Leserecht nennt der Fehler die Zone nicht."""
    with pytest.raises(HTTPException) as ei:
        await dyndns.validate_hostnames_for_user(FakeDB(), _user(), ["dyn.secret.example"])
    assert ei.value.status_code == 403
    assert "secret.example." not in ei.value.detail.replace("dyn.secret.example.", "")
    assert "Keine Berechtigung" in ei.value.detail


async def test_validate_read_only_zone_names_zone(world):
    world.acl.perms[5] = {"example.com.": "read"}
    with pytest.raises(HTTPException) as ei:
        await dyndns.validate_hostnames_for_user(FakeDB(), _user(), ["dyn.example.com"])
    assert ei.value.status_code == 403 and "Zone example.com." in ei.value.detail


async def test_validate_prefers_delegated_subzone(world):
    world.acl.perms[5] = {"home.example.com.": "manage"}
    out = await dyndns.validate_hostnames_for_user(FakeDB(), _user(), ["router.home.example.com"])
    assert out == [{"hostname": "router.home.example.com.", "zone": "home.example.com."}]


async def test_hostname_status(world):
    world.acl.perms[5] = {"example.com.": "read"}
    t = DynDnsToken(user_id=5, hostnames=["a.example.com.", "b.home.example.com.", "c.weg.test."])
    st = await dyndns.hostname_status(FakeDB(), t, _user())
    assert st == [
        {"hostname": "a.example.com.", "zone": "example.com.", "status": "forbidden"},
        {"hostname": "b.home.example.com.", "zone": None, "status": "forbidden"},  # kein Leserecht -> keine Zone
        {"hostname": "c.weg.test.", "zone": None, "status": "no_zone"},
    ]


async def test_rotate_changes_hash_keeps_id(world):
    db = TokenDB()
    t, plain = await dyndns.create_token(db, user=_user(), name="R", hostnames=["home.example.com"],
                                         allowed_types=["A"], ttl=60, update_ptr=False)
    t.id = 3
    t.last_result = "good"
    new_plain = await dyndns.rotate_token(db, t)
    assert new_plain != plain and t.token_hash == dyndns.hash_token(new_plain) and t.id == 3
    assert t.last_result is None and t.token_prefix == dyndns.token_prefix(new_plain)


async def test_revoke_secret_marks_and_rotate_clears(world):
    """Fix-Runde: Sicherheitssperre entwertet das Secret; nur rotate macht den Token wieder nutzbar."""
    db = TokenDB()
    t, plain = await dyndns.create_token(db, user=_user(), name="R", hostnames=["home.example.com"],
                                         allowed_types=["A"], ttl=60, update_ptr=False)
    assert dyndns.is_secret_revoked(t) is False
    dyndns.revoke_secret(t)
    assert t.is_active is False and dyndns.is_secret_revoked(t)
    assert t.token_hash != dyndns.hash_token(plain) and len(t.token_hash) <= 128
    assert dyndns.serialize_token(t)["secret_revoked"] is True
    new_plain = await dyndns.rotate_token(db, t)
    assert not dyndns.is_secret_revoked(t) and t.token_hash == dyndns.hash_token(new_plain)
    assert t.is_active is False  # rotate aktiviert nicht implizit


async def test_revoke_tokens_of_user_includes_paused_and_skips_revoked(world):
    db = TokenDB()
    a = DynDnsToken(id=1, user_id=5, token_hash="h1", is_active=True)
    b = DynDnsToken(id=2, user_id=5, token_hash="h2", is_active=False)  # pausiert -> trotzdem entwerten
    c = DynDnsToken(id=3, user_id=5, token_hash=dyndns.REVOKED_HASH_PREFIX + "x", is_active=False)
    db.select_rows = [a, b, c]
    assert await dyndns.revoke_tokens_of_user(db, 5) == 2
    assert all(dyndns.is_secret_revoked(t) and t.is_active is False for t in (a, b, c))
    assert c.token_hash == dyndns.REVOKED_HASH_PREFIX + "x"
    assert "dyndns_tokens.user_id" in str(db.executed[0])


async def test_delete_tokens_of_user_returns_count(world):
    db = TokenDB()
    db.rowcount = 4
    assert await dyndns.delete_tokens_of_user(db, 5) == 4
    assert "DELETE FROM dyndns_tokens" in str(db.executed[0])


def test_prune_stale_drops_removed_hostnames():
    t = DynDnsToken(hostnames=["a.example.com."], stale_servers={"a.example.com.": ["ns2"], "b.example.com.": ["ns1"]})
    dyndns.prune_stale(t)
    assert t.stale_servers == {"a.example.com.": ["ns2"]}
    assert dyndns.serialize_token(t)["stale_servers"] == ["ns2"]


# ---------------------------------------------------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------------------------------------------------
class Api:
    def __init__(self, monkeypatch, world, user):
        self.db = TokenDB()
        self.user = user
        self.tokens: dict[int, DynDnsToken] = {}
        self.audit = []

        async def _db():
            yield self.db

        async def load_token(db, token_id):
            t = self.tokens.get(token_id)
            if t is None:
                raise HTTPException(status_code=404, detail="Token nicht gefunden")
            return t

        async def write_audit(db, action, rtype, name=None, **kw):
            self.audit.append((action, rtype, name, kw))
            return SimpleNamespace(id=1)

        async def count_tokens(db, uid):
            return sum(1 for t in self.tokens.values() if t.user_id == uid)

        monkeypatch.setattr(dyndns_router, "_load_token", load_token)
        monkeypatch.setattr(dyndns_router, "write_audit", write_audit)
        monkeypatch.setattr(dyndns, "count_tokens_of_user", count_tokens)
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_session_user] = lambda: self.user
        app.dependency_overrides[get_admin_session_user] = lambda: self.user
        self.client = TestClient(app, raise_server_exceptions=False)

    def add(self, tid, **kw):
        defaults = dict(id=tid, user_id=self.user.id, name=f"T{tid}", token_prefix="dnsmgr_ddns_abcd",
                        token_hash=f"h{tid}", hostnames=["home.example.com."], allowed_types=["A", "AAAA"], ttl=60,
                        update_ptr=False, is_active=True)
        defaults.update(kw)
        self.tokens[tid] = DynDnsToken(**defaults)
        return self.tokens[tid]


@pytest.fixture
def api(monkeypatch, world):
    yield lambda user: Api(monkeypatch, world, user)
    for dep in (get_db, get_session_user, get_admin_session_user):
        app.dependency_overrides.pop(dep, None)


def test_post_token_returns_plaintext_once(api, world):
    a = api(_user(1, "admin", "root"))
    r = a.client.post("/api/v1/dyndns/tokens", json={"name": "Fritzbox", "hostnames": ["home.example.com"],
                                                     "allowed_types": ["AAAA", "A", "A"]})
    assert r.status_code == 201, r.text
    body = r.json()
    plain = body["plaintext_token"]
    assert plain.startswith(dyndns.TOKEN_PREFIX) and "warning" in body
    tok = body["token"]
    assert tok["allowed_types"] == ["A", "AAAA"] and tok["hostname_status"][0]["status"] == "ok"
    assert "token_hash" not in tok
    (action, rtype, name, kw) = a.audit[0]
    assert action == "DYNDNS_TOKEN_CREATE" and rtype == "dyndns_token" and plain not in str(kw)
    assert kw["details"]["token_prefix"] == tok["token_prefix"]


def test_post_token_foreign_zone_403_without_zone_name(api, world):
    """[S5] Fremde Zone -> 403, der Text nennt die Zone nicht."""
    a = api(_user())
    r = a.client.post("/api/v1/dyndns/tokens", json={"name": "x", "hostnames": ["dyn.secret.example"]})
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "Zone secret.example" not in detail and "Keine Berechtigung" in detail


def test_post_token_disabled_globally(api, world):
    world.settings[dyndns.KEY_ENABLED] = False
    a = api(_user(1, "admin"))
    r = a.client.post("/api/v1/dyndns/tokens", json={"name": "x", "hostnames": ["home.example.com"]})
    assert r.status_code == 403 and "deaktiviert" in r.json()["detail"]


def test_post_token_limit_and_validation(api, world):
    a = api(_user(1, "admin"))
    for i in range(dyndns.MAX_TOKENS_PER_USER):
        a.add(100 + i)
    r = a.client.post("/api/v1/dyndns/tokens", json={"name": "x", "hostnames": ["home.example.com"]})
    assert r.status_code == 400 and "Maximal 50" in r.json()["detail"]
    r = a.client.post("/api/v1/dyndns/tokens", json={"name": "x", "hostnames": ["home.example.com"], "ttl": 30})
    assert r.status_code == 422
    r = a.client.post("/api/v1/dyndns/tokens", json={"name": "x", "hostnames": [], "ttl": 60})
    assert r.status_code == 422


def test_put_foreign_token_admin_only_is_active(api, world):
    a = api(_user(1, "admin", "root"))
    t = a.add(9, user_id=5)
    r = a.client.put("/api/v1/dyndns/tokens/9", json={"name": "neu"})
    assert r.status_code == 403 and "Fremde DynDNS-Tokens" in r.json()["detail"]
    r = a.client.put("/api/v1/dyndns/tokens/9", json={"is_active": False})
    assert r.status_code == 200 and t.is_active is False
    assert "hostname_status" not in r.json()
    (action, _, _, kw) = a.audit[-1]
    assert action == "DYNDNS_TOKEN_UPDATE" and kw["details"]["changed"] == {"is_active": {"from": True, "to": False}}


def test_put_is_active_on_revoked_secret_is_409_until_rotate(api, world):
    """Fix-Runde: aus Sicherheitsgruenden gesperrter Token (Secret entwertet) laesst sich nicht per PUT
    reaktivieren – weder vom Besitzer noch vom Admin; nach rotate wieder normal."""
    a = api(_user(1, "admin", "root"))
    own = a.add(2, user_id=1)
    foreign = a.add(9, user_id=5)
    for t in (own, foreign):
        dyndns.revoke_secret(t)
    for tid, t in ((2, own), (9, foreign)):
        r = a.client.put(f"/api/v1/dyndns/tokens/{tid}", json={"is_active": True})
        assert r.status_code == 409 and "neues Secret" in r.json()["detail"]
        assert t.is_active is False
    assert a.audit == []
    # Deaktivieren/sonstige Felder bleiben erlaubt
    r = a.client.put("/api/v1/dyndns/tokens/2", json={"name": "Neu", "is_active": False})
    assert r.status_code == 200 and r.json()["secret_revoked"] is True and own.name == "Neu"
    r = a.client.post("/api/v1/dyndns/tokens/2/rotate")
    assert r.status_code == 200 and r.json()["token"]["secret_revoked"] is False
    r = a.client.put("/api/v1/dyndns/tokens/2", json={"is_active": True})
    assert r.status_code == 200 and own.is_active is True and r.json()["secret_revoked"] is False


def test_put_foreign_token_non_admin_is_404(api, world):
    a = api(_user())
    a.add(9, user_id=77)
    assert a.client.put("/api/v1/dyndns/tokens/9", json={"is_active": False}).status_code == 404
    assert a.client.delete("/api/v1/dyndns/tokens/9").status_code == 404
    assert a.client.post("/api/v1/dyndns/tokens/9/rotate").status_code == 404


def test_put_own_token_validates_hostnames_and_prunes_stale(api, world):
    world.acl.perms[5] = {"example.com.": "manage"}
    a = api(_user())
    t = a.add(4, hostnames=["a.example.com.", "b.example.com."], stale_servers={"b.example.com.": ["ns2"]})
    r = a.client.put("/api/v1/dyndns/tokens/4", json={"hostnames": ["a.example.com"], "ttl": 300})
    assert r.status_code == 200
    assert t.hostnames == ["a.example.com."] and t.ttl == 300 and t.stale_servers is None
    assert r.json()["hostname_status"] == [{"hostname": "a.example.com.", "zone": "example.com.", "status": "ok"}]
    r = a.client.put("/api/v1/dyndns/tokens/4", json={"hostnames": ["x.secret.example"]})
    assert r.status_code == 403 and t.hostnames == ["a.example.com."]


def test_rotate_only_owner_returns_new_secret(api, world):
    a = api(_user(1, "admin"))
    t = a.add(2, user_id=1)
    old_hash = t.token_hash
    r = a.client.post("/api/v1/dyndns/tokens/2/rotate")
    assert r.status_code == 200 and r.json()["plaintext_token"].startswith(dyndns.TOKEN_PREFIX)
    assert t.token_hash != old_hash and a.audit[-1][0] == "DYNDNS_TOKEN_ROTATE"
    a.add(3, user_id=5)
    assert a.client.post("/api/v1/dyndns/tokens/3/rotate").status_code == 404  # auch Admin nur eigene


def test_delete_by_owner_and_admin(api, world):
    a = api(_user(1, "admin"))
    t = a.add(5, user_id=5)
    r = a.client.delete("/api/v1/dyndns/tokens/5")
    assert r.status_code == 200 and a.db.deleted == [t] and a.audit[-1][0] == "DYNDNS_TOKEN_DELETE"


def test_list_tokens_and_zones(api, world, monkeypatch):
    world.acl.perms[5] = {"example.com.": "manage", "home.example.com.": "read"}
    a = api(_user())
    t = a.add(4, hostnames=["a.example.com.", "b.secret.example."])
    a.db.select_rows = [t]

    async def writable(db, user):
        return {"example.com."}

    monkeypatch.setattr(ptr_service, "writable_zone_filter", writable)
    r = a.client.get("/api/v1/dyndns/zones")
    assert r.status_code == 200
    assert r.json() == {"zones": [{"name": "example.com.", "servers": ["ns1", "ns2"]}]}
    r = a.client.get("/api/v1/dyndns/tokens")
    assert r.status_code == 200
    (tok,) = r.json()["tokens"]
    assert tok["hostname_status"] == [
        {"hostname": "a.example.com.", "zone": "example.com.", "status": "ok"},
        {"hostname": "b.secret.example.", "zone": None, "status": "forbidden"},
    ]
    assert "token_hash" not in tok and r.json()["max_tokens"] == dyndns.MAX_TOKENS_PER_USER
