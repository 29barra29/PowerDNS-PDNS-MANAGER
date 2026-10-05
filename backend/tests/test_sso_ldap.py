"""Tests fuer services/sso_ldap.py (F10 9.1 Nr. 18-19; Plan S15 Thread-Pool-Grenze).

Verzeichnis: ldap3-Strategie ``MOCK_SYNC`` ueber ``_conn_factory``; Netz-/TLS-Fehler mit kleinen Fake-Verbindungen.
"""
from __future__ import annotations

import asyncio
import logging
import os
import ssl
import threading
import time
import uuid

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from ldap3 import MOCK_SYNC, NONE, Connection, Server  # noqa: E402
from ldap3.core import exceptions as lexc  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services import sso_ldap  # noqa: E402
from app.services.sso_ldap import LdapConfigError, LdapUnavailable  # noqa: E402
from app.services.sso_settings import LdapCfg  # noqa: E402

GUID = uuid.UUID("12345678-1234-5678-1234-567812345678")
USER_DN = "CN=John Doe,OU=Users,DC=example,DC=com"
SVC_DN = "CN=svc,OU=Service,DC=example,DC=com"
ADMINS = "CN=PDNS-Admins,OU=Groups,DC=example,DC=com"


def cfg(**kw) -> LdapCfg:
    base = dict(enabled=True, server_urls=("ldaps://dc1.example.com",), security="ldaps", tls_verify=True,
                bind_dn=SVC_DN, bind_password="svc-pw", user_base_dn="DC=example,DC=com", timeout=5)
    base.update(kw)
    return LdapCfg(**base)


class Directory:
    """MOCK_SYNC-Verzeichnis + Factory, die Aufrufe und Suchfilter protokolliert."""

    def __init__(self):
        self.server = Server("mock", get_info=NONE)
        self._setup = Connection(self.server, client_strategy=MOCK_SYNC)
        self.add(SVC_DN, {"objectClass": "user", "userPassword": "svc-pw", "sAMAccountName": "svc"})
        self.add(USER_DN, {
            "objectClass": ["top", "user"], "objectCategory": "person", "userPassword": "user-pw",
            "sAMAccountName": "jdoe", "mail": "jdoe@example.com", "displayName": "John Doe",
            "objectGUID": GUID.bytes_le, "memberOf": [ADMINS, "CN=Staff,OU=Groups,DC=example,DC=com"],
        })
        self.connections: list[dict] = []
        self.searches: list[str] = []
        self.binds: list[str | None] = []

    def add(self, dn, attrs):
        assert self._setup.strategy.add_entry(dn, attrs)

    def factory(self, server, **kwargs):
        directory = self
        self.connections.append({"server": server, **kwargs})

        class Rec(Connection):
            def search(self, base, flt, *a, **k):
                directory.searches.append(flt)
                return super().search(base, flt, *a, **k)

            def bind(self, *a, **k):
                directory.binds.append(kwargs.get("user"))
                return super().bind(*a, **k)

        return Rec(self.server, client_strategy=MOCK_SYNC, **kwargs)


@pytest.fixture
def directory():
    return Directory()


@pytest.fixture(autouse=True)
def _fresh_pool():
    sso_ldap.shutdown_for_tests()
    yield
    sso_ldap.shutdown_for_tests()


def run(coro):
    return asyncio.run(coro)


def auth(directory, username="jdoe", password="user-pw", **kw):
    return run(sso_ldap.authenticate(cfg(**kw), username, password, _conn_factory=directory.factory))


# ---------------------------------------------------------------------------------------------
# Nr. 18 authenticate
# ---------------------------------------------------------------------------------------------
def test_success_objectguid_and_memberof(directory):
    ident = auth(directory, username="  JDoe ")
    assert ident.dn == USER_DN
    p = ident.profile
    assert p.source == "ldap" and p.issuer == "ldap" and p.subject == str(GUID)
    assert p.username_hint == "jdoe" and p.email == "jdoe@example.com" and p.display_name == "John Doe"
    assert p.groups == [ADMINS, "CN=Staff,OU=Groups,DC=example,DC=com"] and p.email_verified is None
    assert p.dn == USER_DN
    assert directory.binds == [SVC_DN, USER_DN]          # Dienstkonto, dann Benutzer
    assert all(c["read_only"] and c["auto_referrals"] is False and c["raise_exceptions"] is False
               for c in directory.connections)


def test_wrong_password_returns_none(directory):
    assert auth(directory, password="falsch") is None


def test_empty_password_never_binds(directory):
    assert auth(directory, password="") is None
    assert auth(directory, password=None) is None
    assert auth(directory, username="   ") is None
    assert auth(directory, username="x" * 257) is None
    assert directory.connections == [] and directory.binds == []


def test_filter_injection_is_escaped(directory):
    assert auth(directory, username="*)(uid=*") is None
    assert "\\2a\\29\\28uid=\\2a" in directory.searches[0]
    assert directory.searches[0] == ("(&(objectCategory=person)(objectClass=user)"
                                     "(sAMAccountName=\\2a\\29\\28uid=\\2a))")


def test_two_matches_rejected(directory, caplog):
    directory.add("CN=John Doe 2,OU=Users,DC=example,DC=com", {
        "objectClass": "user", "objectCategory": "person", "userPassword": "user-pw", "sAMAccountName": "jdoe",
        "objectGUID": uuid.uuid4().bytes_le})
    with caplog.at_level(logging.WARNING, logger="app.services.sso_ldap"):
        assert auth(directory) is None
    assert "mehrere Treffer" in caplog.text
    assert directory.binds == [SVC_DN]                   # kein Benutzer-Bind


def test_unknown_user_returns_none(directory):
    assert auth(directory, username="nobody") is None


def test_missing_unique_id_attribute(directory):
    with pytest.raises(LdapConfigError) as ei:
        auth(directory, unique_id_attr="entryUUID")
    assert str(ei.value) == "Attribut entryUUID fehlt beim Benutzer"


def test_service_bind_failure_is_config_error(directory):
    with pytest.raises(LdapConfigError) as ei:
        auth(directory, bind_password="falsch")
    assert "Dienstkonto-Bind" in str(ei.value)


def test_anonymous_service_bind(directory):
    ident = auth(directory, bind_dn="", bind_password="")
    assert ident is not None
    assert directory.connections[0]["authentication"] == "ANONYMOUS" and directory.connections[0]["user"] is None


def test_unique_id_variants(directory):
    directory.add("CN=Uid User,OU=Users,DC=example,DC=com", {
        "objectClass": "user", "objectCategory": "person", "userPassword": "pw2", "sAMAccountName": "uuser",
        "entryUUID": "ABCDEF00-1111-2222-3333-444455556666", "binId": b"\xff\xfe\x01"})
    ident = auth(directory, username="uuser", password="pw2", unique_id_attr="entryUUID",
                 unique_id_attr_confirmed=True)
    assert ident.profile.subject == "abcdef00-1111-2222-3333-444455556666"
    ident = auth(directory, username="uuser", password="pw2", unique_id_attr="", unique_id_attr_confirmed=True)
    assert ident.profile.subject == "cn=uid user,ou=users,dc=example,dc=com"
    ident = auth(directory, username="uuser", password="pw2", unique_id_attr="binId", unique_id_attr_confirmed=True)
    assert ident.profile.subject == "fffe01"


def test_long_dn_is_hashed():
    long_dn = "CN=" + "x" * 300 + ",DC=example,DC=com"
    uid = sso_ldap._unique_id({}, long_dn, cfg(unique_id_attr=""))
    assert uid.startswith("dn-sha256:") and len(uid) == len("dn-sha256:") + 64


def test_group_search_escapes_user_dn(directory):
    odd_dn = "CN=Doe\\, John (IT),OU=Users,DC=example,DC=com"
    directory.add(odd_dn, {"objectClass": "user", "objectCategory": "person", "userPassword": "pw3",
                           "sAMAccountName": "jdoe2", "objectGUID": uuid.uuid4().bytes_le})
    directory.add("CN=DNS-Team,OU=Groups,DC=example,DC=com", {"objectClass": "group", "member": [odd_dn]})
    directory.add("CN=Other,OU=Groups,DC=example,DC=com", {"objectClass": "group", "member": [USER_DN]})
    ident = auth(directory, username="jdoe2", password="pw3", group_mode="search",
                 group_base_dn="OU=Groups,DC=example,DC=com")
    group_filter = directory.searches[-1]
    assert group_filter == "(&(objectClass=group)(member=CN=Doe\\5c, John \\28IT\\29,OU=Users,DC=example,DC=com))"
    assert ident.profile.groups == ["CN=DNS-Team,OU=Groups,DC=example,DC=com"]


def test_group_mode_none_and_username_placeholder(directory):
    ident = auth(directory, group_mode="none")
    assert ident.profile.groups is None
    directory.add("CN=posix,OU=Groups,DC=example,DC=com", {"objectClass": "posixGroup", "memberUid": "jdoe"})
    ident = auth(directory, group_mode="search", group_base_dn="OU=Groups,DC=example,DC=com",
                 group_filter="(&(objectClass=posixGroup)(memberUid={username}))")
    assert ident.profile.groups == ["CN=posix,OU=Groups,DC=example,DC=com"]


def test_missing_search_base_is_config_error(directory):
    with pytest.raises(LdapConfigError) as ei:
        auth(directory, user_base_dn="OU=Nirgends,DC=example,DC=com")
    assert "Benutzersuche" in str(ei.value)


class _DeadConn:
    """Verbindung, deren open() mit einer ldap3-Socket-Ausnahme scheitert."""

    def __init__(self, exc):
        self.exc = exc

    def open(self):
        raise self.exc

    def unbind(self):
        pass


def test_socket_error_is_unavailable():
    def factory(server, **kw):
        return _DeadConn(lexc.LDAPSocketOpenError("socket connection error"))

    with pytest.raises(LdapUnavailable) as ei:
        run(sso_ldap.authenticate(cfg(server_urls=("ldaps://a", "ldaps://b")), "jdoe", "pw", _conn_factory=factory))
    assert str(ei.value) == "LDAPSocketOpenError"


def test_failover_to_second_server(directory):
    tried = []

    def factory(server, **kw):
        tried.append(server.host)
        if server.host == "down.example.com":
            return _DeadConn(lexc.LDAPSocketOpenError("refused"))
        return directory.factory(server, **kw)

    ident = run(sso_ldap.authenticate(cfg(server_urls=("ldaps://down.example.com", "ldaps://dc2.example.com")),
                                      "jdoe", "user-pw", _conn_factory=factory))
    assert ident is not None
    assert tried[0] == "down.example.com" and set(tried[1:]) == {"dc2.example.com"}


def test_insecure_transport_refused_at_runtime(directory, monkeypatch):
    monkeypatch.setattr(settings, "SSO_ALLOW_INSECURE", False)
    with pytest.raises(LdapConfigError):
        auth(directory, tls_verify=False)
    with pytest.raises(LdapConfigError):
        auth(directory, security="none", server_urls=("ldap://dc1",))
    assert directory.connections == []
    monkeypatch.setattr(settings, "SSO_ALLOW_INSECURE", True)
    assert auth(directory, security="none", server_urls=("ldap://dc1",)) is not None


class _StartTlsConn:
    def __init__(self, inner, ok=True):
        self.inner, self.ok, self.started = inner, ok, False

    def open(self):
        return self.inner.open()

    def start_tls(self):
        self.started = True
        if not self.ok:
            raise lexc.LDAPStartTLSError("startTLS failed - protocolError")
        return True

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_starttls_called_and_failure_is_unavailable(directory):
    made = []

    def factory(server, **kw):
        c = _StartTlsConn(directory.factory(server, **kw))
        made.append(c)
        return c

    ident = run(sso_ldap.authenticate(cfg(security="starttls", server_urls=("ldap://dc1.example.com",)),
                                      "jdoe", "user-pw", _conn_factory=factory))
    assert ident is not None and made and all(c.started for c in made)

    def bad(server, **kw):
        return _StartTlsConn(directory.factory(server, **kw), ok=False)

    with pytest.raises(LdapUnavailable):
        run(sso_ldap.authenticate(cfg(security="starttls", server_urls=("ldap://dc1.example.com",)),
                                  "jdoe", "user-pw", _conn_factory=bad))


def test_referrals_are_ignored():
    class RefConn:
        def __init__(self, user):
            self.user = user
            self.result = {"result": 0, "description": "success"}
            self.response = []

        def open(self):
            pass

        def bind(self):
            return True

        def search(self, *a, **k):
            self.response = [{"type": "searchResRef", "uri": ["ldap://other/DC=x"]},
                             {"type": "searchResEntry", "dn": USER_DN,
                              "raw_attributes": {"objectGUID": [GUID.bytes_le], "sAMAccountName": [b"jdoe"]}}]
            return True

        def unbind(self):
            pass

    ident = run(sso_ldap.authenticate(cfg(group_mode="none"), "jdoe", "pw",
                                      _conn_factory=lambda server, **kw: RefConn(kw.get("user"))))
    assert ident is not None and ident.dn == USER_DN


# ---------------------------------------------------------------------------------------------
# Nr. 19 TLS
# ---------------------------------------------------------------------------------------------
def test_tls_for():
    tls = sso_ldap._tls_for("ldaps://dc1.example.com:636", cfg(ca_cert="-----BEGIN CERTIFICATE-----x"))
    assert tls.validate == ssl.CERT_REQUIRED
    assert tls.ca_certs_data == "-----BEGIN CERTIFICATE-----x"
    assert tls.sni == "dc1.example.com" and tls.version is None
    tls = sso_ldap._tls_for("ldap://[2001:db8::1]:389", cfg(tls_verify=False))
    assert tls.validate == ssl.CERT_NONE and tls.ca_certs_data is None and tls.sni == "2001:db8::1"
    assert sso_ldap._tls_for("ldap://dc1", cfg(security="none")) is None
    srv = sso_ldap._server_for("ldaps://dc1.example.com", cfg(timeout=7))
    assert srv.ssl is True and srv.connect_timeout == 7 and srv.tls.validate == ssl.CERT_REQUIRED


# ---------------------------------------------------------------------------------------------
# S15 Thread-Pool: 4 Threads, Slot bis Thread-Ende, Warteschlange > 8 -> sofort 503
# ---------------------------------------------------------------------------------------------
def test_pool_saturation_rejects_immediately():
    release = threading.Event()
    started = threading.Semaphore(0)

    def blocker():
        started.release()
        release.wait(10)
        return "ok"

    async def scenario():
        running = [asyncio.create_task(sso_ldap.run_limited(blocker, timeout=10)) for _ in range(4)]
        for _ in range(4):
            while not started.acquire(blocking=False):
                await asyncio.sleep(0.01)
        waiting = [asyncio.create_task(sso_ldap.run_limited(lambda: "w", timeout=10)) for _ in range(8)]
        await asyncio.sleep(0.05)
        state = sso_ldap.pool_state()
        t0 = time.monotonic()
        with pytest.raises(LdapUnavailable) as ei:
            await sso_ldap.run_limited(lambda: "x", timeout=10)
        elapsed = time.monotonic() - t0
        release.set()
        results = await asyncio.gather(*running, *waiting)
        return state, str(ei.value), elapsed, results

    state, msg, elapsed, results = run(scenario())
    assert state == {"running": 4, "waiting": 8}
    assert msg == "Warteschlange voll" and elapsed < 0.5
    assert results == ["ok"] * 4 + ["w"] * 8


def test_timeout_keeps_slot_until_thread_ends():
    release = threading.Event()

    def slow():
        release.wait(5)
        return "done"

    async def scenario():
        with pytest.raises(LdapUnavailable) as ei:
            await sso_ldap.run_limited(slow, timeout=0.2)
        held = sso_ldap.pool_state()["running"]     # Thread laeuft noch -> Slot bleibt belegt
        release.set()
        for _ in range(100):
            if sso_ldap.pool_state()["running"] == 0:
                break
            await asyncio.sleep(0.02)
        return str(ei.value), held, sso_ldap.pool_state()["running"]

    msg, held, after = run(scenario())
    assert msg == "Zeitueberschreitung" and held == 1 and after == 0


def test_authenticate_uses_overall_timeout(directory, monkeypatch):
    seen = {}

    async def fake_run_limited(fn, *args, timeout):
        seen["timeout"] = timeout
        return None

    monkeypatch.setattr(sso_ldap, "run_limited", fake_run_limited)
    assert auth(directory, timeout=8) is None
    assert seen["timeout"] == 8 * 4 + 2


# ---------------------------------------------------------------------------------------------
# Konfigurationstest (POST /settings/sso/test, F10 3.3.3)
# ---------------------------------------------------------------------------------------------
def test_connection_without_user(directory):
    out = run(sso_ldap.test_connection(cfg(), _conn_factory=directory.factory))
    assert out["success"] is True and out["details"]["service_bind"] == "ok"
    assert out["details"]["server"] == "ldaps://dc1.example.com" and out["details"]["user"] is None


def test_connection_with_user_groups_and_password(directory):
    c = cfg(allowed_groups=(ADMINS.lower(),), admin_groups=("CN=Staff,OU=Groups,DC=example,DC=com",))
    out = run(sso_ldap.test_connection(c, "jdoe", "user-pw", _conn_factory=directory.factory))
    assert out["success"] is True
    u = out["details"]["user"]
    assert u["dn"] == USER_DN and u["unique_id"] == str(GUID) and u["groups_total"] == 2
    assert u["is_allowed"] is True and u["is_admin"] is True
    assert u["password_checked"] is True and u["password_ok"] is True
    out = run(sso_ldap.test_connection(c, "jdoe", "falsch", _conn_factory=directory.factory))
    assert out["success"] is False and out["error"] == "Passwort des Testbenutzers falsch"
    assert out["details"]["user"]["password_ok"] is False and out["details"]["user"]["dn"] == USER_DN


def test_connection_error_texts(directory):
    out = run(sso_ldap.test_connection(cfg(), "nobody", _conn_factory=directory.factory))
    assert out == {**out, "success": False, "error": "Benutzer nicht gefunden"}
    out = run(sso_ldap.test_connection(cfg(bind_password="falsch"), _conn_factory=directory.factory))
    assert out["error"].startswith("Bind des Dienstkontos fehlgeschlagen (")
    out = run(sso_ldap.test_connection(cfg(), "jdoe", _conn_factory=lambda s, **k: _DeadConn(
        lexc.LDAPSocketOpenError("socket connection error"))))
    assert out["error"].startswith("Verbindung zu ldaps://dc1.example.com fehlgeschlagen: LDAPSocketOpenError")
    out = run(sso_ldap.test_connection(cfg(), "jdoe", _conn_factory=lambda s, **k: _DeadConn(
        lexc.LDAPSocketOpenError("socket ssl wrapping error: certificate verify failed"))))
    assert out["error"].startswith("TLS-Fehler: ")
    out = run(sso_ldap.test_connection(cfg(unique_id_attr="mail", unique_id_attr_confirmed=True), "jdoe",
                                       _conn_factory=directory.factory))
    assert out["success"] is True and any("mail ist änderbar" in w for w in out["warnings"])
    out = run(sso_ldap.test_connection(cfg(unique_id_attr="entryUUID"), "jdoe", _conn_factory=directory.factory))
    assert out["error"] == "Attribut entryUUID fehlt beim Benutzer"


def test_connection_insecure_text(directory, monkeypatch):
    monkeypatch.setattr(settings, "SSO_ALLOW_INSECURE", False)
    out = run(sso_ldap.test_connection(cfg(tls_verify=False), _conn_factory=directory.factory))
    assert out["error"] == ("Unverschlüsselte LDAP-Verbindungen bzw. das Abschalten der Zertifikatsprüfung "
                            "erfordern SSO_ALLOW_INSECURE=true")
