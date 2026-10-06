"""DynDNS-Update-Endpunkte (F9 9 test_dyndns_update + Bauplan WS-F9F11-BE [S6, D5]).

Die echte App laeuft per ``TestClient`` (ohne lifespan) gegen zwei In-Memory-PowerDNS-Server mit getrennten
Zonenstaenden (``tests/fakes/pdns.py``). DB-Zugriffe der DynDNS-Pipeline sind im Modul-Namespace ersetzt: Tokens und
Besitzer aus einem Speicher, Settings aus einem Dict, ``write_audit``/``write_audit_detached``/``enqueue_event``
werden aufgezeichnet. ``get_db`` liefert eine ``FakeDB`` ohne ServerConfig-Zeilen (alle Server schreibbar).
"""
from __future__ import annotations

import base64
import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.models import DynDnsToken, User  # noqa: E402
from app.services import dyndns, fanout, ptr, webhook_outbox, zone_index  # noqa: E402
from app.services.pdns_client import PowerDNSAPIError  # noqa: E402
from fakes.pdns import FakeDB, fake_pdns, make_zone, rr  # noqa: E402,F401 - Fixture

Z = "example.com."
HOME = "home.example.com."
OFFICE = "office.example.com."
TOKEN = dyndns.TOKEN_PREFIX + "A" * 43
TOKEN2 = dyndns.TOKEN_PREFIX + "B" * 43


# ---------------------------------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------------------------------
class Recorder:
    def __init__(self, ret=None):
        self.calls = []
        self.ret = ret

    async def __call__(self, *args, **kwargs):
        self.calls.append(SimpleNamespace(args=args, kwargs=kwargs))
        return self.ret(*args, **kwargs) if callable(self.ret) else self.ret

    def actions(self):
        return [c.args[1] if len(c.args) > 1 and not isinstance(c.args[0], str) else c.args[0] for c in self.calls]


class Env:
    """Speicher fuer Tokens, Besitzer und Settings; ersetzt die DB-Helfer der Pipeline."""

    def __init__(self, monkeypatch, pdns):
        self.pdns = pdns
        self.db = FakeDB()
        self.settings = {}
        self.owner = User(id=7, username="alice", role="admin", is_active=True, hashed_password="x")
        self.tokens = {}
        self.add_token(TOKEN, id=1, hostnames=[HOME])
        self.audit = Recorder(ret=lambda *a, **k: SimpleNamespace(id=500 + len(self.audit.calls)))
        self.detached = Recorder(ret=1)
        self.events = Recorder(ret=1)
        self.acl_write = None  # None = Admin-Shortcut; sonst Menge der beschreibbaren Zonen
        self.acl_read = set()

        async def get_bool_setting(db, key, default):
            v = self.settings.get(key)
            return default if v is None else bool(v)

        def by_hash(plaintext):
            # wie der echte Hash-Lookup: ein entwertetes Secret passt zu keinem Klartext mehr
            t = self.tokens.get(plaintext)
            return t if t is not None and t.token_hash == dyndns.hash_token(plaintext) else None

        async def verify_token(db, plaintext, *, remote_ip):
            t = by_hash(plaintext)
            if t is None or not t.is_active:
                return None
            t.last_used_ip = remote_ip
            return t

        async def find_token_by_plaintext(db, plaintext):
            return by_hash(plaintext)

        async def load_owner(db, token):
            if token is None or not self.owner.is_active:
                return None
            return self.owner

        async def has_zone_access(db, user, zone, *, write=False):
            if self.acl_write is None:
                return True
            if write:
                return zone in self.acl_write
            return zone in self.acl_write or zone in self.acl_read

        for name, fn in (("get_bool_setting", get_bool_setting), ("verify_token", verify_token),
                         ("find_token_by_plaintext", find_token_by_plaintext), ("load_owner", load_owner),
                         ("has_zone_access", has_zone_access)):
            monkeypatch.setattr(dyndns, name, fn)
        monkeypatch.setattr(dyndns, "write_audit", self.audit)
        monkeypatch.setattr(dyndns, "write_audit_detached", self.detached)
        monkeypatch.setattr(webhook_outbox, "enqueue_event", self.events)
        monkeypatch.setattr(fanout, "REREAD_DELAY", 0)

        async def _db():
            yield self.db

        app.dependency_overrides[get_db] = _db
        self.client = TestClient(app, raise_server_exceptions=False)

    def add_token(self, plain, **kw):
        defaults = dict(user_id=7, name="Fritzbox Buero", token_prefix=dyndns.token_prefix(plain),
                        token_hash=dyndns.hash_token(plain), hostnames=[HOME], allowed_types=["A", "AAAA"], ttl=60,
                        update_ptr=False, is_active=True, stale_servers=None)
        defaults.update(kw)
        t = DynDnsToken(**defaults)
        self.tokens[plain] = t
        return t

    @property
    def token(self) -> DynDnsToken:
        return self.tokens[TOKEN]

    def audits(self, action):
        return [c for c in self.audit.calls if c.args[1] == action]

    def get(self, path="/nic/update", *, token=TOKEN, basic=True, headers=None, **params):
        h = dict(headers or {})
        if token is not None:
            if basic:
                h["Authorization"] = "Basic " + base64.b64encode(f"dyndns:{token}".encode()).decode()
            else:
                h["Authorization"] = f"Bearer {token}"
        return self.client.get(path, params=params, headers=h)


@pytest.fixture
def env(monkeypatch, fake_pdns):
    dyndns.reset_state_for_tests()
    zone_index.invalidate()
    fake_pdns.ns1.add_zone(make_zone(Z, [rr(HOME, "A", "198.51.100.1", ttl=60), rr(OFFICE, "A", "198.51.100.2", ttl=60)]))
    fake_pdns.ns2.add_zone(make_zone(Z, [rr(HOME, "A", "198.51.100.1", ttl=60), rr(OFFICE, "A", "198.51.100.2", ttl=60)]))
    e = Env(monkeypatch, fake_pdns)
    yield e
    app.dependency_overrides.pop(get_db, None)
    dyndns.reset_state_for_tests()
    zone_index.invalidate()


# ---------------------------------------------------------------------------------------------------------------------
# Authentifizierung und Request-weite Fehler
# ---------------------------------------------------------------------------------------------------------------------
def test_missing_auth_is_401_badauth(env):
    r = env.get(token=None, hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 401 and r.text == "badauth"
    assert r.headers["www-authenticate"].startswith("Basic")
    assert r.headers["cache-control"] == "no-store"
    assert env.detached.calls[0].args[0] == "DYNDNS_AUTH_FAILED"
    assert env.detached.calls[0].kwargs["details"]["reason"] == "missing"


def test_wrong_token_is_401(env):
    r = env.get(token=TOKEN2, hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 401 and r.text == "badauth"
    assert env.detached.calls[0].kwargs["details"]["reason"] == "unknown_token"


def test_auth_failures_lock_ip_with_911_and_retry_after(env):
    for _ in range(dyndns.AUTH_FAIL_LIMIT):
        assert env.get(token=TOKEN2, hostname=HOME, myip="203.0.113.5").status_code == 401
    r = env.get(token=TOKEN2, hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 429 and r.text == "911"
    assert int(r.headers["retry-after"]) >= 1
    # Audit nur einmal je IP und 15 min
    assert [c.args[0] for c in env.detached.calls] == ["DYNDNS_AUTH_FAILED"]


def test_locked_ip_with_valid_token_still_updates(env):
    """[S6] Die IP-Sperre gilt nur ohne gueltigen Token."""
    for _ in range(dyndns.AUTH_FAIL_LIMIT + 1):
        env.get(token=TOKEN2, hostname=HOME, myip="203.0.113.5")
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 200 and r.text == "good 203.0.113.5"


def test_token_in_query_revokes_token(env):
    r = env.get(token=None, hostname=HOME, myip="203.0.113.5", password=TOKEN)
    assert r.status_code == 401 and r.text == "badauth"
    assert env.token.is_active is False
    (rev,) = env.audits("DYNDNS_TOKEN_REVOKED")
    assert rev.kwargs["details"]["reason"] == "token_in_query" and TOKEN not in str(rev.kwargs)
    # Danach hilft auch Basic-Auth nicht mehr
    assert env.get(hostname=HOME, myip="203.0.113.5").status_code == 401


def test_token_in_query_secret_stays_invalid_after_reactivation(env):
    """Fix-Runde: Sperre wegen Token im Query entwertet das Secret – auch ein spaeteres is_active=True belebt den
    (in Logs stehenden) Klartext nicht wieder."""
    r = env.get(token=None, hostname=HOME, myip="203.0.113.5", password=TOKEN)
    assert r.status_code == 401
    t = env.token
    assert dyndns.is_secret_revoked(t) and t.token_hash != dyndns.hash_token(TOKEN)
    assert dyndns.serialize_token(t)["secret_revoked"] is True and t.last_result == "badauth"
    t.is_active = True  # z. B. Reaktivieren an der API vorbei
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 401 and r.text == "badauth"
    assert env.pdns.ns1.patches == [] and env.pdns.ns2.patches == []


def test_token_value_in_any_query_param_rejected(env):
    r = env.get(hostname=HOME, myip="203.0.113.5", foo=TOKEN)
    assert r.status_code == 401 and env.token.is_active is False


def test_cross_site_browser_is_403(env):
    r = env.get(hostname=HOME, myip="203.0.113.5", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and r.text == "badauth"
    r = env.get(hostname=HOME, myip="203.0.113.5", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert env.pdns.ns1.patches == []


def test_disabled_globally_is_403(env):
    env.settings[dyndns.KEY_ENABLED] = False
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 403 and r.text == "badauth"


def test_owner_inactive_is_badauth(env):
    env.owner.is_active = False
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 401 and r.text == "badauth"
    assert env.detached.calls[0].kwargs["details"]["reason"] == "owner_inactive"


def test_token_rate_limit_911_then_abuse(env):
    """[S6] > 30 / 5 min -> 429 911 mit Retry-After; ab 300 -> 429 abuse."""
    for _ in range(dyndns.TOKEN_LIMIT):
        assert env.get(hostname=HOME, myip="198.51.100.1").status_code == 200
    r = env.get(hostname=HOME, myip="198.51.100.1")
    assert r.status_code == 429 and r.text == "911" and int(r.headers["retry-after"]) >= 1
    assert env.token.last_result == "911"
    key = dyndns._token_key(env.token.id)
    for _ in range(dyndns.TOKEN_ABUSE_THRESHOLD):
        dyndns._req_by_token.hit(key)
    r = env.get(hostname=HOME, myip="198.51.100.1")
    assert r.status_code == 429 and r.text == "abuse" and "retry-after" in r.headers
    assert env.token.last_result == "abuse"
    # ab der Schwelle wird nicht weiter gezaehlt (begrenzter Speicher)
    n = dyndns._req_by_token.count(key)
    assert env.get(hostname=HOME, myip="198.51.100.1").text == "abuse"
    assert dyndns._req_by_token.count(key) == n


# ---------------------------------------------------------------------------------------------------------------------
# good / nochg
# ---------------------------------------------------------------------------------------------------------------------
def test_good_writes_one_replace_per_server(env):
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 200 and r.text == "good 203.0.113.5"
    for srv in (env.pdns.ns1, env.pdns.ns2):
        (patch,) = srv.patches
        assert patch == [{"name": HOME, "type": "A", "ttl": 60, "changetype": "REPLACE",
                          "records": [{"content": "203.0.113.5", "disabled": False}]}]
        assert srv.values(Z, HOME, "A") == ["203.0.113.5"]
    t = env.token
    assert t.last_result == "good" and t.last_ip_v4 == "203.0.113.5" and t.last_changed_at is not None
    assert t.stale_servers is None
    (audit,) = env.audits("DYNDNS_UPDATE")
    kw = audit.kwargs
    assert audit.args[2] == "record" and audit.args[3] == HOME
    assert kw["zone_name"] == Z and kw["user_id"] == 7 and kw["server_name"] == "ns1"
    d = kw["details"]
    assert d["version"] == 2 and d["zone"] == Z and d["fanout"] == {"ns1": "saved", "ns2": "saved"}
    (ch,) = d["changes"]
    assert ch["name"] == HOME and ch["type"] == "A"
    assert [x["content"] for x in ch["before"]["records"]] == ["198.51.100.1"]
    assert [x["content"] for x in ch["after"]["records"]] == ["203.0.113.5"]
    assert d["token_id"] == 1 and d["token_name"] == "Fritzbox Buero" and d["ip_source"] == "param"
    assert d["client_ip"] == "testclient" and "repair" not in d
    # Private Angaben nur auf oberster Ebene (Antrag WS-F7-BE Fix 1)
    assert "testclient" not in str(d["changes"]) and "type" in d and d["type"] == "A"
    (ev,) = env.events.calls
    assert ev.args[1] == "dyndns.updated" and ev.kwargs["actor"] is env.owner
    data = ev.kwargs["data"]
    assert data["hostname"] == HOME and data["zone"] == Z and data["type"] == "A" and data["ttl"] == 60
    assert data["old"] == ["198.51.100.1"] and data["new"] == ["203.0.113.5"]
    assert data["token"] == {"id": 1, "prefix": env.token.token_prefix, "name": "Fritzbox Buero"}
    assert data["changes"][0]["after"] == {"ttl": 60, "records": [{"content": "203.0.113.5", "disabled": False}]}
    assert "client_ip" not in data and ev.kwargs["audit_log_id"] == 501


def test_nochg_without_write_and_audit(env):
    r = env.get(hostname=HOME, myip="198.51.100.1")
    assert r.status_code == 200 and r.text == "nochg 198.51.100.1"
    assert env.pdns.ns1.patches == [] and env.pdns.ns2.patches == []
    assert env.audit.calls == [] and env.events.calls == [] and env.detached.calls == []
    assert env.token.last_result == "nochg"


def test_ttl_difference_is_a_change(env):
    env.pdns.ns1.add_zone(make_zone(Z, [rr(HOME, "A", "198.51.100.1", ttl=300)]))
    env.pdns.ns2.add_zone(make_zone(Z, [rr(HOME, "A", "198.51.100.1", ttl=300)]))
    r = env.get(hostname=HOME, myip="198.51.100.1")
    assert r.text == "good 198.51.100.1"
    assert env.pdns.ns1.rrset(Z, HOME, "A")["ttl"] == 60


def test_v4_and_v6_in_one_patch(env):
    r = env.get(hostname=HOME, myip="203.0.113.5,2001:db8::5")
    assert r.text == "good 203.0.113.5,2001:db8::5"
    (patch,) = env.pdns.ns1.patches
    assert sorted(x["type"] for x in patch) == ["A", "AAAA"]
    assert env.token.last_ip_v6 == "2001:db8::5"


def test_placeholders_fall_back_to_client_ip(env):
    r = env.get(hostname=HOME, myip="<ipaddr>,<ip6addr>")
    assert r.text == "badip"  # TestClient-Peer ist keine oeffentliche IP


def test_only_aaaa_allowed_and_only_v4_is_badip(env):
    env.token.allowed_types = ["AAAA"]
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 200 and r.text == "badip"
    assert env.detached.calls[0].kwargs["details"]["result"] == "badip"


def test_hostname_not_in_scope_is_nohost(env):
    r = env.get(hostname=OFFICE, myip="203.0.113.5")
    assert r.text == "nohost"
    assert env.pdns.ns1.patches == []


def test_acl_revoked_is_nohost_without_zone_name(env):
    env.acl_write = set()
    r = env.get("/api/v1/dyndns/update", basic=False, hostname=HOME, myip="203.0.113.5")
    body = r.json()
    assert r.status_code == 200 and body["result"] == "nohost"
    host = body["hosts"][0]
    assert host["zone"] is None and Z not in host["detail"]
    env.acl_read = {Z}
    dyndns.reset_state_for_tests()
    host = env.get("/api/v1/dyndns/update", basic=False, hostname=HOME, myip="203.0.113.5").json()["hosts"][0]
    assert host["zone"] == Z and Z in host["detail"]


def test_no_zone_is_nohost(env):
    env.token.hostnames = ["home.other.org."]
    r = env.get(hostname="home.other.org", myip="203.0.113.5")
    assert r.text == "nohost"


def test_missing_hostname_uses_single_token_hostname(env):
    r = env.get(myip="203.0.113.5")
    assert r.text == "good 203.0.113.5"
    env.token.hostnames = [HOME, OFFICE]
    r = env.get(myip="203.0.113.6")
    assert r.status_code == 200 and r.text == "notfqdn"


def test_too_many_hostnames_is_numhost(env):
    hosts = ",".join(f"h{i}.example.com" for i in range(21))
    assert env.get(hostname=hosts, myip="203.0.113.5").text == "numhost"
    r = env.get("/api/v1/dyndns/update", basic=False, hostname=hosts, myip="203.0.113.5")
    assert r.status_code == 400 and r.json()["result"] == "numhost"


def test_multiple_hostnames_one_line_each(env):
    env.token.hostnames = [HOME, OFFICE]
    r = env.get(hostname=f"{HOME},{OFFICE},nicht-erlaubt.example.com", myip="198.51.100.1")
    assert r.status_code == 200
    assert r.text.split("\n") == ["nochg 198.51.100.1", "good 198.51.100.1", "nohost"]
    assert env.token.last_result == "nohost"


def test_all_servers_unreachable_is_503_911(env):
    for srv in (env.pdns.ns1, env.pdns.ns2):
        srv.fail_on_get = PowerDNSAPIError(503, "down", srv.name)
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 503 and r.text == "911" and r.headers["retry-after"] == "300"


def test_all_writes_fail_is_911(env):
    for srv in (env.pdns.ns1, env.pdns.ns2):
        srv.fail_on_patch = PowerDNSAPIError(500, "kaputt", srv.name)
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 503 and r.text == "911"
    assert env.token.stale_servers == {HOME: ["ns1", "ns2"]}


def test_pdns_422_is_dnserr(env):
    for srv in (env.pdns.ns1, env.pdns.ns2):
        srv.fail_on_patch = PowerDNSAPIError(422, '{"error": "Conflicts with pre-existing RRset"}', srv.name)
    r = env.get("/api/v1/dyndns/update", basic=False, hostname=HOME, myip="203.0.113.5")
    host = r.json()["hosts"][0]
    assert r.status_code == 200 and host["result"] == "dnserr"
    assert "Conflicts with pre-existing RRset" in host["detail"]


def test_cname_at_name_is_dnserr(env):
    env.pdns.ns1.add_zone(make_zone(Z, [rr(HOME, "CNAME", "elsewhere.example.net.")]))
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.text == "dnserr" and env.pdns.ns1.patches == []


def test_cache_control_on_success(env):
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"].startswith("text/plain")


# ---------------------------------------------------------------------------------------------------------------------
# [D5] Peer-Stand je Server, persistente stale_servers
# ---------------------------------------------------------------------------------------------------------------------
def test_stale_peer_is_repaired_with_audit_and_nochg(env):
    """Primary aktuell, Peer veraltet -> nur der Peer wird gepatcht, Audit mit repair, Antwort nochg."""
    env.pdns.ns1.add_zone(make_zone(Z, [rr(HOME, "A", "203.0.113.5", ttl=60)]))
    env.token.stale_servers = {HOME: ["ns2"]}
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 200 and r.text == "nochg 203.0.113.5"
    assert env.pdns.ns1.patches == [] and len(env.pdns.ns2.patches) == 1
    assert env.pdns.ns2.values(Z, HOME, "A") == ["203.0.113.5"]
    (audit,) = env.audits("DYNDNS_UPDATE")
    d = audit.kwargs["details"]
    assert d["repair"] is True and d["fanout"] == {"ns2": "saved"}
    assert audit.kwargs["server_name"] == "ns2"
    assert env.events.calls == []  # keine Aenderung aus Sicht des Clients -> kein Webhook
    assert env.token.stale_servers is None and env.token.last_changed_at is None


def test_peer_error_persists_and_survives_restart(env):
    """Peer-Fehler -> stale_servers persistent; nach "Neustart" wird beim naechsten Ping nur der Peer repariert."""
    env.pdns.ns2.fail_on_patch = PowerDNSAPIError(500, "kaputt", "ns2")
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.text == "good 203.0.113.5"
    assert env.token.stale_servers == {HOME: ["ns2"]}
    (audit,) = env.audits("DYNDNS_UPDATE")
    assert audit.kwargs["details"]["fanout"]["ns2"].startswith("error")
    # Neustart: In-Memory-Zustand weg, stale_servers liegt in der Tokenzeile
    dyndns.reset_state_for_tests()
    zone_index.invalidate()
    env.pdns.ns2.fail_on_patch = None
    env.audit.calls.clear()
    env.events.calls.clear()
    n1 = len(env.pdns.ns1.patches)
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.text == "nochg 203.0.113.5"
    assert len(env.pdns.ns1.patches) == n1 and env.pdns.ns2.values(Z, HOME, "A") == ["203.0.113.5"]
    (repair,) = env.audits("DYNDNS_UPDATE")
    assert repair.kwargs["details"]["repair"] is True and repair.kwargs["details"]["fanout"] == {"ns2": "saved"}
    assert env.token.stale_servers is None


def test_stale_first_server_is_repaired_with_nochg(env):
    """Fix-Runde [D5]: Ist ausgerechnet der erste Server veraltet, ist der naechste Ping mit gleicher IP eine
    Reparatur (nochg, repair=true, kein Webhook, last_changed_at unveraendert) und keine neue Aenderung."""
    env.pdns.ns1.fail_on_patch = PowerDNSAPIError(500, "kaputt", "ns1")
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.text == "good 203.0.113.5" and env.token.stale_servers == {HOME: ["ns1"]}
    assert len(env.events.calls) == 1
    changed_at = env.token.last_changed_at
    # Neustart: nur stale_servers in der Tokenzeile bleibt
    dyndns.reset_state_for_tests()
    zone_index.invalidate()
    env.pdns.ns1.fail_on_patch = None
    env.audit.calls.clear()
    env.events.calls.clear()
    n2 = len(env.pdns.ns2.patches)
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.text == "nochg 203.0.113.5"
    assert len(env.pdns.ns2.patches) == n2 and env.pdns.ns1.values(Z, HOME, "A") == ["203.0.113.5"]
    (repair,) = env.audits("DYNDNS_UPDATE")
    d = repair.kwargs["details"]
    assert d["repair"] is True and d["fanout"] == {"ns1": "saved"} and repair.kwargs["server_name"] == "ns1"
    assert env.events.calls == []
    assert env.token.stale_servers is None and env.token.last_changed_at == changed_at


def test_stale_first_server_with_new_ip_reports_old_from_current_server(env):
    """Erster Server veraltet, Client meldet eine neue IP: echte Aenderung, ``old`` kommt vom aktuellen Server."""
    env.pdns.ns2.add_zone(make_zone(Z, [rr(HOME, "A", "203.0.113.5", ttl=60)]))
    env.token.stale_servers = {HOME: ["ns1"]}
    r = env.get(path="/api/v1/dyndns/update", basic=False, hostname=HOME, myip="203.0.113.9")
    assert r.status_code == 200, r.text
    (host,) = r.json()["hosts"]
    assert host["result"] == "good" and not host.get("repair")
    (change,) = [c for c in host["changes"] if c["type"] == "A"]
    assert change["old"] == ["203.0.113.5"] and change["status"] == "updated"
    (event,) = env.events.calls
    assert event.kwargs["data"]["old"] == ["203.0.113.5"]
    assert env.pdns.ns1.values(Z, HOME, "A") == ["203.0.113.9"] == env.pdns.ns2.values(Z, HOME, "A")


def test_unreachable_peer_is_stale_but_nochg(env):
    env.pdns.ns2.fail_on_get = PowerDNSAPIError(503, "down", "ns2")
    r = env.get(hostname=HOME, myip="198.51.100.1")
    assert r.text == "nochg 198.51.100.1"
    assert env.token.stale_servers == {HOME: ["ns2"]}
    assert env.audit.calls == [] and env.pdns.ns2.patches == []


def test_primary_failure_still_writes_peer(env):
    """require_primary=False: faellt der erste Server aus, zaehlt der Peer (good)."""
    env.pdns.ns1.fail_on_patch = PowerDNSAPIError(500, "kaputt", "ns1")
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.text == "good 203.0.113.5"
    assert env.pdns.ns2.values(Z, HOME, "A") == ["203.0.113.5"]
    (audit,) = env.audits("DYNDNS_UPDATE")
    assert audit.kwargs["server_name"] == "ns2" and env.token.stale_servers == {HOME: ["ns1"]}


# ---------------------------------------------------------------------------------------------------------------------
# PTR
# ---------------------------------------------------------------------------------------------------------------------
def test_update_ptr_removes_old_and_sets_new(env, monkeypatch):
    env.token.update_ptr = True
    rec = Recorder(ret=[{"ip": "203.0.113.5", "ptr": "5.113.0.203.in-addr.arpa.", "zone": None, "action": "set",
                         "reason": None}])
    monkeypatch.setattr(ptr, "sync_ptrs", rec)
    r = env.get(hostname=HOME, myip="203.0.113.5")
    assert r.text == "good 203.0.113.5"
    (call,) = rec.calls
    ops = call.args[1]
    assert [(o.op, o.ip, o.target) for o in ops] == [("remove", "198.51.100.1", HOME), ("set", "203.0.113.5", HOME)]
    assert ops[1].ttl == 60 and call.kwargs["acl_user"] is env.owner
    assert call.kwargs["source"] == {"action": "DYNDNS_UPDATE", "zone": Z, "name": HOME, "server": None}
    d = env.audits("DYNDNS_UPDATE")[0].kwargs["details"]
    assert d["ptr"] == [{"ip": "203.0.113.5", "ptr": "5.113.0.203.in-addr.arpa.", "zone": None, "action": "set",
                         "reason": None}]
    assert env.events.calls[0].kwargs["data"]["ptr"] == d["ptr"]


# ---------------------------------------------------------------------------------------------------------------------
# JSON-Variante und whoami
# ---------------------------------------------------------------------------------------------------------------------
def test_json_get_with_bearer(env):
    r = env.get("/api/v1/dyndns/update", basic=False, hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["result"] == "good" and body["ip_source"] == "param" and body["client_ip"] == "testclient"
    host = body["hosts"][0]
    assert host["hostname"] == HOME and host["zone"] == Z and host["ips"] == ["203.0.113.5"]
    assert host["changes"] == [{"type": "A", "status": "updated", "old": ["198.51.100.1"], "new": "203.0.113.5",
                                "reason": None}]
    assert host["fanout"] == {"ns1": "saved", "ns2": "saved"}


def test_json_post_body_overrides_query(env):
    h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    r = env.client.post("/api/v1/dyndns/update", params={"hostname": HOME, "myip": "203.0.113.9"},
                        json={"myip": "203.0.113.7"}, headers=h)
    assert r.status_code == 200 and r.json()["hosts"][0]["ips"] == ["203.0.113.7"]


def test_json_post_broken_body_is_400(env):
    h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    r = env.client.post("/api/v1/dyndns/update", content=b"{kaputt", headers=h)
    assert r.status_code == 400 and r.json() == {"result": "badrequest", "detail": "Ungueltiger JSON-Body"}


def test_json_errors_carry_detail(env):
    r = env.get("/api/v1/dyndns/update", token=None)
    assert r.status_code == 401 and r.json()["result"] == "badauth"
    assert "Bearer" in r.headers["www-authenticate"]
    r = env.get("/api/v1/dyndns/update", basic=False, hostname=HOME, myip="1.2.3")
    assert r.status_code == 400 and r.json() == {"result": "badip", "detail": "Ungueltige IP-Adresse: 1.2.3"}


def test_whoami(env):
    r = env.get("/api/v1/dyndns/whoami", basic=False)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["hostnames"] == [HOME] and body["token_prefix"] == env.token.token_prefix
    assert TOKEN not in r.text and "token_hash" not in body
    assert env.get("/api/v1/dyndns/whoami", token=TOKEN2).status_code == 401
    assert env.pdns.ns1.patches == []


def test_nic_update_is_text_not_spa(env):
    r = env.client.get("/nic/update")
    assert r.status_code == 401 and r.text == "badauth" and "<html" not in r.text.lower()


def test_update_ptr_end_to_end_with_reverse_zone(env, monkeypatch):
    """Echter PTR-Service: alter PTR (zeigt auf den Hostnamen) wird entfernt, neuer gesetzt; PTR_SYNC-Audit."""
    old_rev, new_rev = "100.51.198.in-addr.arpa.", "113.0.203.in-addr.arpa."
    env.pdns.ns1.add_zone(make_zone(old_rev, [rr("1." + old_rev, "PTR", HOME)]))
    env.pdns.ns1.add_zone(make_zone(new_rev))
    env.token.update_ptr = True
    ptr_audit = Recorder(ret=SimpleNamespace(id=777))
    monkeypatch.setattr(ptr, "write_audit", ptr_audit)
    r = env.get("/api/v1/dyndns/update", basic=False, hostname=HOME, myip="203.0.113.5")
    assert r.status_code == 200
    host = r.json()["hosts"][0]
    assert host["result"] == "good"
    assert [(p["op"], p["action"], p["zone"]) for p in host["ptr"]] == [("remove", "removed", old_rev),
                                                                         ("set", "set", new_rev)]
    assert env.pdns.ns1.rrset(old_rev, "1." + old_rev, "PTR") is None
    assert env.pdns.ns1.rrset(new_rev, "5." + new_rev, "PTR")["ttl"] == 60
    assert sorted(c.args[3] for c in ptr_audit.calls) == ["1." + old_rev, "5." + new_rev]
    src = ptr_audit.calls[0].kwargs["details"]["source"]
    assert src == {"action": "DYNDNS_UPDATE", "zone": Z, "name": HOME, "server": None}
    assert [c.args[1] for c in env.events.calls] == ["record.ptr_synced", "record.ptr_synced", "dyndns.updated"]
