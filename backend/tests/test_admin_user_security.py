"""F3: Admin-Benutzersicherheit ohne Datenbank (Spec F2/F3 9.1 Nr. 5–21 + Bauplan-Erweiterungen [S9]).

Nr. 1–4 (Gate "Passwortwechsel erforderlich") liegen unveraendert in ``tests/test_password_change_gate.py``
(W0-INT-BE2a), Nr. 21 (Route-Walk Browser-Session) in ``tests/test_release_fixes.py``, Nr. 22–26 in
``tests/test_zone_notify_export.py``. Bauplan-Abweichung zu Nr. 5: die Option heisst ``revoke_all_access`` und
prueft ``revoked_at`` statt ``is_active`` (``access_revocation.revoke_all``, [S9]).

Muster: Handler direkt aufrufen, ``UserDB`` (In-Memory-Session aus ``f2f3fakes``) als Datenbank, ``write_audit``
per ``monkeypatch`` auf einen Sammler.
"""
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from f2f3fakes import AuditSink, PanelToken, UserDB, http_request, make_user
from app.models.models import WebAuthnCredential
from app.routers import auth as auth_router
from app.routers.auth import (
    AdminPasswordResetBody, ForgotPasswordRequest, PasswordChange, ProfileUpdate, ResetPasswordRequest,
    RevokeAccessBody, UserCreate, UserUpdate,
)
from app.services import password_reset_mail as prm
from app.services import user_guard


@pytest.fixture
def audit(monkeypatch):
    sink = AuditSink()
    monkeypatch.setattr(auth_router, "write_audit", sink)
    return sink


@pytest.fixture(autouse=True)
def _clear_reset_link_limiter():
    auth_router._RESET_LINK_LAST.clear()
    yield
    auth_router._RESET_LINK_LAST.clear()


def _admin(uid: int = 1, **kw):
    return make_user(uid, "chef", role="admin", **kw)


def _token(tid: int, user_id: int, *, active: bool = True) -> PanelToken:
    return PanelToken(id=tid, user_id=user_id, name=f"t{tid}", token_prefix="dnsmgr_usr_x", token_hash=f"h{tid}",
                      is_active=active, permission="manage", allow_admin=False, revoked_at=None)


def _cred(cid: int, user_id: int, name: str) -> WebAuthnCredential:
    return WebAuthnCredential(id=cid, user_id=user_id, name=name, credential_id=f"c{cid}", public_key="pk",
                              sign_count=0)


async def _raises(coro, status: int, detail: str | None = None) -> HTTPException:
    with pytest.raises(HTTPException) as e:
        await coro
    assert e.value.status_code == status, e.value.detail
    if detail is not None:
        assert e.value.detail == detail
    return e.value


# --- Nr. 5 ---------------------------------------------------------------------------------------------------
async def test_reset_user_password_defaults(audit):
    admin, user = _admin(), make_user(2, "anna")
    db = UserDB([admin, user])
    res = await auth_router.reset_user_password(db, 2, None, admin)
    assert user.must_change_password is True and res["must_change_password"] is True
    assert len(res["new_password"]) == 16 and res["revoked"] is None
    call = audit.one("USER_PASSWORD_RESET")
    assert call["user_id"] == 1 and call["details"] == {"target_user_id": 2, "must_change_password": True}
    assert res["new_password"] not in repr(audit.calls)
    assert not db.updates_on("dyndns_tokens")  # ohne Option kein Widerruf


async def test_reset_user_password_revoke_all_access_sets_revoked_at(audit):
    admin, user = _admin(), make_user(2, "anna")
    tokens = [_token(10, 2), _token(11, 2, active=False)]  # auch pausierte Tokens werden endgueltig widerrufen
    db = UserDB([admin, user], tokens=tokens,
                rowcounts={"dyndns_tokens": 1, "webhooks": 2, "webhook_deliveries": 3})
    res = await auth_router.reset_user_password(
        db, 2, AdminPasswordResetBody(must_change_password=False, revoke_all_access=True), admin)
    assert user.must_change_password is False
    assert all(t.revoked_at is not None and t.is_active is False for t in tokens)
    expected = {"panel_tokens": 2, "dyndns_tokens": 1, "webhooks": 2, "cancelled_deliveries": 3}
    assert res["revoked"] == expected
    assert audit.one("USER_PASSWORD_RESET")["details"]["revoked"] == expected
    assert len(db.updates_on("dyndns_tokens")) == 1 and len(db.updates_on("webhooks")) == 1
    (upd,) = db.updates_on("webhook_deliveries")
    sql = str(upd)
    assert "status IN" in sql and upd.compile().params["status"] == "cancelled"


# --- Nr. 6 ---------------------------------------------------------------------------------------------------
async def test_reset_user_password_self_forbidden(audit):
    admin = _admin()
    await _raises(auth_router.reset_user_password(UserDB([admin]), 1, None, admin), 400,
                  auth_router.OWN_PASSWORD_DETAIL)
    assert audit.calls == []


async def test_reset_user_password_external_account_rejected(audit):
    admin, ext = _admin(), make_user(2, "sso", auth_source="oidc")
    await _raises(auth_router.reset_user_password(UserDB([admin, ext]), 2, None, admin), 400,
                  user_guard.EXTERNAL_ACCOUNT_DETAIL)


# --- Nr. 7 ---------------------------------------------------------------------------------------------------
async def test_create_user_email_duplicate_409(audit):
    admin = _admin()
    db = UserDB([admin, make_user(2, "anna", email="anna@example.org")])
    data = UserCreate(username="bert", password="passwort-123", email="anna@example.org")
    await _raises(auth_router.create_user(db, data, admin), 409, "E-Mail wird bereits verwendet")
    assert db.added == [] and audit.calls == []


async def test_create_user_integrity_error_409(audit):
    admin = _admin()
    db = UserDB([admin], flush_error=IntegrityError("INSERT", {}, Exception("Duplicate entry")))
    data = UserCreate(username="bert", password="passwort-123", email="bert@example.org")
    await _raises(auth_router.create_user(db, data, admin), 409, "Benutzername oder E-Mail wird bereits verwendet")
    assert audit.calls == []


async def test_create_user_username_duplicate_stays_400_and_flag_is_stored(audit):
    admin = _admin()
    db = UserDB([admin, make_user(2, "anna")])
    await _raises(auth_router.create_user(db, UserCreate(username="anna", password="passwort-123"), admin), 400,
                  "Benutzername existiert bereits")
    out = await auth_router.create_user(
        db, UserCreate(username="bert", password="passwort-123", must_change_password=True), admin)
    assert out["must_change_password"] is True and db.added[0].must_change_password is True
    assert audit.one("USER_CREATE")["details"]["must_change_password"] is True


# --- Nr. 8 ---------------------------------------------------------------------------------------------------
async def test_update_user_email_duplicate_409(audit):
    admin = _admin()
    db = UserDB([admin, make_user(2, "anna", email="anna@example.org"), make_user(3, "bert")])
    await _raises(auth_router.update_user(db, 3, UserUpdate(email="anna@example.org"), admin), 409,
                  "E-Mail wird bereits verwendet")
    # eigene, unveraenderte Adresse ist kein Duplikat
    out = await auth_router.update_user(db, 2, UserUpdate(email="anna@example.org", display_name="A"), admin)
    assert out["email"] == "anna@example.org"


async def test_update_user_integrity_error_409(audit):
    admin, user = _admin(), make_user(2, "anna")
    db = UserDB([admin, user], flush_error=IntegrityError("UPDATE", {}, Exception("Duplicate entry")))
    await _raises(auth_router.update_user(db, 2, UserUpdate(email="neu@example.org"), admin), 409,
                  "E-Mail wird bereits verwendet")
    assert audit.calls == []


# --- Nr. 9 ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("data, detail", [
    (UserUpdate(password="passwort-123"), "Das eigene Passwort bitte unter Einstellungen ändern"),
    (UserUpdate(must_change_password=True), "Für das eigene Konto kann kein Passwortwechsel erzwungen werden"),
    (UserUpdate(is_active=False), "Du kannst dich nicht selbst deaktivieren"),
    (UserUpdate(role="user"), "Du kannst dir die Admin-Rolle nicht selbst entziehen"),
    (UserUpdate(revoke_all_access=True), auth_router.OWN_ACCESS_DETAIL),
])
async def test_update_user_self_guards(audit, data, detail):
    admin, other = _admin(), _admin(2)  # zweiter aktiver Admin: der Letzter-Admin-Schutz greift hier nicht
    db = UserDB([admin, other])
    await _raises(auth_router.update_user(db, 1, data, admin), 400, detail)
    assert admin.role == "admin" and admin.is_active is True and audit.calls == []


async def test_update_user_sets_flag_and_revokes(audit):
    admin, user = _admin(), make_user(2, "anna")
    tokens = [_token(10, 2)]
    db = UserDB([admin, user], tokens=tokens, rowcounts={"webhooks": 1})
    out = await auth_router.update_user(
        db, 2, UserUpdate(password="passwort-123", must_change_password=True, revoke_all_access=True), admin)
    assert user.must_change_password is True and tokens[0].revoked_at is not None
    assert out["revoked"]["panel_tokens"] == 1 and out["revoked"]["webhooks"] == 1
    details = audit.one("USER_UPDATE")["details"]
    assert details["changed"]["password"] == "set_by_admin"
    assert details["changed"]["must_change_password"] == {"from": False, "to": True}
    assert details["revoked"]["panel_tokens"] == 1


async def test_update_user_password_keeps_flag_when_not_sent(audit):
    admin, user = _admin(), make_user(2, "anna")
    user.must_change_password = True
    await auth_router.update_user(UserDB([admin, user]), 2, UserUpdate(password="passwort-123"), admin)
    assert user.must_change_password is True


async def test_update_user_password_on_external_account_rejected(audit):
    admin, ext = _admin(), make_user(2, "ldapuser", auth_source="ldap")
    await _raises(auth_router.update_user(UserDB([admin, ext]), 2, UserUpdate(password="passwort-123"), admin),
                  400, user_guard.EXTERNAL_ACCOUNT_DETAIL)
    # Profilfelder bleiben fuer externe Konten aenderbar
    await auth_router.update_user(UserDB([admin, ext]), 2, UserUpdate(display_name="L"), admin)


async def test_update_user_last_active_admin_via_api(audit):
    """Handler-Pfad: inaktive Admins zaehlen nicht. Die Datenbank kennt hier nur inaktive weitere Admins (der
    aufrufende Admin ist bewusst nicht als aktive Zeile hinterlegt – ueber die echte API ist der Fall nur so
    konstruierbar, Spec 9.2 Nr. 4)."""
    target = _admin(2)
    db = UserDB([_admin(1, is_active=False), _admin(3, is_active=False), target])
    await _raises(auth_router.update_user(db, 2, UserUpdate(is_active=False), _admin()), 400,
                  user_guard.LAST_ADMIN_DEACTIVATE_DETAIL)
    await _raises(auth_router.update_user(db, 2, UserUpdate(role="user"), _admin()), 400,
                  user_guard.LAST_ADMIN_DEMOTE_DETAIL)
    assert target.is_active is True and target.role == "admin" and audit.calls == []


# --- Nr. 10 --------------------------------------------------------------------------------------------------
class _CountDB:
    def __init__(self, n: int):
        self.n = n
        self.statements = []

    async def execute(self, stmt, *a, **k):
        from f2f3fakes import Result

        self.statements.append(stmt)
        return Result([self.n])


async def test_assert_keeps_active_admin():
    target = _admin(5)
    # (a) letzter aktiver Admin deaktivieren
    await _raises(user_guard.assert_keeps_active_admin(_CountDB(0), target, new_role="admin", new_active=False),
                  400, "Der letzte aktive Admin kann nicht deaktiviert werden")
    # (b) herabstufen
    await _raises(user_guard.assert_keeps_active_admin(_CountDB(0), target, new_role="user", new_active=True),
                  400, "Letzter aktiver Admin kann nicht herabgestuft werden")
    # (c) zweiter aktiver Admin vorhanden
    await user_guard.assert_keeps_active_admin(_CountDB(1), target, new_role="user", new_active=False)
    # (d) die Zaehlung filtert auf is_active und schliesst das Ziel aus (ein inaktiver Admin zaehlt nicht)
    db = _CountDB(0)
    with pytest.raises(HTTPException):
        await user_guard.assert_keeps_active_admin(db, target, new_role="user", new_active=True)
    sql = str(db.statements[0])
    assert "users.is_active" in sql and "users.role" in sql and "users.id !=" in sql
    # (e) Ziel ist kein Admin bzw. bleibt aktiver Admin -> keine Abfrage
    db = _CountDB(0)
    await user_guard.assert_keeps_active_admin(db, make_user(6, "u"), new_role="user", new_active=False)
    await user_guard.assert_keeps_active_admin(db, target, new_role="admin", new_active=True)
    await user_guard.assert_keeps_active_admin(db, _admin(7, is_active=False), new_role="user", new_active=False)
    assert db.statements == []


# --- Nr. 11 --------------------------------------------------------------------------------------------------
async def test_reset_2fa_clears_and_audits(audit):
    admin = _admin()
    user = make_user(2, "anna", totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
    res = await auth_router.reset_user_2fa(UserDB([admin, user]), 2, admin)
    assert res["changed"] is True and res["user"]["totp_enabled"] is False
    assert user.totp_secret is None and user.totp_pending_secret is None and user.totp_enabled is False
    assert audit.one("USER_2FA_RESET")["details"] == {"target_user_id": 2, "had_totp": True, "had_pending": False}


async def test_reset_2fa_works_for_unreadable_secret(audit):
    from app.core.secrets import UNREADABLE

    admin = _admin()
    user = make_user(2, "anna", totp_enabled=True)
    user.totp_secret = UNREADABLE
    res = await auth_router.reset_user_2fa(UserDB([admin, user]), 2, admin)
    assert res["changed"] is True and user.totp_secret is None


async def test_reset_2fa_noop_without_audit(audit):
    admin, user = _admin(), make_user(2, "anna")
    res = await auth_router.reset_user_2fa(UserDB([admin, user]), 2, admin)
    assert res["changed"] is False and res["message"] == "2FA war nicht aktiv" and audit.calls == []


async def test_reset_2fa_pending_only_and_self(audit):
    admin, user = _admin(), make_user(2, "anna", totp_pending_secret="JBSWY3DPEHPK3PXP")
    res = await auth_router.reset_user_2fa(UserDB([admin, user]), 2, admin)
    assert res["changed"] is True and audit.one("USER_2FA_RESET")["details"]["had_pending"] is True
    await _raises(auth_router.reset_user_2fa(UserDB([admin]), 1, admin), 400, auth_router.OWN_2FA_DETAIL)
    await _raises(auth_router.reset_user_2fa(UserDB([admin]), 99, admin), 404, "Benutzer nicht gefunden")


# --- Nr. 12 --------------------------------------------------------------------------------------------------
async def test_delete_user_passkeys_counts_and_audits(audit):
    admin, user = _admin(), make_user(2, "anna")
    creds = [_cred(i, 2, ("n" * 150) if i == 0 else f"key{i}") for i in range(25)]
    db = UserDB([admin, user], creds=creds)
    res = await auth_router.delete_user_passkeys(db, 2, admin)
    assert res["deleted"] == 25 and res["message"] == "25 Passkey(s) von 'anna' entfernt"
    details = audit.one("USER_PASSKEYS_RESET")["details"]
    assert details["deleted"] == 25 and len(details["names"]) == 20 and len(details["names"][0]) == 100


async def test_delete_user_passkeys_zero_and_self(audit):
    admin, user = _admin(), make_user(2, "anna")
    res = await auth_router.delete_user_passkeys(UserDB([admin, user]), 2, admin)
    assert res["deleted"] == 0 and audit.calls == []
    await _raises(auth_router.delete_user_passkeys(UserDB([admin]), 1, admin), 400,
                  auth_router.OWN_PASSKEYS_DETAIL)


# --- Nr. 13 --------------------------------------------------------------------------------------------------
async def test_send_reset_link_errors(audit, monkeypatch):
    admin = _admin()
    no_mail = make_user(2, "anna")
    inactive = make_user(3, "bert", email="bert@example.org", is_active=False)
    ok = make_user(4, "cleo", email="cleo@example.org")
    db = UserDB([admin, no_mail, inactive, ok])
    await _raises(auth_router.send_user_reset_link(db, 2, admin), 400,
                  "Für diesen Benutzer ist keine E-Mail-Adresse hinterlegt")
    await _raises(auth_router.send_user_reset_link(db, 3, admin), 400, "Benutzer ist deaktiviert")
    await _raises(auth_router.send_user_reset_link(db, 1, admin), 400, auth_router.OWN_PASSWORD_DETAIL)

    async def smtp_disabled(*a, **k):
        raise prm.ResetMailError("smtp_disabled")

    monkeypatch.setattr(auth_router, "send_password_reset_mail", smtp_disabled)
    await _raises(auth_router.send_user_reset_link(db, 4, admin), 400,
                  "E-Mail-Versand ist nicht eingerichtet (Einstellungen → SMTP)")
    assert 4 not in auth_router._RESET_LINK_LAST  # Konfigurationsfehler -> keine Wartezeit

    async def no_base(*a, **k):
        raise prm.ResetMailError("no_base_url")

    monkeypatch.setattr(auth_router, "send_password_reset_mail", no_base)
    await _raises(auth_router.send_user_reset_link(db, 4, admin), 400,
                  "Keine öffentliche Basis-URL konfiguriert (Einstellungen → Profil → Öffentliche Basis-URL)")
    assert audit.calls == []

    async def send_failed(*a, **k):
        raise prm.ResetMailError("send_failed")

    monkeypatch.setattr(auth_router, "send_password_reset_mail", send_failed)
    await _raises(auth_router.send_user_reset_link(db, 4, admin), 502,
                  "E-Mail konnte nicht gesendet werden – Details im Server-Log")
    call = audit.one("USER_PASSWORD_RESET_LINK")
    assert call["status"] == "error" and call["error_message"] == "send_failed"
    assert 4 in auth_router._RESET_LINK_LAST  # Sendefehler zaehlt fuer die Sperre


async def test_send_reset_link_success_and_external(audit, monkeypatch):
    admin = _admin()
    ok = make_user(4, "cleo", email="cleo@example.org")
    ext = make_user(5, "sso", email="sso@example.org", auth_source="oidc")
    seen = {}

    async def fake_send(db, user, *, valid_minutes, admin_initiated):
        seen.update(user=user.id, valid_minutes=valid_minutes, admin_initiated=admin_initiated)

    monkeypatch.setattr(auth_router, "send_password_reset_mail", fake_send)
    db = UserDB([admin, ok, ext])
    res = await auth_router.send_user_reset_link(db, 4, admin)
    assert res == {"message": "Reset-Link an cleo@example.org gesendet", "email": "cleo@example.org",
                   "valid_hours": 24}
    assert seen == {"user": 4, "valid_minutes": 1440, "admin_initiated": True}
    assert audit.one("USER_PASSWORD_RESET_LINK")["details"] == {
        "target_user_id": 4, "email": "cleo@example.org", "valid_hours": 24}
    await _raises(auth_router.send_user_reset_link(db, 5, admin), 400, user_guard.EXTERNAL_ACCOUNT_DETAIL)


# --- Nr. 14 --------------------------------------------------------------------------------------------------
async def test_send_reset_link_rate_limit(audit, monkeypatch):
    calls = []

    async def fake_send(db, user, **kw):
        calls.append(user.id)

    monkeypatch.setattr(auth_router, "send_password_reset_mail", fake_send)
    admin, ok = _admin(), make_user(4, "cleo", email="cleo@example.org")
    db = UserDB([admin, ok])
    await auth_router.send_user_reset_link(db, 4, admin)
    await _raises(auth_router.send_user_reset_link(db, 4, admin), 429,
                  "Für diesen Benutzer wurde gerade erst ein Link gesendet – bitte eine Minute warten")
    assert calls == [4]
    # nach Ablauf des Intervalls wieder erlaubt (Aufraeumen beim Zugriff)
    auth_router._RESET_LINK_LAST[4] -= 61
    await auth_router.send_user_reset_link(db, 4, admin)
    assert calls == [4, 4]


# --- Nr. 15 --------------------------------------------------------------------------------------------------
async def test_send_password_reset_mail_builds_url_from_setting(monkeypatch):
    sent = []

    async def fake_threadpool(fn, *args):
        sent.append((fn, args))

    monkeypatch.setattr(prm, "run_in_threadpool", fake_threadpool)
    smtp = {"enabled": True, "host": "smtp.example", "port": 587}

    async def fake_smtp(db):
        return dict(smtp)

    monkeypatch.setattr(prm, "get_smtp_settings", fake_smtp)
    user = make_user(7, "dora", email="dora@example.org", preferred_language="en")
    db = UserDB([user], settings={"app_base_url": "https://dns.example/"})
    await prm.send_password_reset_mail(db, user, valid_minutes=1440, admin_initiated=True)
    (fn, (smtp_arg, to, subject, body_html, body_text)), = sent
    assert fn is prm.send_email and to == "dora@example.org"
    assert "https://dns.example/reset-password?token=" in body_text and "24 hours" in body_text

    # ohne Basis-URL (kein Setting, kein WEBAUTHN_ORIGIN) -> no_base_url, nie Host-Header
    monkeypatch.setattr(prm.app_settings, "WEBAUTHN_ORIGIN", "")
    with pytest.raises(prm.ResetMailError) as e:
        await prm.send_password_reset_mail(UserDB([user]), user)
    assert e.value.code == "no_base_url"
    # WEBAUTHN_ORIGIN als Fallback (erster Eintrag)
    monkeypatch.setattr(prm.app_settings, "WEBAUTHN_ORIGIN", "https://panel.example, https://alt.example")
    assert await prm.resolve_public_base_url(UserDB([user])) == "https://panel.example"
    # ohne SMTP -> smtp_disabled
    smtp["enabled"] = False
    with pytest.raises(prm.ResetMailError) as e:
        await prm.send_password_reset_mail(db, user)
    assert e.value.code == "smtp_disabled"
    # ohne E-Mail -> no_email
    with pytest.raises(prm.ResetMailError) as e:
        await prm.send_password_reset_mail(db, make_user(8, "ohne"))
    assert e.value.code == "no_email"


async def test_send_password_reset_mail_send_failed(monkeypatch):
    async def boom(fn, *args):
        raise OSError("connection refused")

    async def fake_smtp(db):
        return {"enabled": True, "host": "smtp.example"}

    monkeypatch.setattr(prm, "run_in_threadpool", boom)
    monkeypatch.setattr(prm, "get_smtp_settings", fake_smtp)
    user = make_user(7, "dora", email="dora@example.org")
    with pytest.raises(prm.ResetMailError) as e:
        await prm.send_password_reset_mail(UserDB([user], settings={"app_base_url": "https://dns.example"}), user)
    assert e.value.code == "send_failed"


async def test_reset_mail_available(monkeypatch):
    async def smtp_on(db):
        return {"enabled": True, "host": "smtp.example"}

    monkeypatch.setattr(prm, "get_smtp_settings", smtp_on)
    monkeypatch.setattr(prm.app_settings, "WEBAUTHN_ORIGIN", "")
    assert await prm.reset_mail_available(UserDB(settings={"app_base_url": "https://dns.example"})) is True
    assert await prm.reset_mail_available(UserDB()) is False


# --- Nr. 16 --------------------------------------------------------------------------------------------------
def test_reset_token_ttl():
    from app.core.auth import create_password_reset_token, decode_token

    now = datetime.now(timezone.utc).timestamp()
    long_exp = decode_token(create_password_reset_token(1, "h", expires_minutes=1440))["exp"]
    short_exp = decode_token(create_password_reset_token(1, "h"))["exp"]
    assert abs(long_exp - (now + 24 * 3600)) < 30
    assert abs(short_exp - (now + 3600)) < 30


# --- Nr. 17 --------------------------------------------------------------------------------------------------
async def test_forgot_password_generic_on_mail_error(monkeypatch):
    async def no_captcha(*a, **k):
        return None

    monkeypatch.setattr("app.services.captcha.verify_or_raise", no_captcha)
    user = make_user(2, "anna", email="anna@example.org")
    db = UserDB([user], settings={"forgot_password_enabled": "true"})
    sent = []

    async def ok(db_, u, **kw):
        sent.append((u.id, kw))

    monkeypatch.setattr(auth_router, "send_password_reset_mail", ok)
    good = await auth_router.forgot_password(db, ForgotPasswordRequest(username="anna"), http_request())
    assert sent == [(2, {"valid_minutes": 60, "admin_initiated": False})]

    async def fail(*a, **k):
        raise prm.ResetMailError("send_failed")

    monkeypatch.setattr(auth_router, "send_password_reset_mail", fail)
    bad = await auth_router.forgot_password(db, ForgotPasswordRequest(username="anna"), http_request())
    unknown = await auth_router.forgot_password(db, ForgotPasswordRequest(username="niemand"), http_request())
    assert good == bad == unknown == auth_router.FORGOT_PASSWORD_REPLY

    # externe Konten bekommen keinen Link (gleiche Antwort)
    ext = make_user(3, "sso", email="sso@example.org", auth_source="oidc")
    sent.clear()
    monkeypatch.setattr(auth_router, "send_password_reset_mail", ok)
    res = await auth_router.forgot_password(UserDB([ext], settings={"forgot_password_enabled": "true"}),
                                            ForgotPasswordRequest(username="sso"), http_request())
    assert res == auth_router.FORGOT_PASSWORD_REPLY and sent == []


# --- Nr. 18 --------------------------------------------------------------------------------------------------
def test_password_reset_template_default_unchanged():
    from app.services.email_templates import password_reset

    url = "https://dns.example/reset-password?token=abc"
    assert password_reset("de", "Anna", url) == (
        "Passwort zuruecksetzen - PDNS Manager",
        "<p>Hallo Anna,</p>"
        "<p>du hast eine Zuruecksetzung deines Passworts angefordert.</p>"
        "<p>Klicke auf den folgenden Link, um ein neues Passwort zu setzen (der Link ist 1 Stunde gueltig):</p>"
        f'<p><a href="{url}">{url}</a></p>'
        "<p>Falls du das nicht warst, kannst du diese E-Mail einfach ignorieren.</p>",
        f"Passwort zuruecksetzen: {url}",
    )
    assert password_reset("en", "Anna", url) == (
        "Reset your password - PDNS Manager",
        "<p>Hi Anna,</p>"
        "<p>You requested a password reset.</p>"
        "<p>Click the following link to set a new password (the link is valid for 1 hour):</p>"
        f'<p><a href="{url}">{url}</a></p>'
        "<p>If this wasn't you, you can simply ignore this email.</p>",
        f"Reset your password: {url}",
    )
    _, html_de, text_de = password_reset("de", "Anna", url, valid_hours=24, admin_initiated=True)
    assert "24 Stunden" in html_de and "24 Stunden" in text_de and "Administrator" in html_de
    assert "einfach ignorieren" not in html_de and url in text_de
    _, html_en, text_en = password_reset("en", "<b>", url, valid_hours=24, admin_initiated=True)
    assert "24 hours" in html_en and "24 hours" in text_en and "&lt;b&gt;" in html_en
    assert "contact your administrator" in text_en


# --- Nr. 19 --------------------------------------------------------------------------------------------------
async def test_change_password_clears_flag_and_rejects_same(audit):
    import json

    user = make_user(2, "anna", password="altes-passwort-1")
    user.must_change_password = True
    db = UserDB([user])
    same = await auth_router.change_password(
        db, PasswordChange(current_password="altes-passwort-1", new_password="altes-passwort-1"), user)
    assert same.status_code == 400
    assert json.loads(same.body) == {"detail": "Das neue Passwort muss sich vom aktuellen unterscheiden",
                                     "code": "password_unchanged"}
    wrong = await auth_router.change_password(
        db, PasswordChange(current_password="falsch-falsch", new_password="neues-passwort-1"), user)
    assert wrong.status_code == 400 and json.loads(wrong.body)["code"] == "current_password_wrong"
    assert user.must_change_password is True and audit.calls == []

    ok = await auth_router.change_password(
        db, PasswordChange(current_password="altes-passwort-1", new_password="neues-passwort-1"), user)
    assert ok.status_code == 200 and "set-cookie" in ok.headers
    assert user.must_change_password is False
    assert audit.one("PASSWORD_CHANGE")["details"] == {"forced": True}


async def test_change_password_external_account_rejected(audit):
    ext = make_user(2, "sso", auth_source="ldap")
    await _raises(auth_router.change_password(
        UserDB([ext]), PasswordChange(current_password="x", new_password="neues-passwort-1"), ext), 400,
        user_guard.EXTERNAL_ACCOUNT_DETAIL)


# --- Nr. 20 --------------------------------------------------------------------------------------------------
async def test_public_reset_password_clears_flag_and_audits(audit):
    from app.core.auth import create_password_reset_token

    user = make_user(2, "anna")
    user.must_change_password = True
    token = create_password_reset_token(2, user.hashed_password, expires_minutes=1440)
    db = UserDB([user])
    res = await auth_router.reset_password(db, ResetPasswordRequest(token=token, new_password="neues-passwort-1"),
                                           http_request(ip="198.51.100.7"))
    assert res["message"].startswith("Passwort wurde geändert")
    assert user.must_change_password is False
    call = audit.one("PASSWORD_RESET")
    assert call["user_id"] == 2 and call["details"] == {"ip": "198.51.100.7", "via": "link"}
    # Einmal-Nutzung (pwv): derselbe Link ist danach wertlos
    await _raises(auth_router.reset_password(db, ResetPasswordRequest(token=token, new_password="noch-eins-123"),
                                             http_request()), 400)


# --- [S9] Zugangs-Widerruf und Uebersicht ---------------------------------------------------------------------
async def test_revoke_user_access_all_options(audit):
    admin = _admin()
    user = make_user(2, "anna", totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
    tokens = [_token(10, 2)]
    db = UserDB([admin, user], tokens=tokens, creds=[_cred(1, 2, "yubi")],
                rowcounts={"dyndns_tokens": 2, "webhooks": 1, "webhook_deliveries": 4})
    res = await auth_router.revoke_user_access(
        db, 2, RevokeAccessBody(reset_2fa=True, remove_passkeys=True), admin)
    assert res["revoked"] == {"panel_tokens": 1, "dyndns_tokens": 2, "webhooks": 1, "cancelled_deliveries": 4}
    assert res["totp_reset"] is True and res["passkeys_removed"] == 1
    assert tokens[0].revoked_at is not None and user.totp_enabled is False
    assert audit.actions() == ["USER_2FA_RESET", "USER_PASSKEYS_RESET", "USER_ACCESS_REVOKE"]
    details = audit.one("USER_ACCESS_REVOKE")["details"]
    assert details["revoked"]["panel_tokens"] == 1 and details["totp_reset"] is True
    assert set(res["summary"]) >= {"panel_tokens", "dyndns_tokens", "webhooks"}


async def test_revoke_user_access_selected_only_and_guards(audit):
    admin, user = _admin(), make_user(2, "anna")
    tokens = [_token(10, 2)]
    db = UserDB([admin, user], tokens=tokens, rowcounts={"webhooks": 3})
    res = await auth_router.revoke_user_access(
        db, 2, RevokeAccessBody(panel_tokens=False, dyndns_tokens=False, webhooks=True), admin)
    assert res["revoked"]["webhooks"] == 3 and res["revoked"]["panel_tokens"] == 0
    assert tokens[0].revoked_at is None and not db.updates_on("dyndns_tokens")
    nothing = RevokeAccessBody(panel_tokens=False, dyndns_tokens=False, webhooks=False)
    await _raises(auth_router.revoke_user_access(db, 2, nothing, admin), 400, "Nichts zum Widerrufen ausgewählt")
    await _raises(auth_router.revoke_user_access(db, 1, None, admin), 400, auth_router.OWN_ACCESS_DETAIL)
    await _raises(auth_router.revoke_user_access(db, 99, None, admin), 404)


async def test_access_summary_handler():
    admin, user = _admin(), make_user(2, "anna", totp_enabled=True)
    db = UserDB([admin, user], creds=[_cred(1, 2, "a"), _cred(2, 2, "b")],
                counts={"panel_tokens": 3, "dyndns_tokens": 1, "webhooks": 2, "webhook_deliveries": 5})
    res = await auth_router.get_user_access_summary(db, 2, admin)
    assert res == {"user_id": 2, "panel_tokens": 3, "dyndns_tokens": 1, "webhooks": 2, "pending_deliveries": 5,
                   "passkeys": 2, "totp_enabled": True}


async def test_list_users_counts_and_mail_flag(monkeypatch):
    async def avail(db):
        return True

    monkeypatch.setattr(auth_router, "reset_mail_available", avail)
    admin, user = _admin(), make_user(2, "anna")
    db = UserDB([admin, user], tokens=[_token(1, 2), _token(2, 2), _token(3, 2, active=False)],
                creds=[_cred(1, 1, "a")])
    res = await auth_router.list_users(db, admin)
    by_name = {u["username"]: u for u in res["users"]}
    assert by_name["chef"]["passkey_count"] == 1 and by_name["anna"]["passkey_count"] == 0
    assert by_name["anna"]["panel_token_count"] == 2 and by_name["chef"]["panel_token_count"] == 0
    assert res["password_reset_mail_available"] is True
    assert by_name["anna"]["must_change_password"] is False


# --- F5 5.13: 2FA-Geheimnis unlesbar --------------------------------------------------------------------------
def test_totp_secret_state():
    from app.core.secrets import UNREADABLE

    u = make_user(2, "anna", totp_enabled=True, totp_secret="JBSWY3DPEHPK3PXP")
    assert auth_router._totp_secret_state(u) == "ok"
    u.totp_secret = "  "
    assert auth_router._totp_secret_state(u) == "missing"
    u.totp_secret = UNREADABLE
    assert auth_router._totp_secret_state(u) == "unreadable"


async def test_login_with_unreadable_totp_secret_is_503(audit, monkeypatch):
    from fastapi.security import OAuth2PasswordRequestForm
    from app.core.secrets import UNREADABLE

    async def no_captcha(*a, **k):
        return None

    monkeypatch.setattr("app.services.captcha.verify_or_raise", no_captcha)
    user = make_user(2, "anna", password="passwort-123", totp_enabled=True)
    user.totp_secret = UNREADABLE
    form = OAuth2PasswordRequestForm(username="anna", password="passwort-123")
    err = await _raises(auth_router.login(UserDB([user]), http_request(ip="203.0.113.50"), form, None, None), 503,
                        auth_router.TOTP_UNREADABLE_LOGIN)
    assert err.detail.startswith("2FA-Geheimnis kann nicht entschlüsselt werden")
    call = audit.one("LOGIN_FAILED")
    assert call["status"] == "error" and call["details"]["reason"] == "totp_unreadable"


async def test_totp_disable_and_status_with_unreadable_secret(audit):
    from app.core.secrets import UNREADABLE
    from app.routers.auth import TotpDisableBody

    user = make_user(2, "anna", password="passwort-123", totp_enabled=True)
    user.totp_secret = UNREADABLE
    await _raises(auth_router.totp_disable(UserDB([user]), TotpDisableBody(password="passwort-123", code="123456"),
                                           user), 409, auth_router.TOTP_UNREADABLE_DISABLE)
    status = await auth_router.totp_status(user)
    assert status == {"totp_enabled": True, "totp_pending": False, "totp_unreadable": True}


# --- F8 5.2: Profil-Felder leeren -----------------------------------------------------------------------------
def test_profile_update_validators():
    assert ProfileUpdate(phone="  ").phone == ""
    assert ProfileUpdate(city="  Graz ").city == "Graz"
    assert ProfileUpdate(preferred_language=" HU ").preferred_language == "hu"
    assert ProfileUpdate(preferred_language="").preferred_language == ""
    with pytest.raises(ValidationError):
        ProfileUpdate(preferred_language="fr")
    with pytest.raises(ValidationError):
        ProfileUpdate(phone="abc")


async def test_update_profile_clears_sent_empty_fields_only():
    user = make_user(2, "anna", phone="+43 1", city="Wien", company="ACME")
    user.preferred_language = "de"
    await auth_router.update_profile(UserDB([user]), ProfileUpdate(phone="", city="", preferred_language=""), user)
    assert user.phone is None and user.city is None and user.preferred_language is None
    assert user.company == "ACME"  # nicht mitgeschickt -> unveraendert
    await auth_router.update_profile(UserDB([user]), ProfileUpdate(display_name="  "), user)
    assert user.display_name is None


def test_user_guard_is_local_account():
    assert user_guard.is_local_account(make_user(1, "a"))
    assert user_guard.is_local_account(type("U", (), {})())  # ohne Attribut -> lokal
    assert not user_guard.is_local_account(make_user(2, "b", auth_source="oidc"))


def test_reset_link_limiter_cleanup():
    auth_router._RESET_LINK_LAST.update({1: 0.0, 2: 0.0})
    assert auth_router._reset_link_rate_limited(3) is False
    assert 1 not in auth_router._RESET_LINK_LAST and 3 in auth_router._RESET_LINK_LAST
    assert auth_router._reset_link_rate_limited(3) is True


def test_reset_password_body_defaults_force_change():
    assert AdminPasswordResetBody().must_change_password is True
    assert AdminPasswordResetBody().revoke_all_access is False
    assert UserUpdate().must_change_password is None

