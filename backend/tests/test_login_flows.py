"""Login-Flows mit SSO (F10 9.1 Nr. 17, 20, 21, 23; Plan S2, S4, S8): Passwort/LDAP-Login mit Login-Zaehlern,
2FA-Zwischenschritt per Cookie, Sperren fuer externe Konten, Umwandlung in ein lokales Konto, Step-up.

Laeuft gegen SQLite hinter einem AsyncSession-Adapter (``f10fakes``); LDAP und OIDC sind gemockt (der LDAP-Mock
zaehlt die Bind-Versuche). Fehler-Audits (``write_audit_detached``) werden eingesammelt.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import pyotp
import pytest
from fastapi import HTTPException
from sqlalchemy import text

from f10fakes import F10Env, session_headers
from app.core import auth as core_auth
from app.core import login_rate_limit as lrl
from app.core import secrets as secret_store
from app.core.auth import StepUpBody, create_password_reset_token, create_two_factor_pending_token, verify_step_up
from app.core.config import settings
from app.models.models import WebAuthnCredential
from app.routers import auth as auth_router
from app.routers import settings_sso
from app.routers import sso as sso_router
from app.services import sso_ldap, sso_oidc
from app.services.sso_ldap import LdapIdentity
from app.services.sso_provisioning import ExternalProfile

PW = "Passwort-123"
LDAP_PW = "Verzeichnis-Passwort-1"
TOTP_SECRET = "JBSWY3DPEHPK3PXP"
ISS = "https://idp.example.com/realms/firma"
LDAP_ON = {"ldap_enabled": True, "ldap_server_urls": '["ldaps://dc1.example.com"]',
           "ldap_user_base_dn": "DC=example,DC=com", "ldap_jit_enabled": True, "ldap_jit_allow_any_account": True}


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_state():
    lrl.reset_for_tests()
    sso_oidc.reset_for_tests()
    sso_router.reset_for_tests()
    core_auth._TOTP_USED.clear()
    secret_store.configure_for_tests()
    yield
    lrl.reset_for_tests()
    sso_oidc.reset_for_tests()
    sso_router.reset_for_tests()
    core_auth._TOTP_USED.clear()


@pytest.fixture
def detached(monkeypatch):
    """Fehler-Audits laufen in einer eigenen Session – hier eingesammelt als (action, kwargs)."""
    from app.services import audit

    calls = []

    async def _fake(action, *a, **kw):
        calls.append((action, kw))
        return None

    monkeypatch.setattr(audit, "write_audit_detached", _fake)
    monkeypatch.setattr(sso_router, "write_audit_detached", _fake)
    return calls


@pytest.fixture
def env(detached):
    e = F10Env(auth_router, sso_router, settings_sso)
    yield e
    e.close()


class FakeLdap:
    """Ersetzt ``sso_ldap.authenticate``; zaehlt jeden Bind-Versuch."""

    def __init__(self):
        self.binds = 0
        self.identities: dict[tuple[str, str], LdapIdentity] = {}
        self.error: Exception | None = None

    def add(self, username: str, password: str, subject: str, *, groups=None, display_name=None):
        profile = ExternalProfile(source="ldap", issuer="ldap", subject=subject, username_hint=username,
                                  email=None, email_verified=None, display_name=display_name, groups=groups)
        self.identities[(username, password)] = LdapIdentity(dn=f"CN={username},DC=example,DC=com", profile=profile)

    async def authenticate(self, cfg, username, password, **_kw):
        self.binds += 1
        if self.error is not None:
            raise self.error
        return self.identities.get((username, password))


@pytest.fixture
def ldap(monkeypatch):
    fake = FakeLdap()
    monkeypatch.setattr(sso_ldap, "authenticate", fake.authenticate)
    return fake


def login(client, username, password, **form):
    return client.post("/api/v1/auth/login", data={"username": username, "password": password, **form})


def reasons(detached, action="LOGIN_FAILED"):
    return [kw["details"].get("reason") for a, kw in detached if a == action]


def ip_count(ip):
    return lrl._ip_limiter.count(lrl._ip_counter_key(ip))


def user_count(name):
    return lrl._user_limiter.count(lrl.normalize_username(name))


# ---------------------------------------------------------------------------------------------
# Nr. 20 Login-Handler: lokal, LDAP, Dummy-Hash, Ausfall, lokale Anmeldung aus
# ---------------------------------------------------------------------------------------------
def test_local_login_ok(env):
    env.add_user("bob")
    r = login(env.client(), "bob", PW)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["auth_source"] == "local"
    (audit,) = env.audits("LOGIN")
    assert audit.details["method"] == "password"


def test_local_wrong_then_ldap_ok_uses_ldap_account(env, ldap):
    env.set_settings(**LDAP_ON)
    local = env.add_user("jdoe")
    ldap.add("jdoe", LDAP_PW, "guid-jdoe", display_name="John Doe")
    r = login(env.client(), "jdoe", LDAP_PW)
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["auth_source"] == "ldap" and user["username"] == "jdoe-2" and user["id"] != local.id
    (audit,) = env.audits("LOGIN")
    assert audit.details["method"] == "ldap" and audit.details["jit"] is True
    assert ldap.binds == 1
    assert env.reload(local).auth_source == "local"   # das lokale Konto bleibt unberuehrt


def test_unknown_user_ldap_off_checks_dummy_hash(env, monkeypatch, detached):
    seen = []
    real = auth_router.verify_password

    def spy(pw, hashed):
        seen.append(hashed)
        return real(pw, hashed)

    monkeypatch.setattr(auth_router, "verify_password", spy)
    r = login(env.client(), "niemand", "egal-123")
    assert r.status_code == 401 and r.json()["detail"] == "Falscher Benutzername oder Passwort"
    assert seen == [auth_router._dummy_hash()]
    assert reasons(detached) == ["bad_credentials"]


def test_ldap_unavailable_503_counts_as_failure(env, ldap, detached):
    """Review Welle 2: ein Ausfall zaehlt wie ein Fehlversuch (IP und Name) – sonst unbegrenztes Raten/Binden."""
    env.set_settings(**LDAP_ON)
    ldap.error = sso_ldap.LdapUnavailable("Timeout")
    r = login(env.client(ip="198.51.100.20"), "alice", LDAP_PW)
    assert r.status_code == 503
    assert r.json()["detail"] == "Der Anmeldedienst (LDAP) ist nicht erreichbar. Bitte später erneut versuchen."
    assert reasons(detached) == ["ldap_unavailable"]
    assert ip_count("198.51.100.20") == 1 and user_count("alice") == 1


@pytest.mark.parametrize("error", [sso_ldap.LdapUnavailable("Timeout"), sso_ldap.LdapConfigError("Dienstkonto")],
                         ids=["unavailable", "config"])
def test_local_password_guessing_with_broken_ldap_is_limited(env, ldap, detached, error):
    """Review Welle 2 [S2]: LDAP aktiv, aber gestoert – falsches lokales Passwort zaehlt; der 6. Versuch ist 429 ohne
    weiteren Bind, auch mit dem richtigen Passwort. Seit WS-W3-NACHARBEIT (L-3) dieselbe 503 wie fuer unbekannte
    Namen (keine Aufzaehlung lokaler Konten waehrend der Stoerung)."""
    env.set_settings(**LDAP_ON)
    env.add_user("root", role="admin")
    ldap.error = error
    c = env.client(ip="198.51.100.21")
    unknown = login(env.client(ip="198.51.100.29"), "gibt-es-nicht", "falsch-123")
    for _ in range(lrl.USER_MAX_FAILS):
        r = login(c, "root", "falsch-123")
        assert r.status_code == 503 and r.json() == unknown.json()
    assert ldap.binds == lrl.USER_MAX_FAILS + 1
    assert ip_count("198.51.100.21") == lrl.USER_MAX_FAILS and user_count("root") == lrl.USER_MAX_FAILS
    assert login(c, "root", "falsch-123").status_code == 429
    r = login(env.client(ip="203.0.113.21"), "root", PW)     # richtiges Passwort, andere IP: weiter gesperrt
    assert r.status_code == 429
    assert ldap.binds == lrl.USER_MAX_FAILS + 1
    reason = "ldap_unavailable" if isinstance(error, sso_ldap.LdapUnavailable) else "ldap_config"
    local = [kw["details"] for a, kw in detached if a == "LOGIN_FAILED" and kw.get("user_id")]
    assert len(local) == lrl.USER_MAX_FAILS
    assert all(d["reason"] == "bad_credentials" and d["ldap_error"] == reason for d in local)


def test_ldap_on_unknown_and_external_names_check_dummy_hash(env, ldap, monkeypatch):
    """L-3 (WS-W3-NACHARBEIT): auch bei aktivem LDAP pruefen unbekannte/externe Namen einen Hash – die Laufzeit
    verraet nicht, ob es ein lokales Konto gibt."""
    env.set_settings(**LDAP_ON)
    env.add_user("ext", auth_source="ldap", ext_id="guid-ext")
    seen = []
    real = auth_router.verify_password

    def spy(pw, hashed):
        seen.append(hashed)
        return real(pw, hashed)

    monkeypatch.setattr(auth_router, "verify_password", spy)
    assert login(env.client(ip="198.51.100.31"), "niemand", "egal-123").status_code == 401
    assert login(env.client(ip="198.51.100.32"), "ext", "egal-123").status_code == 401
    assert seen == [auth_router._dummy_hash(), auth_router._dummy_hash()]


def test_ldap_outage_ip_limit_stops_binds(env, ldap):
    """Review Welle 2: unbekannte Namen waehrend eines Ausfalls – das IP-Fenster greift, danach kein Bind mehr."""
    env.set_settings(**LDAP_ON)
    ldap.error = sso_ldap.LdapUnavailable("Warteschlange voll")
    c = env.client(ip="198.51.100.22")
    for i in range(lrl.IP_MAX_FAILS):
        assert login(c, f"name-{i}", "egal-123").status_code == 503
    assert login(c, "noch-einer", "egal-123").status_code == 429
    assert ldap.binds == lrl.IP_MAX_FAILS


def test_correct_local_password_with_broken_ldap_logs_in(env, ldap):
    env.set_settings(**LDAP_ON)
    env.add_user("bob")
    ldap.error = sso_ldap.LdapConfigError("Dienstkonto")
    assert login(env.client(), "bob", PW).status_code == 200
    assert ldap.binds == 0


def test_ldap_config_error_503(env, ldap, detached):
    env.set_settings(**LDAP_ON)
    ldap.error = sso_ldap.LdapConfigError("Dienstkonto")
    r = login(env.client(ip="198.51.100.23"), "alice", LDAP_PW)
    assert r.status_code == 503 and "fehlerhaft konfiguriert" in r.json()["detail"]
    assert reasons(detached) == ["ldap_config"]
    # kann nach erfolgreichem Benutzer-Bind kommen (ID-Attribut fehlt): zaehlt, sonst Passwort-Orakel
    assert ip_count("198.51.100.23") == 1 and user_count("alice") == 1


def test_local_login_disabled_blocks_non_admin_but_not_admin(env, detached):
    env.set_settings(sso_local_login_enabled=False, ldap_enabled=True)
    env.add_user("bob")
    env.add_user("root", role="admin")
    r = login(env.client(), "bob", PW)
    assert r.status_code == 403
    assert r.json()["detail"] == "Die Anmeldung mit lokalem Konto ist deaktiviert – bitte über SSO anmelden."
    assert reasons(detached) == ["local_login_disabled"]
    assert login(env.client(), "root", PW).status_code == 200   # Notfallzugang


def test_external_oidc_account_with_password_ldap_off_401(env, detached):
    env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="sub-1")
    r = login(env.client(), "ext", PW)   # das Zufallspasswort ist unbekannt – auch ein "richtiges" zaehlt nicht
    assert r.status_code == 401
    assert reasons(detached) == ["bad_credentials"]


def test_ldap_not_allowed_403(env, ldap, detached):
    env.set_settings(**{**LDAP_ON, "ldap_allowed_groups": '["CN=pdns,OU=Groups,DC=example,DC=com"]',
                        "ldap_jit_allow_any_account": False})
    ldap.add("carol", LDAP_PW, "guid-carol", groups=["CN=other,OU=Groups,DC=example,DC=com"])
    r = login(env.client(), "carol", LDAP_PW)
    assert r.status_code == 403 and r.json()["detail"] == "Dein Konto ist nicht für dieses Panel freigegeben."
    assert reasons(detached) == ["not_allowed"]


def test_ldap_without_jit_no_account_403(env, ldap, detached):
    env.set_settings(**{**LDAP_ON, "ldap_jit_enabled": False})
    ldap.add("dave", LDAP_PW, "guid-dave")
    r = login(env.client(), "dave", LDAP_PW)
    assert r.status_code == 403 and "kein Zugang eingerichtet" in r.json()["detail"]
    assert reasons(detached) == ["no_account"]


def test_ldap_login_with_totp_two_steps(env, ldap):
    env.set_settings(**LDAP_ON)
    ext = env.add_user("erin", auth_source="ldap", issuer="ldap", ext_id="guid-erin", totp_enabled=True,
                       totp_secret=TOTP_SECRET)
    ldap.add("erin", LDAP_PW, "guid-erin")
    c = env.client()
    r = login(c, "erin", LDAP_PW)
    assert r.status_code == 200 and r.json()["need_two_factor"] is True
    payload = core_auth.decode_two_factor_pending_payload(r.json()["two_factor_token"])
    assert payload["sub"] == ext.id and payload["m"] == "ldap"
    r2 = c.post("/api/v1/auth/login/2fa", json={"two_factor_token": r.json()["two_factor_token"],
                                                "totp_code": pyotp.TOTP(TOTP_SECRET).now()})
    assert r2.status_code == 200, r2.text
    (audit,) = env.audits("LOGIN")
    assert audit.details["method"] == "ldap+totp"


# ---------------------------------------------------------------------------------------------
# Login-Zaehler [S2]
# ---------------------------------------------------------------------------------------------
def test_sixth_failure_other_ip_429_without_ldap_bind(env, ldap):
    env.set_settings(**LDAP_ON)
    a = env.client(ip="198.51.100.1")
    for _ in range(lrl.USER_MAX_FAILS):
        assert login(a, "alice", "falsch-123").status_code == 401
    assert ldap.binds == lrl.USER_MAX_FAILS
    r = login(env.client(ip="203.0.113.9"), "Alice ", "falsch-123")   # andere IP, andere Schreibweise
    assert r.status_code == 429 and r.json()["detail"] == auth_router.RATE_LIMIT_DETAIL
    assert ldap.binds == lrl.USER_MAX_FAILS   # kein weiterer Bind (kein AD-Lockout ueber das Panel)


def test_success_does_not_reset_ip_counter(env, ldap):
    env.set_settings(**LDAP_ON)
    ldap.add("jdoe", LDAP_PW, "guid-jdoe")
    c = env.client(ip="198.51.100.2")
    for name in ("mallory", "trudy", "jdoe"):
        assert login(c, name, "falsch-123").status_code == 401
    assert ip_count("198.51.100.2") == 3
    assert login(c, "jdoe", LDAP_PW).status_code == 200
    assert ip_count("198.51.100.2") == 3          # IP-Zaehler bleibt
    assert user_count("jdoe") == 0                 # eingegebener Name: Zaehler geloescht
    assert user_count("mallory") == 1


def test_local_failure_counts_pair(env):
    env.add_user("bob")
    c = env.client(ip="198.51.100.3")
    assert login(c, "bob", "falsch-123").status_code == 401
    assert ip_count("198.51.100.3") == 1 and user_count("bob") == 1
    assert login(c, "bob", PW).status_code == 200
    assert ip_count("198.51.100.3") == 1 and user_count("bob") == 0


# ---------------------------------------------------------------------------------------------
# Nr. 17 2FA-Schritt mit Cookie (OIDC) und ohne Token
# ---------------------------------------------------------------------------------------------
def test_two_factor_with_cookie_oidc(env):
    user = env.add_user("olga", auth_source="oidc", issuer=ISS, ext_id="sub-olga", totp_enabled=True,
                        totp_secret=TOTP_SECRET)
    lrl.record_failed_login("198.51.100.4", "olga")
    c = env.client(ip="198.51.100.4")
    cookie = {"Cookie": "pdnsmgr_2fa=" + create_two_factor_pending_token(user.id, method="oidc")}
    r = c.post("/api/v1/auth/login/2fa", headers=cookie, json={"totp_code": pyotp.TOTP(TOTP_SECRET).now()})
    assert r.status_code == 200, r.text
    (audit,) = env.audits("LOGIN")
    assert audit.details["method"] == "oidc+totp"
    cookies = r.headers.get_list("set-cookie")
    assert any(h.startswith(f"{settings.AUTH_COOKIE_NAME}=") for h in cookies)
    gone = [h.lower() for h in cookies if h.startswith("pdnsmgr_2fa=")]
    assert gone and "max-age=0" in gone[0] and "path=/api/v1/auth/login/2fa" in gone[0]
    assert user_count("olga") == 1   # OIDC loescht keine Zaehler (clear_fails=False)


def test_two_factor_without_token_and_cookie_401(env):
    r = env.client().post("/api/v1/auth/login/2fa", json={"two_factor_token": "", "totp_code": "123456"})
    assert r.status_code == 401 and r.json()["detail"] == "Ungültiger oder abgelaufener Zweitschritt-Token"


def test_two_factor_password_method_respects_local_login_off(env, detached):
    env.set_settings(sso_local_login_enabled=False, oidc_enabled=True)
    user = env.add_user("bob", totp_enabled=True, totp_secret=TOTP_SECRET)
    r = env.client().post("/api/v1/auth/login/2fa", json={
        "two_factor_token": create_two_factor_pending_token(user.id), "totp_code": pyotp.TOTP(TOTP_SECRET).now()})
    assert r.status_code == 403
    assert reasons(detached) == ["local_login_disabled"]


# ---------------------------------------------------------------------------------------------
# Nr. 21 Externe Konten
# ---------------------------------------------------------------------------------------------
def test_totp_disable_external_without_password_local_needs_it(env):
    ext = env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="s1", totp_enabled=True, totp_secret=TOTP_SECRET)
    local = env.add_user("loc", totp_enabled=True, totp_secret=TOTP_SECRET)
    c = env.client()
    r = c.post("/api/v1/auth/me/totp/disable", headers=session_headers(ext),
               json={"code": pyotp.TOTP(TOTP_SECRET).now()})
    assert r.status_code == 200, r.text
    assert env.reload(ext).totp_enabled is False
    r = c.post("/api/v1/auth/me/totp/disable", headers=session_headers(local), json={"code": "123456"})
    assert r.status_code == 400 and r.json()["detail"] == "Passwort ist falsch"


def test_webauthn_register_external_400(env):
    ext = env.add_user("ext", auth_source="ldap", issuer="ldap", ext_id="g1")
    c = env.client()
    r = c.post("/api/v1/auth/me/webauthn/register/begin", headers=session_headers(ext))
    assert r.status_code == 400 and r.json()["detail"] == auth_router.EXTERNAL_PASSKEY_REGISTER_DETAIL
    r = c.post("/api/v1/auth/me/webauthn/register/complete", headers=session_headers(ext),
               json={"name": "x", "challenge_token": "x" * 30, "credential": {}})
    assert r.status_code == 400 and r.json()["detail"] == auth_router.EXTERNAL_PASSKEY_REGISTER_DETAIL


def _passkey_login(env, user, monkeypatch):
    from app.services import webauthn_service as wa

    env.session.add(WebAuthnCredential(user_id=user.id, name="key", credential_id="cred-1", public_key="pk",
                                       sign_count=0))
    env.session.commit()
    called = []
    monkeypatch.setattr(wa, "verify_authentication", lambda *a, **k: called.append(1) or 1)
    # eindeutige Challenge je Test (Einmal-Verbrauch ist prozessweit)
    token = core_auth.create_webauthn_challenge_token(f"chal-{uuid.uuid4().hex}",
                                                      purpose=core_auth.TOKEN_TYPE_WEBAUTHN_AUTH)
    r = env.client().post("/api/v1/auth/webauthn/login/complete",
                          json={"challenge_token": token, "credential": {"id": "cred-1"}})
    return r, called


def test_webauthn_login_external_403(env, monkeypatch, detached):
    ext = env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="s1")
    r, called = _passkey_login(env, ext, monkeypatch)
    assert r.status_code == 403 and r.json()["detail"] == auth_router.EXTERNAL_PASSKEY_LOGIN_DETAIL
    assert called == []   # vor der Signaturpruefung abgelehnt
    assert reasons(detached) == ["external_account"]


def test_webauthn_login_local_disabled_non_admin_403(env, monkeypatch, detached):
    env.set_settings(sso_local_login_enabled=False, oidc_enabled=True)
    user = env.add_user("bob")
    r, called = _passkey_login(env, user, monkeypatch)
    assert r.status_code == 403 and called == []
    assert reasons(detached) == ["local_login_disabled"]


def test_update_profile_external_fields(env):
    ext = env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="s1", email="ext@example.com")
    c, h = env.client(), session_headers(ext)
    r = c.put("/api/v1/auth/me", headers=h, json={"username": "neu"})
    assert r.status_code == 400 and r.json()["detail"] == auth_router.EXTERNAL_USERNAME_DETAIL
    r = c.put("/api/v1/auth/me", headers=h, json={"email": "other@example.com"})
    assert r.status_code == 400 and r.json()["detail"] == auth_router.EXTERNAL_EMAIL_SELF_DETAIL
    r = c.put("/api/v1/auth/me", headers=h, json={"display_name": "Neuer Name", "email": "ext@example.com",
                                                  "username": "ext"})
    assert r.status_code == 200 and r.json()["user"]["display_name"] == "Neuer Name"


def test_update_user_external_email_400(env):
    admin = env.add_user("root", role="admin")
    ext = env.add_user("ext", auth_source="ldap", issuer="ldap", ext_id="g1", email="a@example.com")
    c = env.client()
    r = c.put(f"/api/v1/auth/users/{ext.id}", headers=session_headers(admin), json={"email": "b@example.com"})
    assert r.status_code == 400 and r.json()["detail"] == auth_router.EXTERNAL_EMAIL_ADMIN_DETAIL
    r = c.put(f"/api/v1/auth/users/{ext.id}", headers=session_headers(admin), json={"display_name": "X"})
    assert r.status_code == 200


def test_forgot_password_external_and_local_off(env, monkeypatch):
    sent = []

    async def fake_send(db, user, **kw):
        sent.append(user.username)

    monkeypatch.setattr(auth_router, "send_password_reset_mail", fake_send)
    env.set_settings(forgot_password_enabled=True)
    env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="s1", email="ext@example.com")
    env.add_user("bob", email="bob@example.com")
    env.add_user("root", role="admin", email="root@example.com")
    c = env.client()
    for name in ("ext", "bob", "root"):
        r = c.post("/api/v1/auth/forgot-password", json={"username": name})
        assert r.status_code == 200 and r.json() == auth_router.FORGOT_PASSWORD_REPLY
    assert sent == ["bob", "root"]
    env.set_settings(sso_local_login_enabled=False, oidc_enabled=True)
    for name in ("bob", "root"):
        assert c.post("/api/v1/auth/forgot-password", json={"username": name}).status_code == 200
    assert sent == ["bob", "root", "root"]   # lokal aus: nur noch Admins (Notfallzugang)


def test_reset_password_external_400(env):
    ext = env.add_user("ext", auth_source="ldap", issuer="ldap", ext_id="g1")
    token = create_password_reset_token(ext.id, ext.hashed_password)
    r = env.client().post("/api/v1/auth/reset-password", json={"token": token, "new_password": "Neu-Passwort-1"})
    assert r.status_code == 400 and r.json()["detail"] == "Ungültiger oder abgelaufener Link."


def test_register_blocked_when_local_login_off(env):
    env.set_settings(registration_enabled=True)
    body = {"username": "newbie", "password": "Passwort-123"}
    c = env.client()
    assert c.post("/api/v1/auth/register", json=body).status_code == 201
    env.set_settings(sso_local_login_enabled=False, oidc_enabled=True)
    r = c.post("/api/v1/auth/register", json={**body, "username": "newbie2"})
    assert r.status_code == 403 and r.json()["detail"] == "Registrierung ist deaktiviert"


def test_password_change_external_400(env):
    ext = env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="s1")
    r = env.client().put("/api/v1/auth/me/password", headers=session_headers(ext),
                         json={"current_password": PW, "new_password": "Neu-Passwort-1"})
    assert r.status_code == 400 and "Identitätsanbieter" in r.json()["detail"]


# ---------------------------------------------------------------------------------------------
# Nr. 23 convert-to-local (+ Step-up [S8])
# ---------------------------------------------------------------------------------------------
def test_convert_to_local_checks_and_success(env):
    admin = env.add_user("root", role="admin")
    ext = env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="sub-x", is_active=True)
    local = env.add_user("bob")
    c, h = env.client(), session_headers(admin)
    url = "/api/v1/auth/users/{}/convert-to-local"
    assert c.post(url.format(9999), headers=h, json={}).status_code == 404
    r = c.post(url.format(admin.id), headers=h, json={})
    assert r.status_code == 400 and r.json()["detail"] == "Das eigene Konto kann nicht umgewandelt werden"
    r = c.post(url.format(local.id), headers=h, json={})
    assert r.status_code == 400 and r.json()["detail"] == "Das Konto ist bereits ein lokales Konto"
    # ohne Step-up: 403 stepup_required, nichts geaendert
    r = c.post(url.format(ext.id), headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "stepup_required"
    assert r.headers.get("x-step-up-required") == "stepup_required"
    assert env.reload(ext).auth_source == "oidc"
    r = c.post(url.format(ext.id), headers=h, json={"step_up": {"current_password": "falsch-123"}})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "stepup_failed"
    r = c.post(url.format(ext.id), headers=h, json={"step_up": {"current_password": PW}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["new_password"]) == 16 and body["must_change_password"] is True
    ext = env.reload(ext)
    assert (ext.auth_source, ext.external_issuer, ext.external_id) == ("local", None, None)
    assert ext.must_change_password is True
    assert core_auth.verify_password(body["new_password"], ext.hashed_password)
    (audit,) = env.audits("USER_CONVERT_LOCAL")
    assert audit.user_id == admin.id
    assert audit.details == {"target_user_id": ext.id, "previous_source": "oidc", "previous_issuer": ISS,
                             "must_change_password": True, "step_up": "password"}
    assert body["new_password"] not in str(audit.details)


def test_convert_to_local_optional_must_change(env):
    admin = env.add_user("root", role="admin")
    ext = env.add_user("ext", auth_source="ldap", issuer="ldap", ext_id="g")
    r = env.client().post(f"/api/v1/auth/users/{ext.id}/convert-to-local", headers=session_headers(admin),
                          json={"must_change_password": False, "step_up": {"current_password": PW}})
    assert r.status_code == 200 and r.json()["must_change_password"] is False


# ---------------------------------------------------------------------------------------------
# verify_step_up (B.3 [S8])
# ---------------------------------------------------------------------------------------------
def _request(via="session", auth_time=None, ip="198.51.100.50"):
    return SimpleNamespace(state=SimpleNamespace(auth_via=via, auth_time=auth_time),
                           headers={}, client=SimpleNamespace(host=ip))


def _user(**kw):
    base = dict(id=7, username="admin7", auth_source="local", totp_enabled=False, totp_secret=None,
                hashed_password=core_auth.hash_password(PW))
    base.update(kw)
    return SimpleNamespace(**base)


async def _step_up_code(user, req, body):
    try:
        await verify_step_up(None, user, req, body)
    except HTTPException as exc:
        return exc.status_code, (exc.detail or {}).get("code") if isinstance(exc.detail, dict) else exc.detail
    return 200, None


async def test_step_up_local_password_and_totp():
    u = _user()
    assert await _step_up_code(u, _request(), None) == (403, "stepup_required")
    assert await _step_up_code(u, _request(), StepUpBody(current_password="falsch-1")) == (403, "stepup_failed")
    assert user_count("admin7") == 1 and ip_count("198.51.100.50") == 1
    assert await verify_step_up(None, u, _request(), StepUpBody(current_password=PW)) == "password"
    assert user_count("admin7") == 0          # Erfolg loescht nur das Paar
    assert ip_count("198.51.100.50") == 1

    t = _user(totp_enabled=True, totp_secret=TOTP_SECRET)
    assert await _step_up_code(t, _request(), StepUpBody(current_password=PW)) == (403, "stepup_required")
    assert await _step_up_code(t, _request(), StepUpBody(current_password=PW, totp_code="000000")) == \
        (403, "stepup_failed")
    ok = StepUpBody(current_password=PW, totp_code=pyotp.TOTP(TOTP_SECRET).now())
    assert await verify_step_up(None, t, _request(), ok) == "password"


async def test_step_up_totp_unreadable_and_rate_limit():
    u = _user(totp_enabled=True, totp_secret=secret_store.UNREADABLE)
    code = await _step_up_code(u, _request(), StepUpBody(current_password=PW, totp_code="123456"))
    assert code == (403, "stepup_failed")
    for _ in range(lrl.USER_MAX_FAILS):
        lrl.record_failed_login("203.0.113.77", "admin7")
    assert await _step_up_code(_user(), _request(), StepUpBody(current_password=PW)) == \
        (429, core_auth.STEP_UP_RATE_LIMIT_DETAIL)


async def test_step_up_external_fresh_session_only():
    ext = _user(auth_source="oidc")
    assert await verify_step_up(None, ext, _request(auth_time=time.time() - 60), None) == "fresh_session"
    assert await _step_up_code(ext, _request(auth_time=time.time() - 601), None) == (403, "reauth_required")
    assert await _step_up_code(ext, _request(auth_time=None), StepUpBody(current_password=PW)) == \
        (403, "reauth_required")


async def test_step_up_requires_browser_session():
    status, detail = await _step_up_code(_user(), _request(via="panel_token"), StepUpBody(current_password=PW))
    assert status == 403 and detail == core_auth.SESSION_REQUIRED_DETAIL


# ---------------------------------------------------------------------------------------------
# PUT /settings/sso mit Step-up [S8] und Admin-Mail
# ---------------------------------------------------------------------------------------------
OIDC_STORED = {"app_base_url": "https://dns.example.com", "oidc_enabled": True, "oidc_issuer": ISS,
               "oidc_client_id": "pdns", "oidc_client_secret": "client-secret-1"}


@pytest.fixture
def mails(monkeypatch):
    calls = []

    async def fake_notify(**kw):
        calls.append(kw)
        return 0

    monkeypatch.setattr(settings_sso, "notify_local_admins", fake_notify)
    return calls


def test_put_sso_sensitive_change_needs_step_up(env, mails):
    env.set_settings(**OIDC_STORED)
    admin = env.add_user("root", role="admin")
    c, h = env.client(), session_headers(admin)
    change = {"oidc": {"issuer": "https://new-idp.example.com/realms/x", "client_secret": "neues-secret"}}
    r = c.put("/api/v1/settings/sso", headers=h, json=change)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "stepup_required"
    assert c.get("/api/v1/settings/sso", headers=h).json()["oidc"]["issuer"] == ISS   # nichts gespeichert
    assert env.audits("SSO_SETTINGS_UPDATE") == [] and mails == []
    r = c.put("/api/v1/settings/sso", headers=h, json={**change, "step_up": {"current_password": PW}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["oidc"]["issuer"] == "https://new-idp.example.com/realms/x"
    (audit,) = env.audits("SSO_SETTINGS_UPDATE")
    assert audit.details["step_up"] == "password" and audit.resource_type == "system"
    assert "neues-secret" not in str(audit.details) and audit.details["changed"]["oidc_client_secret"] == "changed"
    assert len(mails) == 1 and mails[0]["actor"] == "root"
    assert "oidc_issuer" in mails[0]["changed_keys"]


def test_put_sso_non_sensitive_change_without_step_up(env, mails):
    env.set_settings(**OIDC_STORED)
    admin = env.add_user("root", role="admin")
    r = env.client().put("/api/v1/settings/sso", headers=session_headers(admin),
                         json={"oidc": {"display_name": "Firmen-Login"}})
    assert r.status_code == 200, r.text
    (audit,) = env.audits("SSO_SETTINGS_UPDATE")
    assert "step_up" not in audit.details and mails == []


def test_put_sso_external_admin_needs_fresh_login(env, mails):
    env.set_settings(**OIDC_STORED)
    ext_admin = env.add_user("extadmin", role="admin", auth_source="oidc", issuer=ISS, ext_id="adm")
    env.add_user("root", role="admin")   # Notfallzugang bleibt
    c = env.client()
    change = {"oidc": {"jit_enabled": True, "jit_allow_any_account": True}}
    old = session_headers(ext_admin, iat=int(time.time()) - 3600)
    r = c.put("/api/v1/settings/sso", headers=old, json=change)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "reauth_required"
    r = c.put("/api/v1/settings/sso", headers=session_headers(ext_admin), json=change)
    assert r.status_code == 200, r.text
    (audit,) = env.audits("SSO_SETTINGS_UPDATE")
    assert audit.details["step_up"] == "fresh_session" and audit.details["jit_allow_any_account"] is True


# ---------------------------------------------------------------------------------------------
# OIDC-Callback: 2FA aktiv, Geheimnis unlesbar/fehlend -> keine Anmeldung [S4]
# ---------------------------------------------------------------------------------------------
METADATA = {"issuer": ISS, "authorization_endpoint": ISS + "/auth", "token_endpoint": ISS + "/token",
            "jwks_uri": ISS + "/certs"}


def _oidc_ready(env, monkeypatch, subject):
    env.set_settings(app_base_url="https://dns.example.com", oidc_enabled=True, oidc_issuer=ISS,
                     oidc_client_id="pdns", oidc_token_auth_method="none")

    async def meta(issuer, **kw):
        return dict(METADATA)

    async def complete(cfg, metadata, *, code, state_payload):
        return ExternalProfile(source="oidc", issuer=ISS, subject=subject, username_hint="olga", email=None,
                               email_verified=None, display_name=None, groups=None)

    monkeypatch.setattr(sso_oidc, "get_provider_metadata", meta)
    monkeypatch.setattr(sso_oidc, "complete_authorization", complete)
    client = env.client()
    cookie = sso_oidc.create_oidc_state_token(state="st-1", nonce="n-1", code_verifier="cv-1", issuer=ISS,
                                              intent="login")
    return client, {"Cookie": f"pdnsmgr_oidc={cookie}"}


@pytest.mark.parametrize("raw_secret", ["enc:v1:kaputt-nicht-entschluesselbar", None])
def test_oidc_callback_totp_unreadable_no_login(env, monkeypatch, detached, raw_secret):
    user = env.add_user("olga", auth_source="oidc", issuer=ISS, ext_id="sub-olga", totp_enabled=True)
    env.session.execute(text("UPDATE users SET totp_secret = :v WHERE id = :i"), {"v": raw_secret, "i": user.id})
    env.session.commit()
    env.session.expire_all()
    c, state_cookie = _oidc_ready(env, monkeypatch, "sub-olga")
    r = c.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": "st-1"}, headers=state_cookie)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?sso_error=totp_unreadable"
    cookies = r.headers.get_list("set-cookie")
    assert not any(h.startswith(f"{settings.AUTH_COOKIE_NAME}=") for h in cookies)
    assert not any(h.startswith("pdnsmgr_2fa=") for h in cookies)
    assert reasons(detached) == ["totp_unreadable"]
    assert env.audits("LOGIN") == []


def test_oidc_callback_totp_ok_goes_to_2fa_step(env, monkeypatch):
    env.add_user("olga", auth_source="oidc", issuer=ISS, ext_id="sub-olga", totp_enabled=True,
                 totp_secret=TOTP_SECRET)
    c, state_cookie = _oidc_ready(env, monkeypatch, "sub-olga")
    r = c.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": "st-1"}, headers=state_cookie)
    assert r.status_code == 303 and r.headers["location"] == "/login?sso_2fa=1"
    cookies = r.headers.get_list("set-cookie")
    two = [h for h in cookies if h.startswith("pdnsmgr_2fa=")]
    assert two and "path=/api/v1/auth/login/2fa" in two[0].lower() and "secure" in two[0].lower()
    assert not any(h.startswith(f"{settings.AUTH_COOKIE_NAME}=") for h in cookies)
    # Abschluss mit dem Cookie (Nr. 17)
    r2 = c.post("/api/v1/auth/login/2fa", json={"totp_code": pyotp.TOTP(TOTP_SECRET).now()})
    assert r2.status_code == 200, r2.text
    assert [a.details["method"] for a in env.audits("LOGIN")] == ["oidc+totp"]


def test_oidc_callback_require_totp_off_logs_in_directly(env, monkeypatch):
    env.set_settings(sso_require_totp=False)
    env.add_user("olga", auth_source="oidc", issuer=ISS, ext_id="sub-olga", totp_enabled=True,
                 totp_secret=TOTP_SECRET)
    c, state_cookie = _oidc_ready(env, monkeypatch, "sub-olga")
    r = c.get("/api/v1/auth/oidc/callback", params={"code": "abc", "state": "st-1"}, headers=state_cookie)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert any(h.startswith(f"{settings.AUTH_COOKIE_NAME}=") for h in r.headers.get_list("set-cookie"))


# ---------------------------------------------------------------------------------------------
# LDAP-Verknuepfung nutzt die Login-Zaehler [S2]
# ---------------------------------------------------------------------------------------------
def test_ldap_link_uses_login_counters(env, ldap):
    env.set_settings(**LDAP_ON)
    user = env.add_user("bob")
    ldap.add("b.directory", LDAP_PW, "guid-bob")
    c, h = env.client(ip="198.51.100.60"), session_headers(user)
    url = "/api/v1/auth/me/sso/ldap/link"
    body = {"current_password": PW, "ldap_username": "b.directory", "ldap_password": LDAP_PW}
    # falsches Panel-Passwort zaehlt fuer den Panel-Benutzer
    r = c.post(url, headers=h, json={**body, "current_password": "falsch-1"})
    assert r.status_code == 400 and r.json()["detail"] == "Aktuelles Passwort ist falsch"
    assert user_count("bob") == 1 and ldap.binds == 0
    # falsches Verzeichnis-Passwort zaehlt fuer den Verzeichnis-Namen
    r = c.post(url, headers=h, json={**body, "ldap_password": "falsch-2"})
    assert r.status_code == 400 and ldap.binds == 1 and user_count("b.directory") == 1
    # gesperrter Verzeichnis-Name: 429 ohne Bind
    for i in range(lrl.USER_MAX_FAILS):
        lrl.record_failed_login(f"203.0.113.{i + 1}", "B.Directory")
    r = c.post(url, headers=h, json=body)
    assert r.status_code == 429 and ldap.binds == 1
    # gesperrter Panel-Benutzer ebenso
    lrl.reset_for_tests()
    for i in range(lrl.USER_MAX_FAILS):
        lrl.record_failed_login(f"203.0.113.{i + 1}", "bob")
    assert c.post(url, headers=h, json=body).status_code == 429 and ldap.binds == 1


@pytest.mark.parametrize("error", [sso_ldap.LdapUnavailable("Timeout"), sso_ldap.LdapConfigError("ID-Attribut")],
                         ids=["unavailable", "config"])
def test_ldap_link_broken_ldap_counts_directory_name(env, ldap, error):
    """Review Welle 2 [S2]: auch beim Verknuepfen zaehlen Ausfall/Konfigurationsfehler fuer den Verzeichnis-Namen."""
    env.set_settings(**LDAP_ON)
    user = env.add_user("bob")
    ldap.error = error
    c, h = env.client(ip="198.51.100.61"), session_headers(user)
    url = "/api/v1/auth/me/sso/ldap/link"
    body = {"current_password": PW, "ldap_username": "b.directory", "ldap_password": LDAP_PW}
    for _ in range(lrl.USER_MAX_FAILS):
        assert c.post(url, headers=h, json=body).status_code == 503
    assert user_count("b.directory") == lrl.USER_MAX_FAILS and user_count("bob") == 0
    assert c.post(url, headers=h, json=body).status_code == 429
    assert ldap.binds == lrl.USER_MAX_FAILS
