"""Tests fuer services/sso_provisioning.py (F10 9.1 Nr. 1-7, 22; Plan S1, S5).

Die meisten Faelle laufen gegen eine synchrone SQLite-Datenbank hinter einem kleinen AsyncSession-Adapter
(aiosqlite ist nicht im Testimage). MariaDB-spezifisches Verhalten (binaere Kollation der externen IDs,
case-insensitive Benutzernamen) pruefen die ``requires_db``-Tests am Ende.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import secrets as secret_store  # noqa: E402
from app.core.auth import verify_password  # noqa: E402
from app.models.models import AuditLog, SystemSetting, User, WebAuthnCredential  # noqa: E402
from app.services import sso_provisioning as prov  # noqa: E402
from app.services.sso_provisioning import (  # noqa: E402
    ExternalProfile,
    ProvisioningError,
    ProvisioningPolicy,
)
from dbutil import requires_db  # noqa: E402


# ---------------------------------------------------------------------------------------------
# SQLite hinter einem AsyncSession-Adapter
# ---------------------------------------------------------------------------------------------
class _AsyncNested:
    def __init__(self, session: Session):
        self._s = session
        self._tx = None

    async def __aenter__(self):
        self._tx = self._s.begin_nested()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._tx.commit()
        else:
            self._tx.rollback()
        return False


class AsyncSessionAdapter:
    """Minimaler AsyncSession-Ersatz ueber einer synchronen SQLite-Session."""

    def __init__(self, session: Session):
        self.sync_session = session

    async def execute(self, *args, **kwargs):
        return self.sync_session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self.sync_session.scalar(*args, **kwargs)

    def add(self, obj):
        self.sync_session.add(obj)

    async def flush(self):
        self.sync_session.flush()

    async def commit(self):
        self.sync_session.commit()

    async def rollback(self):
        self.sync_session.rollback()

    def begin_nested(self):
        return _AsyncNested(self.sync_session)


def _sqlite_engine():
    eng = create_engine("sqlite://")

    # pysqlite-Transaktionsverhalten korrigieren, damit SAVEPOINTs funktionieren (SQLAlchemy-Doku)
    @event.listens_for(eng, "connect")
    def _connect(dbapi_conn, _rec):
        dbapi_conn.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    for model in (User, WebAuthnCredential, AuditLog, SystemSetting):
        model.__table__.create(eng)
    return eng


@pytest.fixture
def db():
    secret_store.configure_for_tests()
    eng = _sqlite_engine()
    with Session(eng) as s:
        yield AsyncSessionAdapter(s)
    eng.dispose()


@pytest.fixture
def fast_hash(monkeypatch):
    """bcrypt ist langsam – fuer Tests, die den Hash nicht pruefen, ein schneller Ersatz."""
    monkeypatch.setattr(prov, "hash_password", lambda pw: "fake$" + hashlib.sha256(pw.encode()).hexdigest())


def run(coro):
    return asyncio.run(coro)


def add_user(db, username, *, role="user", is_active=True, auth_source="local", issuer=None, ext_id=None,
             email=None, display_name=None):
    u = User(username=username, hashed_password="x", role=role, is_active=is_active, auth_source=auth_source,
             external_issuer=issuer, external_id=ext_id, email=email, display_name=display_name)
    db.add(u)
    db.sync_session.flush()
    return u


def audits(db, action=None):
    q = select(AuditLog).order_by(AuditLog.id)
    if action:
        q = q.where(AuditLog.action == action)
    return list(db.sync_session.execute(q).scalars())


def profile(**kw) -> ExternalProfile:
    base = dict(source="oidc", issuer="https://idp.example", subject="sub-1", username_hint="jdoe",
                email="jdoe@example.com", email_verified=True, display_name="John Doe", groups=["staff"])
    base.update(kw)
    return ExternalProfile(**base)


OPEN = ProvisioningPolicy(source="oidc", jit_enabled=True, jit_allow_any_account=True)


# ---------------------------------------------------------------------------------------------
# Nr. 1 sanitize_username
# ---------------------------------------------------------------------------------------------
def test_sanitize_username_cases():
    assert prov.sanitize_username("max.mustermann@firma.de", "s") == "max.mustermann"
    # ß hat keine NFKD-Zerlegung und entfaellt (dokumentiertes Verhalten)
    assert prov.sanitize_username("Jürgen Groß", "s") == "Jurgen-Gro"
    expected = "user-" + hashlib.sha256(b"seed-1").hexdigest()[:8]
    assert prov.sanitize_username("ab", "seed-1") == expected
    assert len(prov.sanitize_username("a" * 200, "s")) == 64
    assert prov.sanitize_username("!!!***", "seed-1") == expected
    assert prov.sanitize_username(None, "seed-1") == expected
    assert prov.sanitize_username("..--abc--..", "s") == "abc"
    assert prov.sanitize_username("a  b//c", "s") == "a-b-c"


# ---------------------------------------------------------------------------------------------
# Nr. 2 unique_username
# ---------------------------------------------------------------------------------------------
def test_unique_username_suffixes(db):
    add_user(db, "jdoe")
    add_user(db, "jdoe-2")
    assert run(prov.unique_username(db, "jdoe")) == "jdoe-3"
    assert run(prov.unique_username(db, "frei")) == "frei"


def test_unique_username_random_after_99(db):
    add_user(db, "jdoe")
    for n in range(2, 100):
        add_user(db, f"jdoe-{n}")
    name = run(prov.unique_username(db, "jdoe"))
    assert re.fullmatch(r"jdoe-[0-9a-f]{6}", name)


# ---------------------------------------------------------------------------------------------
# Nr. 3 group_matches – LDAP nur vollstaendige DN [S1], OIDC Stringvergleich
# ---------------------------------------------------------------------------------------------
def test_ldap_group_other_ou_does_not_match():
    conf = ["CN=pdns-admins,OU=Groups,DC=x"]
    assert prov.group_matches(conf, ["CN=pdns-admins,OU=Other,DC=x"], source="ldap") == []
    assert prov.group_matches(["CN=pdns-admins,OU=Other,DC=x"], ["CN=pdns-admins,OU=Groups,DC=x"],
                              source="ldap") == []


def test_ldap_group_case_and_spaces_irrelevant():
    conf = ["CN=PDNS-Admins,OU=Groups,DC=Example,DC=com"]
    user = ["cn = pdns-admins , ou=GROUPS,dc=example ,  dc=COM"]
    assert prov.group_matches(conf, user, source="ldap") == conf
    # Leerzeichenfolgen im Wert zaehlen wie ein Leerzeichen (caseIgnoreMatch)
    assert prov.group_matches(["CN=PDNS  Admins,DC=x"], ["cn=pdns admins,dc=x"], source="ldap")


def test_ldap_group_escapes_and_multivalued_rdn():
    assert prov.group_matches(["CN=Smith\\, John,OU=G,DC=x"], ["cn=smith\\2C john,ou=g,dc=x"], source="ldap")
    assert prov.group_matches(["CN=a+UID=b,DC=x"], ["uid=b+cn=a,dc=x"], source="ldap")
    assert prov.group_matches(["CN=M\\C3\\BCller,DC=x"], ["cn=müller,dc=x"], source="ldap")


def test_ldap_group_plain_name_never_matches():
    # Frueher (Spec): Name gegen erstes RDN – jetzt nur vollstaendige DN [S1]
    assert prov.group_matches(["pdns-admins"], ["CN=pdns-admins,OU=Groups,DC=x"], source="ldap") == []
    assert prov.group_matches(["CN=x,DC=y"], ["kein dn", None, 5], source="ldap") == []


def test_oidc_group_string_compare():
    assert prov.group_matches(["/pdns-admins"], ["pdns-admins"], source="oidc") == ["/pdns-admins"]
    assert prov.group_matches(["PDNS-Admins"], ["/pdns-admins"], source="oidc") == ["PDNS-Admins"]
    assert prov.group_matches(["pdns-admins"], ["other"], source="oidc") == []
    assert prov.group_matches(["pdns-admins"], ["CN=pdns-admins,OU=G,DC=x"], source="oidc") == []
    assert prov.group_matches([], ["a"], source="oidc") == []
    assert prov.group_matches(["a"], None, source="oidc") == []


def test_dn_key_validity():
    assert prov.is_valid_dn("CN=a,DC=b")
    assert not prov.is_valid_dn("pdns-admins")
    assert not prov.is_valid_dn("CN=")
    assert not prov.is_valid_dn("")
    assert not prov.is_valid_dn(None)


# ---------------------------------------------------------------------------------------------
# Nr. 4 desired_role
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("mode,groups,expected", [
    ("off", ["admins"], None),
    ("off", None, None),
    ("promote", ["admins"], "admin"),
    ("promote", ["staff"], None),
    ("promote", None, None),
    ("sync", ["admins"], "admin"),
    ("sync", ["staff"], "user"),
    ("sync", [], "user"),
    ("sync", None, None),
])
def test_desired_role_matrix(mode, groups, expected):
    pol = ProvisioningPolicy(source="oidc", jit_enabled=False, admin_groups=("admins",), role_mode=mode)
    assert prov.desired_role(pol, profile(groups=groups), "user") == expected


# ---------------------------------------------------------------------------------------------
# Nr. 5 apply_role
# ---------------------------------------------------------------------------------------------
def test_apply_role_skips_last_admin(db):
    u = add_user(db, "boss", role="admin", auth_source="oidc", issuer="i", ext_id="s")
    add_user(db, "old-admin", role="admin", is_active=False)   # inaktiv zaehlt nicht
    rc = run(prov.apply_role(db, u, "user", source="oidc", matched=[]))
    assert rc is None
    assert u.role == "admin"
    [entry] = audits(db, "USER_ROLE_SYNC")
    assert entry.details["skipped"] == "last_admin"
    assert entry.details["from"] == "admin" and entry.details["to"] == "user"


def test_apply_role_demotes_with_second_admin(db):
    u = add_user(db, "boss", role="admin", auth_source="oidc", issuer="i", ext_id="s")
    add_user(db, "other", role="admin")
    rc = run(prov.apply_role(db, u, "user", source="oidc", matched=["x"]))
    assert rc == ("admin", "user")
    assert u.role == "user"
    [entry] = audits(db, "USER_ROLE_SYNC")
    assert "skipped" not in entry.details
    assert entry.details["target_user_id"] == u.id and entry.details["source"] == "oidc"


def test_apply_role_promote_audits_groups(db):
    u = add_user(db, "jdoe", auth_source="ldap", issuer="ldap", ext_id="g")
    rc = run(prov.apply_role(db, u, "admin", source="ldap", matched=[f"CN=g{i},DC=x" for i in range(15)]))
    assert rc == ("user", "admin")
    [entry] = audits(db, "USER_ROLE_SYNC")
    assert len(entry.details["groups"]) == 10
    assert run(prov.apply_role(db, u, "admin", source="ldap")) is None   # keine Aenderung


# ---------------------------------------------------------------------------------------------
# Nr. 6 resolve_external_user
# ---------------------------------------------------------------------------------------------
def test_jit_creates_account_with_random_password(db):
    res = run(prov.resolve_external_user(db, profile(), OPEN))
    u = res.user
    assert res.created and res.role_change is None
    assert u.auth_source == "oidc" and u.external_issuer == "https://idp.example" and u.external_id == "sub-1"
    assert u.username == "jdoe" and u.email == "jdoe@example.com" and u.display_name == "John Doe"
    assert u.role == "user" and u.is_active
    assert u.hashed_password and not verify_password("", u.hashed_password)
    [entry] = audits(db, "USER_CREATE")
    assert entry.details["via"] == "oidc" and entry.details["external_issuer"] == "https://idp.example"
    assert entry.details["target_user_id"] == u.id
    assert res.audit_extra == {"jit": True, "issuer": "https://idp.example"}


def test_second_login_finds_same_account(db, fast_hash):
    first = run(prov.resolve_external_user(db, profile(), OPEN)).user
    res = run(prov.resolve_external_user(db, profile(display_name="John D."), OPEN))
    assert res.user.id == first.id and not res.created
    assert res.profile_updated == ["display_name"]
    assert res.audit_extra["jit"] is False and res.audit_extra["profile_updated"] == ["display_name"]


def test_jit_disabled_no_account(db):
    pol = ProvisioningPolicy(source="oidc", jit_enabled=False)
    with pytest.raises(ProvisioningError) as ei:
        run(prov.resolve_external_user(db, profile(), pol))
    assert ei.value.code == "no_account"


def test_jit_without_restriction_or_confirmation_is_refused(db):
    # S5: JIT an, aber weder Gruppen/Domains noch jit_allow_any_account -> keine Anlage
    pol = ProvisioningPolicy(source="oidc", jit_enabled=True)
    assert not pol.jit_permitted
    with pytest.raises(ProvisioningError) as ei:
        run(prov.resolve_external_user(db, profile(), pol))
    assert ei.value.code == "no_account"
    assert audits(db, "USER_CREATE") == []


def test_inactive_account_disabled(db):
    u = add_user(db, "jdoe", is_active=False, auth_source="oidc", issuer="https://idp.example", ext_id="sub-1")
    with pytest.raises(ProvisioningError) as ei:
        run(prov.resolve_external_user(db, profile(), OPEN))
    assert ei.value.code == "account_disabled" and ei.value.user_id == u.id


def test_missing_groups_with_allowed_groups_not_allowed(db):
    pol = ProvisioningPolicy(source="oidc", jit_enabled=True, allowed_groups=("pdns",))
    with pytest.raises(ProvisioningError) as ei:
        run(prov.resolve_external_user(db, profile(groups=None), pol))
    assert ei.value.code == "not_allowed"
    with pytest.raises(ProvisioningError):
        run(prov.resolve_external_user(db, profile(groups=["other"]), pol))


def test_allowed_group_member_gets_account(db, fast_hash):
    pol = ProvisioningPolicy(source="oidc", jit_enabled=True, allowed_groups=("/pdns",))
    assert run(prov.resolve_external_user(db, profile(groups=["pdns"]), pol)).created


def test_email_domain_requires_verified_email(db, fast_hash):
    pol = ProvisioningPolicy(source="oidc", jit_enabled=True, allowed_email_domains=("example.com",))
    assert pol.jit_permitted
    with pytest.raises(ProvisioningError) as ei:
        run(prov.resolve_external_user(db, profile(email_verified=None), pol))
    assert ei.value.code == "not_allowed"
    with pytest.raises(ProvisioningError):
        run(prov.resolve_external_user(db, profile(email="x@other.org"), pol))
    with pytest.raises(ProvisioningError):  # Subdomain ist eine andere Domain
        run(prov.resolve_external_user(db, profile(email="x@sub.example.com"), pol))
    res = run(prov.resolve_external_user(db, profile(email="JDoe@Example.com"), pol))
    assert res.created


def test_groups_or_domain_either_suffices(db, fast_hash):
    pol = ProvisioningPolicy(source="oidc", jit_enabled=True, allowed_groups=("pdns",),
                             allowed_email_domains=("example.com",))
    assert run(prov.resolve_external_user(db, profile(groups=None), pol)).created   # Domain reicht
    assert run(prov.resolve_external_user(
        db, profile(subject="sub-2", email="a@other.org", groups=["pdns"]), pol)).created


def test_email_taken_or_unverified_not_used(db, fast_hash):
    add_user(db, "someone", email="jdoe@example.com")
    res = run(prov.resolve_external_user(db, profile(), OPEN))
    assert res.user.email is None
    res2 = run(prov.resolve_external_user(db, profile(subject="sub-2", email="new@example.com",
                                                      email_verified=False), OPEN))
    assert res2.user.email is None


def test_username_collision_with_local_account(db, fast_hash):
    local = add_user(db, "jdoe")
    res = run(prov.resolve_external_user(db, profile(), OPEN))
    assert res.user.id != local.id and res.user.username == "jdoe-2"
    assert local.auth_source == "local" and local.external_id is None


def test_never_links_by_username_or_email(db, fast_hash):
    local = add_user(db, "jdoe", email="jdoe@example.com")
    res = run(prov.resolve_external_user(db, profile(), OPEN))
    assert res.created and res.user.id != local.id


def test_subject_is_case_sensitive(db, fast_hash):
    a = run(prov.resolve_external_user(db, profile(subject="Abc"), OPEN)).user
    b = run(prov.resolve_external_user(db, profile(subject="abc", username_hint="other"), OPEN)).user
    assert a.id != b.id


def test_integrity_error_takes_existing_account(db, fast_hash, monkeypatch):
    existing = add_user(db, "racer", auth_source="oidc", issuer="https://idp.example", ext_id="sub-1")
    real_find = prov.find_external_user
    calls = {"n": 0}

    async def find_once_none(session, source, issuer, subject):
        calls["n"] += 1
        if calls["n"] == 1:
            return None   # paralleler Erstlogin: das Konto entsteht erst nach unserer Suche
        return await real_find(session, source, issuer, subject)

    monkeypatch.setattr(prov, "find_external_user", find_once_none)
    res = run(prov.resolve_external_user(db, profile(), OPEN))
    assert res.user.id == existing.id and not res.created
    assert audits(db, "USER_CREATE") == []


def test_role_sync_on_login(db, fast_hash):
    pol = ProvisioningPolicy(source="oidc", jit_enabled=True, jit_allow_any_account=True,
                             admin_groups=("admins",), role_mode="sync")
    created = run(prov.resolve_external_user(db, profile(groups=["admins"]), pol))
    assert created.user.role == "admin"
    add_user(db, "other-admin", role="admin")
    res = run(prov.resolve_external_user(db, profile(groups=["staff"]), pol))
    assert res.role_change == ("admin", "user") and res.user.role == "user"
    assert res.audit_extra["role_changed"] == {"from": "admin", "to": "user"}


def test_ldap_profile_has_no_issuer_in_audit_extra(db, fast_hash):
    pol = ProvisioningPolicy(source="ldap", jit_enabled=True, jit_allow_any_account=True)
    res = run(prov.resolve_external_user(db, profile(source="ldap", issuer="ldap", subject="guid-1"), pol))
    assert res.audit_extra == {"jit": True}


# ---------------------------------------------------------------------------------------------
# Nr. 7 link_external_identity
# ---------------------------------------------------------------------------------------------
def test_link_conflict(db):
    add_user(db, "other", auth_source="oidc", issuer="https://idp.example", ext_id="sub-1")
    me = add_user(db, "me")
    with pytest.raises(ProvisioningError) as ei:
        run(prov.link_external_identity(db, me, profile(), OPEN))
    assert ei.value.code == "link_conflict"
    assert me.auth_source == "local"


def test_link_success_resets_credentials(db, fast_hash):
    me = add_user(db, "me")
    me.must_change_password = True
    old_hash = me.hashed_password
    for i in range(2):
        db.add(WebAuthnCredential(user_id=me.id, name=f"k{i}", credential_id=f"cred-{i}", public_key="pk"))
    db.add(WebAuthnCredential(user_id=999, name="fremd", credential_id="cred-x", public_key="pk"))
    db.sync_session.flush()
    out = run(prov.link_external_identity(db, me, profile(display_name="Me Myself"), OPEN))
    assert out["passkeys_removed"] == 2 and out["role_changed"] is None
    assert "display_name" in out["profile_updated"]
    assert me.auth_source == "oidc" and me.external_id == "sub-1" and me.external_issuer == "https://idp.example"
    assert me.hashed_password != old_hash and me.must_change_password is False
    left = db.sync_session.execute(select(WebAuthnCredential)).scalars().all()
    assert [c.credential_id for c in left] == ["cred-x"]


def test_link_not_allowed(db):
    me = add_user(db, "me")
    pol = ProvisioningPolicy(source="oidc", jit_enabled=False, allowed_groups=("pdns",))
    with pytest.raises(ProvisioningError) as ei:
        run(prov.link_external_identity(db, me, profile(groups=["x"]), pol))
    assert ei.value.code == "not_allowed"


def _enable_sso(db):
    db.add(SystemSetting(key="oidc_enabled", value="true"))
    db.sync_session.flush()


def test_link_last_local_admin_with_sso_active(db):
    _enable_sso(db)
    me = add_user(db, "admin", role="admin")
    with pytest.raises(HTTPException) as ei:
        run(prov.link_external_identity(db, me, profile(), OPEN))
    assert ei.value.status_code == 400
    assert ei.value.detail == prov.LAST_LOCAL_ADMIN_DETAIL
    assert me.auth_source == "local"


# ---------------------------------------------------------------------------------------------
# Nr. 22 assert_keeps_local_admin
# ---------------------------------------------------------------------------------------------
def test_keeps_local_admin_sso_off_never_fails(db):
    me = add_user(db, "admin", role="admin")
    run(prov.assert_keeps_local_admin(db, me, new_active=False))
    run(prov.assert_keeps_local_admin(db, me, new_role="user"))
    run(prov.assert_keeps_local_admin(db, me, removing=True))


@pytest.mark.parametrize("kwargs", [{"new_active": False}, {"new_role": "user"}, {"removing": True}])
def test_keeps_local_admin_sso_on_last_admin(db, kwargs):
    _enable_sso(db)
    me = add_user(db, "admin", role="admin")
    add_user(db, "ext-admin", role="admin", auth_source="ldap", issuer="ldap", ext_id="g")  # zaehlt nicht
    add_user(db, "inactive-admin", role="admin", is_active=False)                         # zaehlt nicht
    with pytest.raises(HTTPException) as ei:
        run(prov.assert_keeps_local_admin(db, me, **kwargs))
    assert ei.value.status_code == 400


def test_keeps_local_admin_second_admin_ok(db):
    _enable_sso(db)
    me = add_user(db, "admin", role="admin")
    add_user(db, "admin2", role="admin")
    run(prov.assert_keeps_local_admin(db, me, removing=True))
    run(prov.assert_keeps_local_admin(db, me, new_role="admin", new_active=True))   # bleibt Admin


def test_keeps_local_admin_irrelevant_targets(db, monkeypatch):
    _enable_sso(db)

    async def boom(*_a, **_k):
        raise AssertionError("keine Abfrage erwartet")

    monkeypatch.setattr(prov, "count_active_local_admins", boom)
    run(prov.assert_keeps_local_admin(db, add_user(db, "u1"), removing=True))
    run(prov.assert_keeps_local_admin(db, add_user(db, "x", role="admin", auth_source="oidc", issuer="i",
                                                   ext_id="s"), removing=True))
    run(prov.assert_keeps_local_admin(db, add_user(db, "y", role="admin"), new_role="admin"))


def test_count_helpers(db):
    add_user(db, "a1", role="admin")
    add_user(db, "a2", role="admin", auth_source="oidc", issuer="i", ext_id="s")
    add_user(db, "a3", role="admin", is_active=False)
    add_user(db, "u1")
    assert run(prov._count_active_admins(db)) == 2
    assert run(prov.count_active_local_admins(db)) == 1


# ---------------------------------------------------------------------------------------------
# Parallele Workstreams (Welle-1-Integration): F3 user_guard
# ---------------------------------------------------------------------------------------------
@pytest.mark.wave_integration
def test_admin_count_matches_f3_user_guard(db):
    from app.services import user_guard   # WS-F2F3

    add_user(db, "a1", role="admin")
    add_user(db, "a2", role="admin", is_active=False)
    a3 = add_user(db, "a3", role="admin", auth_source="oidc", issuer="i", ext_id="s")
    for excl in (None, a3.id):
        assert run(user_guard.count_active_admins(db, exclude_user_id=excl)) == \
            run(prov._count_active_admins(db, exclude_user_id=excl))


# ---------------------------------------------------------------------------------------------
# MariaDB (binaere Kollation der externen IDs, case-insensitive Benutzernamen)
# ---------------------------------------------------------------------------------------------
async def _with_session(fn):
    from app.core.database import async_session

    async with async_session() as s:
        try:
            return await fn(s)
        finally:
            await s.rollback()


@requires_db
def test_db_subject_case_and_username_collation(fresh_db, monkeypatch):
    monkeypatch.setattr(prov, "hash_password", lambda pw: "fake$" + hashlib.sha256(pw.encode()).hexdigest())

    async def scenario(s):
        s.add(User(username="admin", hashed_password="x", role="admin", is_active=True))
        await s.flush()
        a = await prov.resolve_external_user(s, profile(subject="Abc", username_hint="Admin"), OPEN)
        b = await prov.resolve_external_user(s, profile(subject="abc", username_hint="Admin"), OPEN)
        again = await prov.resolve_external_user(s, profile(subject="Abc", username_hint="Admin"), OPEN)
        return a.user, b.user, again.user

    a, b, again = asyncio.run(_with_session(scenario))
    assert a.id != b.id                     # sub ist case-sensitiv (utf8mb4_bin)
    assert again.id == a.id
    assert a.username == "Admin-2"          # Kollision mit lokalem "admin" erkannt (case-insensitive Kollation)
    assert b.username == "Admin-3"


@requires_db
def test_db_unique_identity_and_race(fresh_db, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    monkeypatch.setattr(prov, "hash_password", lambda pw: "fake$" + hashlib.sha256(pw.encode()).hexdigest())

    async def dup(s):
        s.add(User(username="e1", hashed_password="x", auth_source="ldap", external_issuer="ldap", external_id="g"))
        await s.flush()
        s.add(User(username="e2", hashed_password="x", auth_source="ldap", external_issuer="ldap", external_id="g"))
        with pytest.raises(IntegrityError):
            await s.flush()

    asyncio.run(_with_session(dup))

    async def race(s):
        existing = User(username="racer", hashed_password="x", auth_source="oidc",
                        external_issuer="https://idp.example", external_id="sub-race")
        s.add(existing)
        await s.flush()
        real_find = prov.find_external_user
        calls = {"n": 0}

        async def find_once_none(session, source, issuer, subject):
            calls["n"] += 1
            return None if calls["n"] == 1 else await real_find(session, source, issuer, subject)

        monkeypatch.setattr(prov, "find_external_user", find_once_none)
        res = await prov.resolve_external_user(s, profile(subject="sub-race"), OPEN)
        return existing.id, res

    existing_id, res = asyncio.run(_with_session(race))
    assert res.user.id == existing_id and not res.created
