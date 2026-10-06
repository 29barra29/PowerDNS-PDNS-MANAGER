"""``services/access_revocation.py`` ohne Datenbank (Bauplan B.16, [S9]).

Prueft die erzeugten Statements: Browser-Sitzungen beenden (``users.sessions_revoked_at``, L3), Panel-Tokens
endgueltig widerrufen (``revoked_at``), DynDNS-Tokens sperren (Secret entwertet, auch pausierte; A1 aus WS-F9F11-BE
fix2), Webhooks nur des Ziel-Benutzers deaktivieren, ausstehende Zustellungen (``queued``/``failed``) auf
``cancelled``. Der SQL-Pfad gegen MariaDB steht in ``test_admin_user_security_db.py``.
"""
from datetime import datetime, timezone

from f2f3fakes import DynDnsToken, PanelToken, UserDB
from app.core import metrics as prom
from app.services import access_revocation as ar


def _tok(tid, uid, *, active=True, revoked=False):
    from app.core.timeutil import utcnow

    return PanelToken(id=tid, user_id=uid, name="t", token_prefix="p", token_hash=f"h{tid}", is_active=active,
                      permission="manage", allow_admin=False, revoked_at=utcnow() if revoked else None)


def _dyn(tid, uid, *, active=True, revoked=False):
    return DynDnsToken(id=tid, user_id=uid, name="d", token_prefix="dnsmgr_dyn_x",
                       token_hash=("revoked:" if revoked else "") + f"{tid:064x}", is_active=active)


async def test_revoke_all_statements_and_counts(monkeypatch):
    recorded = []
    monkeypatch.setattr(prom, "record_webhook_delivery", lambda status: recorded.append(status))
    tokens = [_tok(1, 5), _tok(2, 5, active=False)]
    dyn_tokens = [_dyn(1, 5), _dyn(2, 5, active=False), _dyn(3, 5, active=False, revoked=True), _dyn(4, 6)]
    old_hash = dyn_tokens[2].token_hash
    db = UserDB(tokens=tokens, dyndns=dyn_tokens, rowcounts={"webhooks": 1, "webhook_deliveries": 3})
    out = await ar.revoke_all(db, 5, reason="test")
    # DynDNS: aktive UND pausierte Tokens werden gesperrt, schon gesperrte zaehlen nicht, fremde bleiben
    assert out == {"panel_tokens": 2, "dyndns_tokens": 2, "webhooks": 1, "cancelled_deliveries": 3}
    assert all(t.revoked_at is not None and t.is_active is False for t in tokens)
    assert all(t.is_active is False and t.token_hash.startswith("revoked:") for t in dyn_tokens[:3])
    assert dyn_tokens[2].token_hash == old_hash
    assert dyn_tokens[3].is_active is True and not dyn_tokens[3].token_hash.startswith("revoked:")
    assert not db.updates_on("dyndns_tokens")  # kein blosses is_active=0 mehr
    (sess,) = db.updates_on("users")
    (hook,) = db.updates_on("webhooks")
    (dlv,) = db.updates_on("webhook_deliveries")
    for stmt in (sess, hook, dlv):
        assert 5 in stmt.compile().params.values()  # nur der Ziel-Benutzer
    assert isinstance(sess.compile().params["sessions_revoked_at"], datetime)
    assert hook.compile().params["is_active"] is False
    params = dlv.compile().params
    assert params["status"] == "cancelled" and params["last_error_code"] == "webhook_inactive"
    assert sorted(params["status_1"]) == ["failed", "queued"]
    assert recorded == ["cancelled"] * 3
    assert db.flushes >= 1


async def test_revoke_all_selected_kinds_only():
    tokens = [_tok(1, 5)]
    db = UserDB(tokens=tokens, rowcounts={"webhooks": 4})
    out = await ar.revoke_all(db, 5, reason="test", panel_tokens=False, dyndns_tokens=False)
    assert out == {"panel_tokens": 0, "dyndns_tokens": 0, "webhooks": 4, "cancelled_deliveries": 0}
    assert tokens[0].revoked_at is None
    assert not db.updates_on("dyndns_tokens") and not db.updates_on("panel_tokens")
    db = UserDB(tokens=tokens)
    out = await ar.revoke_all(db, 5, reason="test", webhooks=False)
    assert out["panel_tokens"] == 1 and not db.updates_on("webhook_deliveries") and not db.updates_on("webhooks")


async def test_revoke_all_is_idempotent_for_revoked_tokens():
    tokens = [_tok(1, 5, revoked=True)]
    db = UserDB(tokens=tokens)
    out = await ar.revoke_all(db, 5, reason="test")
    assert out["panel_tokens"] == 0


async def test_access_summary_counts():
    db = UserDB(counts={"panel_tokens": 3, "dyndns_tokens": 2, "webhooks": 1, "webhook_deliveries": 7})
    assert await ar.access_summary(db, 5) == {
        "panel_tokens": 3, "dyndns_tokens": 2, "webhooks": 1, "pending_deliveries": 7}
    sqls = [str(s) for s in db.executed]
    assert any("panel_tokens.revoked_at IS NULL" in s for s in sqls)
    # DynDNS: alles ohne Sicherheitssperre zaehlt (auch pausierte Tokens liessen sich reaktivieren)
    assert any("dyndns_tokens.token_hash NOT LIKE" in s for s in sqls)
    assert any("webhook_deliveries.status IN" in s for s in sqls)


async def test_revoke_all_always_ends_sessions():
    db = UserDB()
    await ar.revoke_all(db, 5, reason="test", panel_tokens=False, dyndns_tokens=False, webhooks=False)
    (sess,) = db.updates_on("users")
    assert 5 in sess.compile().params.values()


def test_session_revocation_time_rounds_up_to_next_second():
    now = datetime(2026, 10, 6, 12, 0, 5, 250000, tzinfo=timezone.utc)
    assert ar.session_revocation_time(now) == datetime(2026, 10, 6, 12, 0, 6)
    # auch genau auf der Sekunde: eine Sitzung aus derselben Sekunde gilt als widerrufen
    assert ar.session_revocation_time(datetime(2026, 10, 6, 12, 0, 5)) == datetime(2026, 10, 6, 12, 0, 6)
    assert ar.session_revocation_time().tzinfo is None
