"""Zugangs-Widerruf bei Kontouebernahme (Bauplan B.16, [S9], E-F3-3).

Ein kompromittiertes Konto behaelt ueber ein neues Passwort hinaus Zugang ueber Panel-Tokens, DynDNS-Tokens und
Webhooks (die Daten nach aussen tragen). ``revoke_all`` schliesst alle drei Wege:

- Panel-Tokens: endgueltig widerrufen (``revoked_at`` gesetzt, ueber ``panel_token.revoke_all_for_user``) –
  ``is_active=0`` allein heisst seit F14 nur "pausiert" und koennte vom Besitzer wieder aktiviert werden.
- DynDNS-Tokens: ``is_active=0``.
- Webhooks: ``is_active=0``; noch nicht zugestellte Zustellungen (``queued`` und ``failed`` = wartet auf
  Wiederholung) werden auf ``cancelled`` gesetzt (der Worker fasst ``cancelled`` nicht mehr an, F6-BE).

Nur ORM/SQL auf den Modellen aus Welle 0b, keine Importe aus F6/F9 (die laufen parallel). Kein Audit und kein
Commit hier – der Router schreibt den Audit-Eintrag (``details.revoked``) und ``DbWrite`` committet.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics as prom
from app.models.models import DynDnsToken, PanelToken, Webhook, WebhookDelivery
from app.services import panel_token as ptk

logger = logging.getLogger(__name__)

# Zustellungen, die noch ausstehen und beim Widerruf verworfen werden (in_progress laeuft gerade, der Worker
# schliesst sie selbst ab und sieht danach den deaktivierten Webhook).
PENDING_DELIVERY_STATUSES = ("queued", "failed")
CANCEL_ERROR_CODE = "webhook_inactive"
CANCEL_ERROR_TEXT = "Webhook deaktiviert (Zugänge des Besitzers widerrufen)"

REVOKE_KINDS = ("panel_tokens", "dyndns_tokens", "webhooks")


async def access_summary(db: AsyncSession, user_id: int) -> dict:
    """Zaehler der Zugaenge, die ``revoke_all`` betreffen wuerde.

    ``panel_tokens``: nicht widerrufene Tokens (auch pausierte/abgelaufene – sie liessen sich reaktivieren bzw.
    verlaengern), ``dyndns_tokens``/``webhooks``: aktive Eintraege, ``pending_deliveries``: ausstehende
    Webhook-Zustellungen.
    """
    panel = (await db.execute(
        select(func.count(PanelToken.id)).where(PanelToken.user_id == user_id, PanelToken.revoked_at.is_(None))
    )).scalar()
    dyndns = (await db.execute(
        select(func.count(DynDnsToken.id)).where(DynDnsToken.user_id == user_id, DynDnsToken.is_active.is_(True))
    )).scalar()
    hooks = (await db.execute(
        select(func.count(Webhook.id)).where(Webhook.user_id == user_id, Webhook.is_active.is_(True))
    )).scalar()
    pending = (await db.execute(
        select(func.count(WebhookDelivery.id)).where(
            WebhookDelivery.user_id == user_id, WebhookDelivery.status.in_(PENDING_DELIVERY_STATUSES)
        )
    )).scalar()
    return {
        "panel_tokens": int(panel or 0),
        "dyndns_tokens": int(dyndns or 0),
        "webhooks": int(hooks or 0),
        "pending_deliveries": int(pending or 0),
    }


async def revoke_all(
    db: AsyncSession,
    user_id: int,
    *,
    reason: str,
    panel_tokens: bool = True,
    dyndns_tokens: bool = True,
    webhooks: bool = True,
) -> dict:
    """Widerruft die Zugaenge eines Benutzers (Default: alle drei Arten) und liefert die Anzahlen.

    Rueckgabe ``{"panel_tokens": n, "dyndns_tokens": n, "webhooks": n, "cancelled_deliveries": n}`` – fuer den
    Audit-Eintrag (``details.revoked``). ``reason`` erscheint nur im Log (z. B. ``"admin_revoke"``).
    Nur ``flush``, kein Commit.
    """
    out = {"panel_tokens": 0, "dyndns_tokens": 0, "webhooks": 0, "cancelled_deliveries": 0}
    if panel_tokens:
        out["panel_tokens"] = len(await ptk.revoke_all_for_user(db, user_id))
    if dyndns_tokens:
        res = await db.execute(
            sql_update(DynDnsToken)
            .where(DynDnsToken.user_id == user_id, DynDnsToken.is_active.is_(True))
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
        out["dyndns_tokens"] = int(res.rowcount or 0)
    if webhooks:
        res = await db.execute(
            sql_update(Webhook)
            .where(Webhook.user_id == user_id, Webhook.is_active.is_(True))
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
        out["webhooks"] = int(res.rowcount or 0)
        res = await db.execute(
            sql_update(WebhookDelivery)
            .where(
                WebhookDelivery.user_id == user_id,
                WebhookDelivery.status.in_(PENDING_DELIVERY_STATUSES),
            )
            .values(status="cancelled", last_error_code=CANCEL_ERROR_CODE, last_error=CANCEL_ERROR_TEXT)
            .execution_options(synchronize_session=False)
        )
        out["cancelled_deliveries"] = int(res.rowcount or 0)
    await db.flush()
    for _ in range(out["cancelled_deliveries"]):
        prom.record_webhook_delivery("cancelled")
    logger.info("Zugaenge von user_id=%s widerrufen (%s): %s", user_id, reason, out)
    return out
