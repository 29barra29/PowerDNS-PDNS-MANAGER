"""Login-Router: Drosselung je (IP, Benutzername), Login-Abschluss ueber login_session, Metriken (B.3 [S2], B.4, F13 5.11)."""
import pytest
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from app.core import login_rate_limit as lrl
from app.core.auth import create_two_factor_pending_token, hash_password
from app.core.config import settings
from app.routers import auth as auth_router

PASSWORD = "richtig-passwort-1"


@pytest.fixture(autouse=True)
def _fresh_limiter():
    lrl.reset_for_tests()
    yield
    lrl.reset_for_tests()


@pytest.fixture(autouse=True)
def detached_audits(monkeypatch):
    """Fehler-Audits laufen in einer eigenen Session – hier ohne DB einsammeln."""
    from app.services import audit

    calls = []

    async def _fake(action, *a, **kw):
        calls.append((action, kw.get("details")))
        return None

    monkeypatch.setattr(audit, "write_audit_detached", _fake)
    return calls


@pytest.fixture
def metrics(monkeypatch):
    calls = []
    monkeypatch.setattr(auth_router.prom, "record_login", lambda m, r: calls.append((m, r)))
    from app.services import login_session

    monkeypatch.setattr(login_session.prom, "record_login", lambda m, r: calls.append((m, r)))
    return calls


def _client(user):
    session = FakeSession(user_row=user)
    return TestClient(build_app(session, auth_router), raise_server_exceptions=False), session


def _user(**kw):
    u = make_user(role="user", uid=5, username="bob", **kw)
    u.hashed_password = hash_password(PASSWORD)
    return u


def _login(c, password, username="bob"):
    return c.post("/api/v1/auth/login", data={"username": username, "password": password})


def test_locked_username_blocks_before_any_lookup(metrics):
    for i in range(lrl.USER_MAX_FAILS):
        lrl.record_failed_login(f"203.0.113.{i}", "Bob ")  # andere IPs, Schreibweise egal
    c, session = _client(_user())
    r = _login(c, PASSWORD)
    assert r.status_code == 429 and r.json()["detail"] == auth_router.RATE_LIMIT_DETAIL
    assert session.executed == []  # kein Captcha-, DB- oder Passwort-Check
    assert ("password", "rate_limited") in metrics


def test_failures_count_per_username_then_429(metrics):
    c, _ = _client(_user())
    for _ in range(lrl.USER_MAX_FAILS):
        assert _login(c, "falsch").status_code == 401
    assert _login(c, PASSWORD).status_code == 429
    assert metrics.count(("password", "failure")) == lrl.USER_MAX_FAILS


def test_failed_login_writes_detached_audit(detached_audits):
    c, _ = _client(_user())
    assert _login(c, "falsch").status_code == 401
    assert detached_audits and detached_audits[0][0] == "LOGIN_FAILED"
    assert detached_audits[0][1]["reason"] == "bad_credentials"


def test_success_uses_complete_login_and_clears_only_pair(metrics):
    user = _user()
    c, session = _client(user)
    for _ in range(3):
        _login(c, "falsch")
    r = _login(c, PASSWORD)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["username"] == "bob" and r.json()["user"]["auth_source"] == "local"
    assert settings.AUTH_COOKIE_NAME in r.headers.get("set-cookie", "")
    assert lrl._user_limiter.count("bob") == 0
    assert lrl._ip_limiter.count(lrl._ip_counter_key("testclient")) == 3  # IP-Zaehler bleibt
    audits = [o for o in session.added if getattr(o, "action", None) == "LOGIN"]
    assert audits and audits[0].details["method"] == "password"
    assert ("password", "success") in metrics
    assert user.last_login is not None


def test_inactive_and_two_factor_required_metrics(metrics):
    u = _user()
    u.is_active = False
    c, _ = _client(u)
    assert _login(c, PASSWORD).status_code == 403
    assert ("password", "denied") in metrics
    u2 = _user(totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
    c, _ = _client(u2)
    r = _login(c, PASSWORD)
    assert r.status_code == 200 and r.json()["need_two_factor"] is True
    assert ("password", "2fa_required") in metrics


def test_two_factor_step_counts_user_and_respects_user_lock(metrics):
    u = _user(totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
    c, _ = _client(u)
    pending = create_two_factor_pending_token(5)
    r = c.post("/api/v1/auth/login/2fa", json={"two_factor_token": pending, "totp_code": "000000"})
    assert r.status_code == 401
    assert lrl._user_limiter.count("bob") == 1 and ("totp", "failure") in metrics
    for _ in range(lrl.USER_MAX_FAILS):
        lrl.record_failed_login("198.51.100.9", "bob")
    r = c.post("/api/v1/auth/login/2fa", json={"two_factor_token": pending, "totp_code": "000000"})
    assert r.status_code == 429 and ("totp", "rate_limited") in metrics


def test_aliases_point_to_login_session():
    from app.services import login_session

    assert auth_router._user_to_dict is login_session.user_to_dict
    assert auth_router._set_session_cookie is login_session.set_session_cookie
    assert auth_router._complete_login is login_session.complete_login
