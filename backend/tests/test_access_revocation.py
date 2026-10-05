"""``services/access_revocation.py`` ohne Datenbank (Bauplan B.16, [S9]).

Prueft die erzeugten Statements: Panel-Tokens endgueltig widerrufen (``revoked_at``), DynDNS-Tokens und Webhooks
nur des Ziel-Benutzers deaktivieren, ausstehende Zustellungen (``queued``/``failed``) auf ``cancelled``. Der
SQL-Pfad gegen MariaDB steht in ``test_admin_user_security_db.py``.
"""
from f2f3fakes import PanelToken, UserDB
from app.core import metrics as prom
from app.services import access_revocation as ar


def _tok(tid, uid, *, active=True, revoked=False):
    from app.core.timeutil import utcnow

    return PanelToken(id=tid, user_id=uid, name="t", token_prefix="p", token_hash=f"h{tid}", is_active=active,
                      permission="manage", allow_admin=False, revoked_at=utcnow() if revoked else None)


async def test_revoke_all_statements_and_counts(monkeypatch):
    recorded = []
    monkeypatch.setattr(prom, "record_webhook_delivery", lambda status: recorded.append(status))
    tokens = [_tok(1, 5), _tok(2, 5, active=False)]
    db = UserDB(tokens=tokens, rowcounts={"dyndns_tokens": 2, "webhooks": 1, "webhook_deliveries": 3})
    out = await ar.revoke_all(db, 5, reason="test")
    assert out == {"panel_tokens": 2, "dyndns_tokens": 2, "webhooks": 1, "cancelled_deliveries": 3}
    assert all(t.revoked_at is not None and t.is_active is False for t in tokens)
    (dyn,) = db.updates_on("dyndns_tokens")
    (hook,) = db.updates_on("webhooks")
    (dlv,) = db.updates_on("webhook_deliveries")
    for stmt in (dyn, hook, dlv):
        assert 5 in stmt.compile().params.values()  # nur der Ziel-Benutzer
    assert dyn.compile().params["is_active"] is False and hook.compile().params["is_active"] is False
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
    assert any("dyndns_tokens.is_active" in s for s in sqls)
    assert any("webhook_deliveries.status IN" in s for s in sqls)
