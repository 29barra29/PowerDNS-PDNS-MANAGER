"""SSO-Router (F10 9.1 Nr. 15, 16, 24-28, 31; Plan S3, S5): Anbieterliste, OIDC-Start/-Callback, Verknuepfung,
SSO-Einstellungen inkl. Test-Endpunkt, App-Info, Benutzerliste, Notfallzugang, Setup ueber ``complete_login``.

SQLite hinter einem AsyncSession-Adapter (``f10fakes``); OIDC-Dienst und LDAP sind gemockt bzw. laufen gegen einen
``httpx.MockTransport``. Fehler-Audits (``write_audit_detached``) werden eingesammelt.
"""
from __future__ import annotations

import sys
import types
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from f10fakes import F10Env, SqliteSession, session_headers
from app.core import auth as core_auth
from app.core import login_rate_limit as lrl
from app.core import secrets as secret_store
from app.core.auth import password_version
from app.core.config import settings
from app.core.secret_mask import SECRET_MASK
from app.models.models import WebAuthnCredential
from app.routers import auth as auth_router
from app.routers import settings as settings_router
from app.routers import settings_sso
from app.routers import setup as setup_router
from app.routers import sso as sso_router
from app.services import sso_ldap, sso_oidc
from app.services.sso_ldap import LdapIdentity
from app.services.sso_oidc import OidcError
from app.services.sso_provisioning import ExternalProfile

PW = "Passwort-123"
ISS = "https://idp.example.com/realms/firma"
BASE = "https://dns.example.com"
METADATA = {"issuer": ISS, "authorization_endpoint": ISS + "/protocol/openid-connect/auth",
            "token_endpoint": ISS + "/protocol/openid-connect/token", "jwks_uri": ISS + "/certs"}
OIDC_ON = {"app_base_url": BASE, "oidc_enabled": True, "oidc_issuer": ISS, "oidc_client_id": "pdns",
           "oidc_client_secret": "client-secret-1", "oidc_jit_enabled": True, "oidc_jit_allow_any_account": True}
LDAP_ON = {"ldap_enabled": True, "ldap_server_urls": '["ldaps://dc1.example.com"]',
           "ldap_user_base_dn": "DC=example,DC=com", "ldap_bind_dn": "CN=svc,DC=example,DC=com",
           "ldap_bind_password": "bind-pw-1", "ldap_display_name": "Windows"}


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
    e = F10Env(auth_router, sso_router, settings_sso, settings_router, setup_router)
    yield e
    e.close()


@pytest.fixture
def mails(monkeypatch):
    calls = []

    async def fake_notify(**kw):
        calls.append(kw)
        return 0

    monkeypatch.setattr(settings_sso, "notify_local_admins", fake_notify)
    return calls


def profile(sub="sub-1", *, username="jdoe", groups=None, email=None):
    return ExternalProfile(source="oidc", issuer=ISS, subject=sub, username_hint=username, email=email,
                           email_verified=True if email else None, display_name="John Doe", groups=groups)


class FakeOidc:
    """Ersetzt Discovery und ``complete_authorization`` (Profil bzw. Fehler)."""

    def __init__(self):
        self.metadata = dict(METADATA)
        self.discovery_error: OidcError | None = None
        self.profile = profile()
        self.error: Exception | None = None
        self.calls = 0
        self.discovery_calls = 0

    async def get_provider_metadata(self, issuer, **_kw):
        self.discovery_calls += 1
        if self.discovery_error:
            raise self.discovery_error
        return dict(self.metadata)

    async def complete_authorization(self, cfg, metadata, *, code, state_payload):
        self.calls += 1
        if self.error:
            raise self.error
        return self.profile


@pytest.fixture
def oidc(monkeypatch):
    fake = FakeOidc()
    monkeypatch.setattr(sso_oidc, "get_provider_metadata", fake.get_provider_metadata)
    monkeypatch.setattr(sso_oidc, "complete_authorization", fake.complete_authorization)
    return fake


def state_cookie(intent="login", *, state="st-1", user=None, issuer=ISS) -> dict:
    tok = sso_oidc.create_oidc_state_token(
        state=state, nonce="n-1", code_verifier="cv-1", issuer=issuer, intent=intent,
        user_id=user.id if user else None, pwv=password_version(user.hashed_password) if user else None)
    return {"Cookie": f"pdnsmgr_oidc={tok}"}


def callback(client, cookie=None, **params):
    params.setdefault("code", "code-1")
    params.setdefault("state", "st-1")
    return client.get("/api/v1/auth/oidc/callback", params=params, headers=cookie or {})


def cookies_of(resp) -> list[str]:
    return resp.headers.get_list("set-cookie")


def has_session_cookie(resp) -> bool:
    return any(h.startswith(f"{settings.AUTH_COOKIE_NAME}=") for h in cookies_of(resp))


def state_cookie_deleted(resp) -> bool:
    return any(h.startswith("pdnsmgr_oidc=") and "max-age=0" in h.lower() for h in cookies_of(resp))


def failed(detached, action="LOGIN_FAILED"):
    return [kw for a, kw in detached if a == action]


# ---------------------------------------------------------------------------------------------
# Nr. 27 Anbieterliste
# ---------------------------------------------------------------------------------------------
def test_providers_default_and_complete(env):
    c = env.client()
    r = c.get("/api/v1/auth/sso/providers")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {"local_login_enabled": True, "providers": [], "ldap": {"enabled": False, "label": None},
                        "linking": {"oidc": False, "ldap": False}}
    env.set_settings(oidc_enabled=True, oidc_issuer=ISS, oidc_client_id="pdns")   # Secret und Basis-URL fehlen
    assert c.get("/api/v1/auth/sso/providers").json()["providers"] == []
    env.set_settings(**OIDC_ON, **LDAP_ON, oidc_display_name="Firmen-Login")
    body = c.get("/api/v1/auth/sso/providers").json()
    assert body["providers"] == [{"id": "oidc", "type": "oidc", "label": "Firmen-Login",
                                  "start_url": "/api/v1/auth/oidc/start"}]
    assert body["ldap"] == {"enabled": True, "label": "Windows"}
    assert body["linking"] == {"oidc": True, "ldap": True}
    raw = c.get("/api/v1/auth/sso/providers").text
    for secret in (ISS, "client-secret-1", "bind-pw-1", "pdns\""):
        assert secret not in raw


# ---------------------------------------------------------------------------------------------
# Nr. 15 OIDC-Start
# ---------------------------------------------------------------------------------------------
def test_start_disabled_and_incomplete(env, oidc):
    c = env.client()
    r = c.get("/api/v1/auth/oidc/start")
    assert r.status_code == 303 and r.headers["location"] == "/login?sso_error=disabled"
    assert r.headers["cache-control"] == "no-store"
    env.set_settings(oidc_enabled=True, oidc_issuer=ISS)
    r = c.get("/api/v1/auth/oidc/start")
    assert r.status_code == 303 and r.headers["location"] == "/login?sso_error=config"


def test_start_discovery_error_audited(env, oidc, detached):
    env.set_settings(**OIDC_ON)
    oidc.discovery_error = OidcError("discovery", "HTTP 500")
    r = env.client().get("/api/v1/auth/oidc/start")
    assert r.status_code == 303 and r.headers["location"] == "/login?sso_error=discovery"
    (kw,) = failed(detached)
    assert kw["details"]["reason"] == "discovery" and kw["details"]["method"] == "oidc"


def test_start_discovery_error_negative_cache_and_dedupe(env, oidc, detached):
    """Review Welle 2: nicht erreichbarer IdP – kein ausgehender Request und kein Audit je Anfrage."""
    env.set_settings(**OIDC_ON)
    oidc.discovery_error = OidcError("discovery", "Timeout")
    c = env.client(ip="198.51.100.74")
    for _ in range(5):
        assert c.get("/api/v1/auth/oidc/start").headers["location"] == "/login?sso_error=discovery"
    assert oidc.discovery_calls == 1 and len(failed(detached)) == 1
    # Negativ-Cache abgelaufen, IdP wieder erreichbar -> normaler Start
    oidc.discovery_error = None
    for key, (_until, exc) in list(sso_router._discovery_failed.items()):
        sso_router._discovery_failed[key] = (0.0, exc)
    assert c.get("/api/v1/auth/oidc/start").status_code == 302 and oidc.discovery_calls == 2


@pytest.mark.parametrize("base, secure", [(BASE, True), ("http://dns.local", False)])
def test_start_redirects_with_state_cookie(env, oidc, base, secure):
    env.set_settings(**{**OIDC_ON, "app_base_url": base})
    r = env.client().get("/api/v1/auth/oidc/start")
    assert r.status_code == 302 and r.headers["cache-control"] == "no-store"
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == METADATA["authorization_endpoint"]
    q = parse_qs(loc.query)
    assert q["code_challenge_method"] == ["S256"] and q["response_mode"] == ["query"]
    assert q["redirect_uri"] == [f"{base}/api/v1/auth/oidc/callback"] and q["client_id"] == ["pdns"]
    (hdr,) = [h for h in cookies_of(r) if h.startswith("pdnsmgr_oidc=")]
    low = hdr.lower()
    assert "path=/api/v1/auth/oidc" in low and "httponly" in low and "samesite=lax" in low and "max-age=600" in low
    assert ("secure" in low.split(";")[-1] or "; secure" in low) is secure
    payload = sso_oidc.decode_oidc_state_token(hdr.split(";")[0].split("=", 1)[1])
    assert payload["it"] == "login" and payload["st"] == q["state"][0] and payload["iss"] == ISS


# ---------------------------------------------------------------------------------------------
# Nr. 16 OIDC-Callback
# ---------------------------------------------------------------------------------------------
def test_callback_without_cookie_mismatch_and_replay(env, oidc, detached):
    env.set_settings(**OIDC_ON)
    c = env.client()
    r = callback(c)
    assert r.status_code == 303 and r.headers["location"] == "/login?sso_error=state"
    assert state_cookie_deleted(r) and r.headers["cache-control"] == "no-store"
    r = callback(c, state_cookie(state="anders"))
    assert r.headers["location"] == "/login?sso_error=state"
    ok = state_cookie()
    assert callback(c, ok).headers["location"] == "/"
    r = callback(c, ok)                                   # Replay desselben States
    assert r.headers["location"] == "/login?sso_error=state"
    # vor der State-Pruefung: hoechstens ein Audit je (IP, Code) im Dedupe-Fenster (Review Welle 2)
    assert [kw["details"]["reason"] for kw in failed(detached)] == ["state"]
    assert oidc.calls == 1


def test_callback_oidc_disabled_no_audit_no_metric(env, oidc, detached, monkeypatch):
    """Review Welle 2: OIDC aus (Standard) – anonyme Callbacks erzeugen weder Audit noch Metrik."""
    metrics = []
    monkeypatch.setattr(sso_router.prom, "record_login", lambda *a: metrics.append(a))
    c = env.client()
    for i in range(30):
        r = c.get("/api/v1/auth/oidc/callback", params={"state": f"x{i}", "code": "y"})
        assert r.status_code == 303 and r.headers["location"] == "/login?sso_error=disabled"
    assert failed(detached) == [] and metrics == [] and oidc.discovery_calls == 0


def test_callback_without_state_audit_deduplicated_per_ip(env, oidc, detached):
    env.set_settings(**OIDC_ON)
    a, b = env.client(ip="198.51.100.70"), env.client(ip="2001:db8:1:2::5")
    for i in range(10):
        assert callback(a, state=f"x{i}").headers["location"] == "/login?sso_error=state"
    assert callback(b).headers["location"] == "/login?sso_error=state"
    assert callback(env.client(ip="2001:db8:1:2::99")).headers["location"] == "/login?sso_error=state"  # gleiches /64
    kws = failed(detached)
    assert [kw["details"]["ip"] for kw in kws] == ["198.51.100.70", "2001:db8:1:2::5"]
    assert oidc.discovery_calls == 0                     # ohne gueltigen State keine Discovery


def test_callback_failures_throttled_per_ip(env, oidc, detached):
    env.set_settings(**OIDC_ON)
    c = env.client(ip="198.51.100.71")
    for i in range(sso_router.OIDC_FAIL_MAX):
        assert callback(c, state=f"x{i}").headers["location"] == "/login?sso_error=state"
    n_audits = len(failed(detached))
    # gedrosselt: auch ein gueltiger State fuehrt weder zu Token-Tausch noch zu Audit oder Discovery
    r = callback(c, state_cookie())
    assert r.status_code == 303 and r.headers["location"] == "/login?sso_error=rate_limited"
    assert not has_session_cookie(r) and state_cookie_deleted(r)
    assert oidc.calls == 0 and oidc.discovery_calls == 0 and len(failed(detached)) == n_audits
    r = c.get("/api/v1/auth/oidc/start")
    assert r.headers["location"] == "/login?sso_error=rate_limited" and oidc.discovery_calls == 0
    # andere IP ist nicht betroffen
    assert callback(env.client(ip="198.51.100.72"), state_cookie()).headers["location"] == "/"


def test_callback_after_valid_state_still_audited(env, oidc, detached):
    """Nach gueltigem State bleibt jeder Fehler sichtbar (kein Dedupe), z. B. abgelehnte Konten."""
    env.set_settings(**{**OIDC_ON, "oidc_jit_enabled": False})
    c = env.client(ip="198.51.100.73")
    for i in range(3):
        st = f"st-{i}"
        assert callback(c, state_cookie(state=st), state=st).headers["location"] == "/login?sso_error=no_account"
    assert [kw["details"]["reason"] for kw in failed(detached)] == ["no_account"] * 3


def test_callback_idp_error_detail_filtered(env, oidc, detached):
    env.set_settings(**OIDC_ON)
    c = env.client()
    r = callback(c, state_cookie(), error="access_denied", error_description="<b>geheim</b>")
    assert r.headers["location"] == "/login?sso_error=idp_error&sso_detail=access_denied"
    r = callback(c, state_cookie(), error="<script>")
    assert r.headers["location"] == "/login?sso_error=idp_error"
    assert failed(detached) == []          # Fehler meldet der Anbieter selbst (Schritt 1): kein Audit
    assert oidc.calls == 0


def test_callback_iss_parameter_mismatch(env, oidc):
    env.set_settings(**OIDC_ON)
    r = callback(env.client(), state_cookie(), iss="https://evil.example.com")
    assert r.headers["location"] == "/login?sso_error=idp_error"
    r = callback(env.client(), state_cookie(state="st-2"), state="st-2", iss=ISS)
    assert r.headers["location"] == "/"


def test_callback_success_jit_login(env, oidc):
    env.set_settings(**OIDC_ON)
    r = callback(env.client(), state_cookie())
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert has_session_cookie(r) and state_cookie_deleted(r)
    user = env.user_by_name("jdoe")
    assert (user.auth_source, user.external_issuer, user.external_id) == ("oidc", ISS, "sub-1")
    (login,) = env.audits("LOGIN")
    assert login.details["method"] == "oidc" and login.details["issuer"] == ISS and login.details["jit"] is True
    assert env.audits("USER_CREATE")


@pytest.mark.parametrize("setup, code", [
    ({"oidc_enabled": False}, "disabled"),
    ({"oidc_client_id": ""}, "config"),
])
def test_callback_config_changed_since_start(env, oidc, setup, code):
    env.set_settings(**OIDC_ON)
    env.set_settings(**setup)
    assert callback(env.client(), state_cookie()).headers["location"] == f"/login?sso_error={code}"


def test_callback_issuer_changed_since_start(env, oidc):
    env.set_settings(**OIDC_ON)
    r = callback(env.client(), state_cookie(issuer="https://old-idp.example.com"))
    assert r.headers["location"] == "/login?sso_error=state"


def test_callback_missing_code(env, oidc):
    env.set_settings(**OIDC_ON)
    r = env.client().get("/api/v1/auth/oidc/callback", params={"state": "st-1"}, headers=state_cookie())
    assert r.headers["location"] == "/login?sso_error=idp_error"


@pytest.mark.parametrize("err, code", [
    (OidcError("token", "invalid_grant"), "token"),
    (OidcError("id_token", "nonce"), "id_token"),
    (OidcError("discovery", "jwks"), "discovery"),
])
def test_callback_oidc_errors(env, oidc, detached, err, code):
    env.set_settings(**OIDC_ON)
    oidc.error = err
    r = callback(env.client(), state_cookie())
    assert r.headers["location"] == f"/login?sso_error={code}" and not has_session_cookie(r)
    (kw,) = failed(detached)
    assert kw["details"]["reason"] == code and kw["error_message"] == err.log_detail


def test_callback_provisioning_errors(env, oidc, detached):
    env.set_settings(**{**OIDC_ON, "oidc_jit_enabled": False})
    r = callback(env.client(), state_cookie())
    assert r.headers["location"] == "/login?sso_error=no_account"
    user = env.add_user("off", auth_source="oidc", issuer=ISS, ext_id="sub-off", is_active=False)
    oidc.profile = profile("sub-off", username="off")
    r = callback(env.client(), state_cookie(state="st-2"), state="st-2")
    assert r.headers["location"] == "/login?sso_error=account_disabled"
    kws = failed(detached)
    assert [k["details"]["reason"] for k in kws] == ["no_account", "account_disabled"]
    assert kws[1]["user_id"] == user.id and kws[1]["details"]["sub"] == "sub-off"
    env.set_settings(oidc_jit_enabled=True, oidc_allowed_groups='["pdns-users"]', oidc_jit_allow_any_account=False)
    oidc.profile = profile("sub-new", username="neu", groups=["andere"])
    r = callback(env.client(), state_cookie(state="st-3"), state="st-3")
    assert r.headers["location"] == "/login?sso_error=not_allowed"


def test_callback_unexpected_error_rolls_back_jit(env, oidc, monkeypatch, detached):
    env.set_settings(**OIDC_ON)

    async def boom(*a, **k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(sso_router, "complete_login", boom)
    r = callback(env.client(), state_cookie())
    assert r.headers["location"] == "/login?sso_error=internal"
    assert env.user_by_name("jdoe") is None         # JIT-Anlage zurueckgerollt
    assert [k["details"]["reason"] for k in failed(detached)] == ["internal"]


# ---------------------------------------------------------------------------------------------
# Nr. 16/24 OIDC-Verknuepfung (Start + Abschluss im Callback)
# ---------------------------------------------------------------------------------------------
def test_oidc_link_start_checks(env, oidc):
    env.set_settings(**OIDC_ON)
    bob = env.add_user("bob", totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
    ext = env.add_user("ext", auth_source="ldap", issuer="ldap", ext_id="g")
    c = env.client(ip="198.51.100.70")
    url = "/api/v1/auth/me/sso/oidc/link"
    r = c.post(url, headers=session_headers(bob), json={"current_password": "falsch-1"})
    assert r.status_code == 400 and r.json()["detail"] == "Aktuelles Passwort ist falsch"
    assert lrl._user_limiter.count("bob") == 1
    r = c.post(url, headers=session_headers(bob), json={"current_password": PW})
    assert r.status_code == 400 and r.json()["detail"] == "Falscher TOTP-Code"
    r = c.post(url, headers=session_headers(ext), json={"current_password": PW})
    assert r.status_code == 400 and r.json()["detail"] == sso_router.ALREADY_LINKED_DETAIL
    env.set_settings(oidc_allow_linking=False)
    r = c.post(url, headers=session_headers(bob), json={"current_password": PW})
    assert r.status_code == 400 and r.json()["detail"] == "Die Verknüpfung mit OIDC ist nicht aktiviert"


def test_oidc_link_start_success_and_errors(env, oidc):
    env.set_settings(**OIDC_ON)
    root = env.add_user("root", role="admin")
    bob = env.add_user("bob")
    c = env.client()
    url = "/api/v1/auth/me/sso/oidc/link"
    r = c.post(url, headers=session_headers(bob), json={"current_password": PW})
    assert r.status_code == 200, r.text
    assert r.json()["authorization_url"].startswith(METADATA["authorization_endpoint"] + "?")
    (hdr,) = [h for h in cookies_of(r) if h.startswith("pdnsmgr_oidc=")]
    payload = sso_oidc.decode_oidc_state_token(hdr.split(";")[0].split("=", 1)[1])
    assert payload["it"] == "link" and payload["uid"] == bob.id
    assert payload["pwv"] == password_version(bob.hashed_password)
    # letzter aktiver lokaler Admin bei aktivem SSO: Notfallzugang
    r = c.post(url, headers=session_headers(root), json={"current_password": PW})
    assert r.status_code == 400 and r.json()["detail"] == "Mindestens ein aktiver lokaler Admin muss als Notfallzugang bestehen bleiben"
    oidc.discovery_error = OidcError("discovery", "Timeout")
    r = c.post(url, headers=session_headers(bob), json={"current_password": PW})
    assert r.status_code == 502


def test_oidc_link_callback_success(env, oidc):
    env.set_settings(**OIDC_ON)
    bob = env.add_user("bob")
    env.session.add(WebAuthnCredential(user_id=bob.id, name="k", credential_id="c-bob", public_key="pk"))
    env.session.commit()
    oidc.profile = profile("sub-bob", username="robert")
    r = callback(env.client(), state_cookie("link", user=bob))
    assert r.status_code == 303 and r.headers["location"] == "/settings?tab=integrations&sso_linked=oidc"
    assert has_session_cookie(r)
    bob = env.reload(bob)
    assert (bob.auth_source, bob.external_issuer, bob.external_id) == ("oidc", ISS, "sub-bob")
    (link,) = env.audits("USER_SSO_LINK")
    assert link.details["source"] == "oidc" and link.details["passkeys_removed"] == 1
    (login,) = env.audits("LOGIN")
    assert login.details["linked"] is True and login.details["method"] == "oidc"


def test_oidc_link_callback_failures(env, oidc, detached):
    env.set_settings(**OIDC_ON)
    bob = env.add_user("bob")
    other = env.add_user("taken", auth_source="oidc", issuer=ISS, ext_id="sub-taken")
    cookie = state_cookie("link", user=bob)
    # Passwort seit dem Start geaendert -> pwv passt nicht
    bob.hashed_password = core_auth.hash_password("Neues-Passwort-1")
    env.session.commit()
    r = callback(env.client(), cookie)
    assert r.headers["location"] == "/settings?tab=integrations&sso_error=link_failed"
    # Identitaet gehoert schon einem anderen Konto
    oidc.profile = profile("sub-taken")
    r = callback(env.client(), state_cookie("link", state="st-2", user=bob), state="st-2")
    assert r.headers["location"] == "/settings?tab=integrations&sso_error=link_conflict"
    assert env.reload(bob).auth_source == "local" and other.id
    reasons = [kw["details"]["reason"] for a, kw in detached if a == "USER_SSO_LINK"]
    assert reasons == ["link_failed", "link_conflict"]
    assert failed(detached) == []   # Verknuepfung: kein LOGIN_FAILED


def test_oidc_link_callback_rejected_after_access_revocation(env, oidc, detached):
    """W2-NACHARBEIT 4.1 (WS-W3-NACHARBEIT): Ein Link-Start vor "Alle Zugaenge widerrufen" bzw. einem Admin-Reset
    laesst sich danach nicht mehr abschliessen (State traegt iat); ein neuer Start nach dem Widerruf klappt."""
    from datetime import timedelta

    from app.core.timeutil import utcnow_naive
    from app.services import access_revocation

    env.set_settings(**OIDC_ON)
    bob = env.add_user("bob")
    cookie = state_cookie("link", user=bob)   # Start, z. B. mit einer gekaperten Sitzung
    payload = sso_oidc.decode_oidc_state_token(cookie["Cookie"].split("=", 1)[1])
    assert isinstance(payload["iat"], int)
    bob.sessions_revoked_at = access_revocation.session_revocation_time()
    env.session.commit()
    oidc.profile = profile("sub-bob", username="robert")
    r = callback(env.client(), cookie)
    assert r.status_code == 303 and r.headers["location"] == "/settings?tab=integrations&sso_error=link_failed"
    assert not has_session_cookie(r)
    bob = env.reload(bob)
    assert (bob.auth_source, bob.external_id) == ("local", None)
    reasons = [kw["details"]["reason"] for a, kw in detached if a == "USER_SSO_LINK"]
    assert reasons == ["link_failed"]
    # Widerruf liegt vor dem (neuen) Start -> Verknuepfung moeglich
    bob.sessions_revoked_at = utcnow_naive().replace(microsecond=0) - timedelta(minutes=1)
    env.session.commit()
    r = callback(env.client(), state_cookie("link", state="st-2", user=bob), state="st-2")
    assert r.headers["location"] == "/settings?tab=integrations&sso_linked=oidc"
    assert env.reload(bob).auth_source == "oidc"


def test_oidc_state_without_iat_fails_closed_only_after_revocation(env, oidc):
    """Alt-State ohne iat (vor dem Update gestartet, max. 10 min): ohne Widerruf weiter gueltig, nach Widerruf nicht."""
    from jose import jwt as jose_jwt

    env.set_settings(**OIDC_ON)
    bob = env.add_user("bob")

    def legacy_cookie(state):
        tok = state_cookie("link", state=state, user=bob)["Cookie"].split("=", 1)[1]
        payload = jose_jwt.get_unverified_claims(tok)
        payload.pop("iat")
        return {"Cookie": "pdnsmgr_oidc=" + jose_jwt.encode(payload, settings.JWT_SECRET_KEY,
                                                            algorithm=settings.JWT_ALGORITHM)}

    from app.services import access_revocation
    bob.sessions_revoked_at = access_revocation.session_revocation_time()
    env.session.commit()
    oidc.profile = profile("sub-bob")
    r = callback(env.client(), legacy_cookie("st-1"), state="st-1")
    assert r.headers["location"] == "/settings?tab=integrations&sso_error=link_failed"
    bob.sessions_revoked_at = None
    env.session.commit()
    r = callback(env.client(), legacy_cookie("st-2"), state="st-2")
    assert r.headers["location"] == "/settings?tab=integrations&sso_linked=oidc"


# ---------------------------------------------------------------------------------------------
# Nr. 24 LDAP-Verknuepfung
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def ldap(monkeypatch):
    state = types.SimpleNamespace(identity=None, error=None, binds=0)

    async def authenticate(cfg, username, password, **_kw):
        state.binds += 1
        if state.error:
            raise state.error
        return state.identity

    monkeypatch.setattr(sso_ldap, "authenticate", authenticate)
    return state


def ldap_identity(sub="guid-1", groups=None):
    p = ExternalProfile(source="ldap", issuer="ldap", subject=sub, username_hint="b.dir", email=None,
                        email_verified=None, display_name=None, groups=groups)
    return LdapIdentity(dn="CN=b.dir,DC=example,DC=com", profile=p)


def test_ldap_link_success(env, ldap):
    env.set_settings(**LDAP_ON)
    bob = env.add_user("bob")
    old_headers = session_headers(bob)
    ldap.identity = ldap_identity()
    body = {"current_password": PW, "ldap_username": "b.dir", "ldap_password": "x-pw-1"}
    c = env.client()
    r = c.post("/api/v1/auth/me/sso/ldap/link", headers=old_headers, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Konto mit LDAP verknüpft" and r.json()["user"]["auth_source"] == "ldap"
    assert has_session_cookie(r)
    (link,) = env.audits("USER_SSO_LINK")
    assert link.details["source"] == "ldap" and link.details["external_id"] == "guid-1"
    # Passwort-Hash geaendert -> alte Session ungueltig
    assert c.get("/api/v1/auth/me", headers=old_headers).status_code == 401


def test_ldap_link_errors(env, ldap, detached):
    env.set_settings(**LDAP_ON)
    bob = env.add_user("bob")
    env.add_user("taken", auth_source="ldap", issuer="ldap", ext_id="guid-taken")
    c, h = env.client(), session_headers(bob)
    url = "/api/v1/auth/me/sso/ldap/link"
    body = {"current_password": PW, "ldap_username": "b.dir", "ldap_password": "x-pw-1"}
    r = c.post(url, headers=h, json=body)          # identity None
    assert r.status_code == 400 and r.json()["detail"] == "LDAP-Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch"
    ldap.identity = ldap_identity("guid-taken")
    r = c.post(url, headers=h, json=body)
    assert r.status_code == 409
    env.set_settings(ldap_allowed_groups='["CN=pdns,OU=G,DC=example,DC=com"]')
    ldap.identity = ldap_identity("guid-new", groups=["CN=x,OU=G,DC=example,DC=com"])
    r = c.post(url, headers=h, json=body)
    assert r.status_code == 403 and r.json()["detail"] == "Dein Verzeichnis-Konto ist nicht für dieses Panel freigegeben"
    assert [kw["error_message"] for a, kw in detached if a == "USER_SSO_LINK"] == ["link_conflict", "not_allowed"]
    ldap.error = sso_ldap.LdapUnavailable("weg")
    assert c.post(url, headers=h, json=body).status_code == 503
    ldap.error = sso_ldap.LdapConfigError("bind")
    r = c.post(url, headers=h, json=body)
    assert r.status_code == 503 and "fehlerhaft konfiguriert" in r.json()["detail"]
    env.set_settings(ldap_allow_linking=False)
    r = c.post(url, headers=h, json=body)
    assert r.status_code == 400 and r.json()["detail"] == "Die Verknüpfung mit LDAP ist nicht aktiviert"
    assert env.reload(bob).auth_source == "local"


# ---------------------------------------------------------------------------------------------
# Nr. 25 Einstellungen GET/PUT
# ---------------------------------------------------------------------------------------------
def test_settings_get_masks_secrets(env):
    env.set_settings(**OIDC_ON, **LDAP_ON)
    admin = env.add_user("root", role="admin")
    env.add_user("x", auth_source="oidc", issuer=ISS, ext_id="s")
    r = env.client().get("/api/v1/settings/sso", headers=session_headers(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["oidc"]["client_secret"] == SECRET_MASK and body["oidc"]["client_secret_set"] is True
    assert body["ldap"]["bind_password"] == SECRET_MASK and body["oidc"]["linked_accounts"] == 1
    assert body["general"]["redirect_uri"] == BASE + "/api/v1/auth/oidc/callback"
    assert "client-secret-1" not in r.text and "bind-pw-1" not in r.text


def test_settings_get_and_put_include_session_max_age(env, mails, monkeypatch):
    """Review Welle 2 / Plan E-F10-2: der SSO-Tab warnt bei mehr als 86400 s – dazu liefert das Backend den Wert."""
    from app.schemas.sso import SsoSettingsOut

    monkeypatch.setattr(settings, "AUTH_COOKIE_MAX_AGE", 7 * 86400)
    admin = env.add_user("root", role="admin")
    c, h = env.client(), session_headers(admin)
    body = c.get("/api/v1/settings/sso", headers=h).json()
    assert body["general"]["session_max_age"] == 7 * 86400
    assert SsoSettingsOut.model_validate(body).general.session_max_age == 7 * 86400
    r = c.put("/api/v1/settings/sso", headers=h, json={"oidc": {"display_name": "Firmen-Login"}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["general"]["session_max_age"] == 7 * 86400


def test_settings_put_secret_keep_and_clear(env, mails):
    env.set_settings(app_base_url=BASE, oidc_issuer=ISS, oidc_client_id="pdns", oidc_client_secret="client-secret-1")
    admin = env.add_user("root", role="admin")
    c, h = env.client(), session_headers(admin)
    r = c.put("/api/v1/settings/sso", headers=h, json={"oidc": {"client_secret": SECRET_MASK, "display_name": "A"}})
    assert r.status_code == 200 and r.json()["settings"]["oidc"]["client_secret_set"] is True
    r = c.put("/api/v1/settings/sso", headers=h, json={"oidc": {"client_secret": ""}})
    assert r.status_code == 200 and r.json()["settings"]["oidc"]["client_secret_set"] is False
    audits = env.audits("SSO_SETTINGS_UPDATE")
    assert audits[-1].details["changed"]["oidc_client_secret"] == "removed"
    assert all("client-secret-1" not in str(a.details) for a in audits)


def test_settings_put_consistency_and_validation(env, mails):
    admin = env.add_user("root", role="admin")
    c, h = env.client(), session_headers(admin)
    r = c.put("/api/v1/settings/sso", headers=h, json={"oidc": {"enabled": True},
                                                       "step_up": {"current_password": PW}})
    assert r.status_code == 400 and r.json()["detail"] == "Für OIDC müssen Issuer-URL und Client-ID gesetzt sein"
    r = c.put("/api/v1/settings/sso", headers=h, json={"ldap": {"unique_id_attr": "sAMAccountName"}})
    assert r.status_code == 422          # S16 vor jedem Step-up
    r = c.put("/api/v1/settings/sso", headers=h, json={"oidc": {"issuer": "http://idp.example.com"}})
    assert r.status_code == 422          # Feldvalidierung (https)
    assert env.audits("SSO_SETTINGS_UPDATE") == []


def test_settings_put_issuer_change_warning_and_cache(env, monkeypatch, mails):
    env.set_settings(**OIDC_ON)
    admin = env.add_user("root", role="admin")
    env.add_user("x", auth_source="oidc", issuer=ISS, ext_id="s")
    cleared = []
    monkeypatch.setattr(sso_oidc, "clear_caches", lambda: cleared.append(1))
    r = env.client().put("/api/v1/settings/sso", headers=session_headers(admin), json={
        "oidc": {"issuer": "https://neu.example.com/realms/y", "client_secret": "neu-secret-1"},
        "step_up": {"current_password": PW}})
    assert r.status_code == 200, r.text
    assert any("1 bestehende OIDC-Konten" in w for w in r.json()["warnings"])
    assert cleared == [1]


def test_settings_put_retarget_without_secret_400(env, mails):
    env.set_settings(**OIDC_ON)
    admin = env.add_user("root", role="admin")
    r = env.client().put("/api/v1/settings/sso", headers=session_headers(admin), json={
        "oidc": {"issuer": "https://neu.example.com"}, "step_up": {"current_password": PW}})
    assert r.status_code == 400 and "erneut eingeben" in r.json()["detail"]


def test_settings_routes_need_admin_session(env):
    user = env.add_user("bob")
    c = env.client()
    assert c.get("/api/v1/settings/sso", headers=session_headers(user)).status_code == 403
    assert c.get("/api/v1/settings/sso").status_code == 401


# ---------------------------------------------------------------------------------------------
# Nr. 26 Test-Endpunkt [S3]
# ---------------------------------------------------------------------------------------------
def test_settings_test_oidc_discovery_error(env, detached):
    env.set_settings(app_base_url=BASE)
    admin = env.add_user("root", role="admin")
    sso_oidc.set_transport_for_tests(httpx.MockTransport(lambda req: httpx.Response(500, json={})))
    r = env.client().post("/api/v1/settings/sso/test", headers=session_headers(admin),
                          json={"target": "oidc", "oidc": {"issuer": ISS, "client_id": "pdns"}})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False and body["error"] == "Discovery fehlgeschlagen: HTTP 500"
    (kw,) = [kw for a, kw in detached if a == "SSO_SETTINGS_TEST"]
    assert kw["details"] == {"target": "oidc", "issuer": ISS, "unsaved_values": True, "success": False}


def test_settings_test_ldap_passthrough_and_audit(env, monkeypatch):
    env.set_settings(**LDAP_ON)
    admin = env.add_user("root", role="admin")
    seen = {}

    async def fake_test(cfg, user, password, **_kw):
        seen.update(cfg=cfg, user=user, password=password)
        return {"success": False, "message": None, "error": "Passwort des Testbenutzers falsch", "warnings": [],
                "details": {"server": "ldaps://dc1.example.com", "user": {"dn": "CN=t", "password_ok": False}}}

    monkeypatch.setattr(sso_ldap, "test_connection", fake_test)
    r = env.client().post("/api/v1/settings/sso/test", headers=session_headers(admin), json={
        "target": "ldap", "test_username": "t.user", "test_password": "geheim-test-1"})
    assert r.status_code == 200 and r.json()["error"] == "Passwort des Testbenutzers falsch"
    assert r.json()["details"]["user"]["dn"] == "CN=t"
    assert seen["cfg"].bind_password == "bind-pw-1" and seen["password"] == "geheim-test-1"


def test_settings_test_ldap_success_audit_without_values(env, monkeypatch):
    env.set_settings(**LDAP_ON)
    admin = env.add_user("root", role="admin")

    async def fake_test(cfg, user, password, **_kw):
        return {"success": True, "message": "ok", "error": None, "warnings": [], "details": {}}

    monkeypatch.setattr(sso_ldap, "test_connection", fake_test)
    r = env.client().post("/api/v1/settings/sso/test", headers=session_headers(admin), json={
        "target": "ldap", "test_username": "t.user", "test_password": "geheim-test-1"})
    assert r.json()["success"] is True
    (audit,) = env.audits("SSO_SETTINGS_TEST")
    assert audit.details == {"target": "ldap", "servers": ["ldaps://dc1.example.com"], "unsaved_values": False,
                             "test_user": True, "password_checked": True, "success": True}
    assert "geheim-test-1" not in str(audit.details) and "bind-pw-1" not in str(audit.details)


def test_settings_test_ldap_password_counts_as_failed_login(env, monkeypatch):
    """L-1 (WS-W3-NACHARBEIT): Ein LDAP-Test mit falschem Testpasswort zaehlt im Login-Limiter (IP + Testbenutzer);
    nach der Sperre 429 ohne Verzeichnis-Abfrage. Ohne Testpasswort und bei bestaetigtem Passwort zaehlt nichts."""
    env.set_settings(**LDAP_ON)
    admin = env.add_user("root", role="admin")
    calls = []

    async def fake_test(cfg, user, password, **_kw):
        calls.append(password)
        ok = password == "richtig-1"
        return {"success": ok, "message": None, "error": None if ok else "Passwort des Testbenutzers falsch",
                "warnings": [], "details": {"user": {"dn": "CN=t", "password_checked": True, "password_ok": ok}}}

    monkeypatch.setattr(sso_ldap, "test_connection", fake_test)
    c = env.client()

    def run(password=None, username="t.user"):
        body = {"target": "ldap", "test_username": username}
        if password is not None:
            body["test_password"] = password
        return c.post("/api/v1/settings/sso/test", headers=session_headers(admin), json=body)

    assert run("richtig-1").json()["success"] is True
    assert not lrl.is_login_rate_limited("testclient", "t.user")
    for _ in range(3):
        assert run().status_code == 200          # nur Benutzersuche: kein Zaehler
    for i in range(5):
        r = run(f"falsch-{i}")
        assert r.status_code == 200 and r.json()["success"] is False
    assert lrl.is_login_rate_limited("203.0.113.9", "t.user")   # Name gesperrt, auch fuer die Anmeldung
    before = len(calls)
    r = run("richtig-1")
    assert r.status_code == 429 and len(calls) == before
    assert run().status_code == 200              # ohne Passwort weiter moeglich
    assert run("x", username="anderer").status_code == 200


def test_settings_test_retarget_needs_secret(env, monkeypatch):
    env.set_settings(**LDAP_ON)
    admin = env.add_user("root", role="admin")
    called = []

    async def fake_test(*a, **k):
        called.append(1)
        return {"success": True}

    monkeypatch.setattr(sso_ldap, "test_connection", fake_test)
    r = env.client().post("/api/v1/settings/sso/test", headers=session_headers(admin), json={
        "target": "ldap", "ldap": {"server_urls": ["ldaps://evil.example.com"]}})
    assert r.status_code == 400 and called == []


def test_settings_test_unexpected_error(env, monkeypatch, detached):
    admin = env.add_user("root", role="admin")

    async def boom(*a, **k):
        raise RuntimeError("geheimes Detail")

    monkeypatch.setattr(sso_oidc, "test_configuration", boom)
    r = env.client().post("/api/v1/settings/sso/test", headers=session_headers(admin),
                          json={"target": "oidc", "oidc": {"issuer": ISS}})
    assert r.status_code == 200 and r.json()["error"] == "Unerwarteter Fehler: RuntimeError"
    assert "geheimes Detail" not in r.text


def test_settings_test_ldap_unreachable_host_clean_error(env, monkeypatch):
    """Echter ldap3-Verbindungsversuch gegen einen geschlossenen Port: sauberer Fehlertext statt 500."""
    monkeypatch.setattr(settings, "SSO_ALLOW_INSECURE", True)
    admin = env.add_user("root", role="admin")
    r = env.client().post("/api/v1/settings/sso/test", headers=session_headers(admin), json={
        "target": "ldap", "ldap": {"server_urls": ["ldap://127.0.0.1:1"], "security": "none",
                                   "user_base_dn": "DC=example,DC=com", "timeout": 2}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False and body["error"]
    assert "127.0.0.1" in body["error"] or "nicht erreichbar" in body["error"]


# ---------------------------------------------------------------------------------------------
# Nr. 28 App-Info, Benutzerliste, Notfallzugang, delete_user, Setup
# ---------------------------------------------------------------------------------------------
def test_app_info_registration_follows_local_login(env):
    env.set_settings(registration_enabled=True)
    c = env.client()
    assert c.get("/api/v1/settings/app-info").json()["registration_enabled"] is True
    env.set_settings(sso_local_login_enabled=False)
    assert c.get("/api/v1/settings/app-info").json()["registration_enabled"] is False


def test_list_users_sso_block(env):
    env.set_settings(oidc_role_mode="sync", oidc_jit_enabled=True)
    admin = env.add_user("root", role="admin")
    env.add_user("ext", auth_source="oidc", issuer=ISS, ext_id="sub-ext")
    r = env.client().get("/api/v1/auth/users", headers=session_headers(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["sso"] == {"oidc_role_mode": "sync", "ldap_role_mode": "off", "oidc_jit": True, "ldap_jit": False}
    ext = next(u for u in body["users"] if u["username"] == "ext")
    assert ext["external_issuer"] == ISS and ext["external_id"] == "sub-ext" and ext["auth_source"] == "oidc"
    me = env.client().get("/api/v1/auth/me", headers=session_headers(admin)).json()
    assert "external_id" not in me


def test_last_local_admin_cannot_be_demoted_or_deactivated_with_sso(env):
    env.set_settings(oidc_enabled=True)
    root = env.add_user("root", role="admin")
    other = env.add_user("admin2", role="admin")
    env.add_user("extadmin", role="admin", auth_source="oidc", issuer=ISS, ext_id="a")   # zaehlt nicht
    c, h = env.client(), session_headers(root)
    assert c.put(f"/api/v1/auth/users/{other.id}", headers=h, json={"role": "user"}).status_code == 200
    # root ist jetzt einziger lokaler Admin; der externe Admin kann ihn nicht herabstufen
    ext_admin = env.user_by_name("extadmin")
    r = c.put(f"/api/v1/auth/users/{root.id}", headers=session_headers(ext_admin), json={"is_active": False})
    assert r.status_code == 400 and r.json()["detail"] == "Mindestens ein aktiver lokaler Admin muss als Notfallzugang bestehen bleiben"
    r = c.delete(f"/api/v1/auth/users/{root.id}", headers=session_headers(ext_admin))
    assert r.status_code == 400


def _fake_dyndns(monkeypatch, result=2):
    """services/dyndns.py liefert WS-F9F11-BE parallel – hier ein Test-Double fuer den Aufrufvertrag."""
    import app.services as services_pkg

    calls = []
    mod = types.ModuleType("app.services.dyndns")

    async def delete_tokens_of_user(db, user_id):
        calls.append(user_id)
        return result

    mod.delete_tokens_of_user = delete_tokens_of_user
    monkeypatch.setitem(sys.modules, "app.services.dyndns", mod)
    monkeypatch.setattr(services_pkg, "dyndns", mod, raising=False)
    return calls


def test_delete_user_uses_dyndns_service(env, monkeypatch):
    calls = _fake_dyndns(monkeypatch)
    root = env.add_user("root", role="admin")
    bob = env.add_user("bob")
    r = env.client().delete(f"/api/v1/auth/users/{bob.id}", headers=session_headers(root))
    assert r.status_code == 200, r.text
    assert calls == [bob.id]
    (audit,) = env.audits("USER_DELETE")
    assert audit.details["deleted_dyndns_tokens"] == 2 and audit.details["auth_source"] == "local"
    assert env.user_by_name("bob") is None


def test_delete_last_local_admin_with_sso_400(env, monkeypatch):
    calls = _fake_dyndns(monkeypatch)
    env.set_settings(ldap_enabled=True)
    root = env.add_user("root", role="admin")
    ext_admin = env.add_user("extadmin", role="admin", auth_source="ldap", issuer="ldap", ext_id="g")
    r = env.client().delete(f"/api/v1/auth/users/{root.id}", headers=session_headers(ext_admin))
    assert r.status_code == 400 and calls == []


@pytest.mark.wave_integration
def test_delete_user_with_real_dyndns_service(env):
    """Braucht services/dyndns.delete_tokens_of_user (WS-F9F11-BE, Welle 2 parallel)."""
    from app.models.models import DynDnsToken

    root = env.add_user("root", role="admin")
    bob = env.add_user("bob")
    for i in range(2):
        env.session.add(DynDnsToken(user_id=bob.id, name=f"t{i}", token_prefix=f"p{i}", token_hash=f"h{i}",
                                    hostnames=[], allowed_types=["A"]))
    env.session.commit()
    r = env.client().delete(f"/api/v1/auth/users/{bob.id}", headers=session_headers(root))
    assert r.status_code == 200, r.text
    (audit,) = env.audits("USER_DELETE")
    assert audit.details["deleted_dyndns_tokens"] == 2
    from sqlalchemy import func, select

    assert env.session.execute(select(func.count()).select_from(DynDnsToken)).scalar() == 0


def test_setup_register_uses_complete_login(env, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_REGISTRATION", True)
    r = env.client().post("/api/v1/setup/register", json={
        "username": "erster", "email": "erster@example.com", "password": "Passwort-123", "display_name": "Erster"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["message"] == "Administrator-Account erfolgreich erstellt!"
    assert body["user"]["role"] == "admin" and body["user"]["auth_source"] == "local"
    assert body["user"]["must_change_password"] is False and body["user"]["last_login"]
    assert has_session_cookie(r)
    (audit,) = env.audits("LOGIN")
    assert audit.details["method"] == "setup"


# ---------------------------------------------------------------------------------------------
# Admin-Benachrichtigung (best effort)
# ---------------------------------------------------------------------------------------------
def test_notify_local_admins(env, monkeypatch):
    from contextlib import asynccontextmanager

    from app.core import database
    from app.services import email_service

    env.add_user("root", role="admin", email="root@example.com")
    env.add_user("root2", role="admin", email="root2@example.com")
    env.add_user("ext", role="admin", auth_source="oidc", issuer=ISS, ext_id="a", email="ext@example.com")
    env.add_user("old", role="admin", is_active=False, email="old@example.com")
    env.add_user("bob", email="bob@example.com")

    @asynccontextmanager
    async def fake_session():
        yield SqliteSession(env.session)

    sent = []

    def fake_send(smtp, to, subject, body_html, body_text=None):
        if to == "root2@example.com":
            raise RuntimeError("smtp kaputt")
        sent.append((to, subject, body_text))

    smtp = {"enabled": True, "host": "mail.example.com"}

    async def fake_smtp(db):
        return dict(smtp)

    monkeypatch.setattr(database, "async_session", fake_session)
    monkeypatch.setattr(email_service, "get_smtp_settings", fake_smtp)
    monkeypatch.setattr(email_service, "send_email", fake_send)
    import asyncio
    from datetime import datetime

    n = asyncio.run(settings_sso.notify_local_admins(actor="root", changed_keys=["oidc_issuer"],
                                                     when=datetime(2026, 10, 6, 12, 30)))
    assert n == 1 and [s[0] for s in sent] == ["root@example.com"]
    assert "SSO-Einstellungen geändert von root am 06.10.2026 12:30 UTC" in sent[0][2]
    smtp["enabled"] = False
    assert asyncio.run(settings_sso.notify_local_admins(actor="root", changed_keys=[], when=datetime.now())) == 0


# ---------------------------------------------------------------------------------------------
# Nr. 31 Routen vor dem SPA-Catch-all
# ---------------------------------------------------------------------------------------------
def test_sso_routes_not_shadowed_by_spa(env):
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app

    app.dependency_overrides[get_db] = env.app.dependency_overrides[get_db]
    try:
        c = TestClient(app, raise_server_exceptions=False, follow_redirects=False)
        r = c.get("/api/v1/auth/sso/providers")
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
        r = c.get("/api/v1/auth/oidc/callback")
        assert r.status_code == 303 and r.headers["location"].startswith("/login?sso_error=")
        assert c.get("/api/v1/auth/oidc/start").headers["location"] == "/login?sso_error=disabled"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_router_orders():
    assert sso_router.ROUTER_ORDER == 25 and settings_sso.ROUTER_ORDER == 102
