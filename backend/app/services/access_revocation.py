"""Zugangs-Widerruf bei Kontouebernahme (Bauplan B.16, [S9], E-F3-3).

Ein kompromittiertes Konto behaelt ueber ein neues Passwort hinaus Zugang ueber Browser-Sitzungen, Panel-Tokens,
DynDNS-Tokens und Webhooks (die Daten nach aussen tragen). ``revoke_all`` schliesst alle Wege:

- Browser-Sitzungen: ``users.sessions_revoked_at`` (``revoke_sessions``) – ``core.auth.get_current_user`` lehnt
  jede Sitzung ab, deren ``iat`` davor liegt (L3, WS-W2-NACHARBEIT). Das Passwort bleibt unveraendert; der
  Benutzer meldet sich einfach neu an. Immer Teil von ``revoke_all``.
- Panel-Tokens: endgueltig widerrufen (``revoked_at`` gesetzt, ueber ``panel_token.revoke_all_for_user``) –
  ``is_active=0`` allein heisst seit F14 nur "pausiert" und koennte vom Besitzer wieder aktiviert werden.
- DynDNS-Tokens: Sicherheitssperre ueber ``dyndns.revoke_tokens_of_user`` (deaktiviert und Secret entwertet, auch
  pausierte Tokens) – ``is_active=0`` allein liesse sich vom Besitzer per PUT wieder aufheben (Antrag A1 aus
  WS-F9F11-BE fix2).
- Webhooks: ``is_active=0``; noch nicht zugestellte Zustellungen (``queued`` und ``failed`` = wartet auf
  Wiederholung) werden auf ``cancelled`` gesetzt (der Worker fasst ``cancelled`` nicht mehr an, F6-BE).

Kein Audit und kein Commit hier – der Router schreibt den Audit-Eintrag (``details.revoked``) und ``DbWrite``
committet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics as prom
from app.core.timeutil import to_naive_utc, utcnow_naive
from app.models.models import DynDnsToken, PanelToken, User, Webhook, WebhookDelivery
from app.services import dyndns as dyndns_service
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
    verlaengern), ``dyndns_tokens``: Tokens ohne Sicherheitssperre (auch pausierte, sie liessen sich reaktivieren),
    ``webhooks``: aktive Eintraege, ``pending_deliveries``: ausstehende Webhook-Zustellungen.
    """
    panel = (await db.execute(
        select(func.count(PanelToken.id)).where(PanelToken.user_id == user_id, PanelToken.revoked_at.is_(None))
    )).scalar()
    dyndns = (await db.execute(
        select(func.count(DynDnsToken.id)).where(
            DynDnsToken.user_id == user_id,
            DynDnsToken.token_hash.notlike(dyndns_service.REVOKED_HASH_PREFIX + "%"),
        )
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


def session_revocation_time(now: Optional[datetime] = None) -> datetime:
    """Zeitpunkt fuer ``users.sessions_revoked_at``: naive UTC, auf volle Sekunden gekuerzt plus eine Sekunde.

    JWT-``iat`` hat Sekunden-Aufloesung; mit dem Aufrunden ist auch eine Sitzung aus derselben Sekunde wie der
    Widerruf ungueltig (eine neue Anmeldung ist nach spaetestens einer Sekunde wieder moeglich).
    """
    base = to_naive_utc(now) if now is not None else utcnow_naive()
    return base.replace(microsecond=0) + timedelta(seconds=1)


async def revoke_sessions(db: AsyncSession, user_id: int, *, now: Optional[datetime] = None) -> datetime:
    """Beendet alle Browser-Sitzungen eines Benutzers (setzt ``users.sessions_revoked_at``). Nur SQL, kein Commit.

    ``core.auth.get_current_user`` lehnt danach jede Sitzung mit ``iat`` vor diesem Zeitpunkt ab (401
    "Sitzung abgelaufen"). Panel-, DynDNS- und ACME-Tokens sind davon nicht betroffen (dafuer ``revoke_all``).
    """
    ts = session_revocation_time(now)
    await db.execute(sql_update(User).where(User.id == user_id).values(sessions_revoked_at=ts))
    return ts


async def revoke_all(
    db: AsyncSession,
    user_id: int,
    *,
    reason: str,
    panel_tokens: bool = True,
    dyndns_tokens: bool = True,
    webhooks: bool = True,
) -> dict:
    """Widerruft die Zugaenge eines Benutzers (Default: alle drei Token-/Webhook-Arten) und liefert die Anzahlen.

    Beendet immer auch alle Browser-Sitzungen (``revoke_sessions``, unabhaengig von den Schaltern).
    Rueckgabe ``{"panel_tokens": n, "dyndns_tokens": n, "webhooks": n, "cancelled_deliveries": n}`` – fuer den
    Audit-Eintrag (``details.revoked``). ``dyndns_tokens`` zaehlt neu gesperrte Tokens (auch pausierte).
    ``reason`` erscheint nur im Log (z. B. ``"admin_revoke"``). Nur ``flush``, kein Commit.
    """
    out = {"panel_tokens": 0, "dyndns_tokens": 0, "webhooks": 0, "cancelled_deliveries": 0}
    await revoke_sessions(db, user_id)
    if panel_tokens:
        out["panel_tokens"] = len(await ptk.revoke_all_for_user(db, user_id))
    if dyndns_tokens:
        out["dyndns_tokens"] = await dyndns_service.revoke_tokens_of_user(db, user_id)
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
