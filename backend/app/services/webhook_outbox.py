"""Webhook-Outbox: Ereignisse transaktional einreihen (F6 5.4, Bauplan B.8).

``enqueue_event`` legt je Empfaenger eine Zeile in ``webhook_deliveries`` an – in derselben Session wie
die fachliche Aenderung und das Audit. Damit gilt: Commit -> Ereignis wird (spaeter) zugestellt;
Rollback -> kein Ereignis. Der Body ist bereits fertig signiert (``build_payload`` + ``sign``), der
Worker sendet ihn byte-identisch.

Aufrufmuster in Routern (immer mit ``await``, nach dem Erfolgs-Audit, vor ``return``)::

    from app.services.webhook_outbox import enqueue_event
    audit = await write_audit(db, ...)
    await enqueue_event(db, "record.created", actor=current_user, zone=zone_id, server=server_name,
                        data={...}, audit_log_id=audit.id if audit else None)

Fehler beim Einreihen brechen den Request nie: das Einreihen laeuft in einem SAVEPOINT, ein Fehler
verwirft nur die Outbox-Zeilen (geloggt), Audit und fachliche Aenderung bleiben.

Weitere Funktionen der Outbox (Test-Zustellung, Retry, Neu-Signieren, Abbrechen, Zaehler) ergaenzt
WS-F6-BE in Welle 1.
"""
from __future__ import annotations

import logging
from datetime import timezone
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy import event as sa_event, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.names import normalize_zone_name
from app.core.request_context import get_auth_via
from app.core.secrets import is_unreadable
from app.core.timeutil import utcnow
from app.models.models import User, UserZoneAccess, Webhook, WebhookDelivery
from app.services.webhook_events import EVENT_CATALOG, build_payload, webhook_wants
from app.services.webhook_service import sign

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
_WAKEUP_KEY = "webhook_wakeup"

ERROR_SECRET_UNREADABLE = "secret_unreadable"
ERROR_URL_UNREADABLE = "url_unreadable"
TEXT_SECRET_UNREADABLE = "Webhook-Secret nicht lesbar – bitte „Secret erneuern“"
TEXT_URL_UNREADABLE = "Webhook-Ziel-URL nicht lesbar – bitte die URL neu eintragen"

EnqueueObserver = Callable[[str, int], None]
_enqueue_observers: list[EnqueueObserver] = []


def register_enqueue_observer(cb: EnqueueObserver) -> None:
    """Rueckruf ``cb(event, anzahl_zeilen)`` nach jedem erfolgreichen Einreihen (z. B. Metriken)."""
    if cb not in _enqueue_observers:
        _enqueue_observers.append(cb)


def _notify_enqueue_observers(event: str, count: int) -> None:
    for cb in list(_enqueue_observers):
        try:
            cb(event, count)
        except Exception as exc:  # noqa: BLE001 - Beobachter duerfen das Einreihen nie stoeren
            logger.debug("Webhook-Enqueue-Beobachter fehlgeschlagen: %s", exc)


async def select_recipients(
    db: AsyncSession,
    *,
    event: str,
    actor_user_id: Optional[int],
    zone: Optional[str],
) -> list[Webhook]:
    """Empfaenger-Webhooks eines Ereignisses (Rechte zum Zeitpunkt des Einreihens).

    ``scope="own"``: nur Aktionen des Besitzers. ``scope="zones"``: eigene Aktionen plus Aktionen
    anderer in Zonen, auf die der Besitzer Lese- oder Schreibrecht hat (Admin-Besitzer: alle Zonen).
    Inaktive Webhooks und inaktive Besitzer bekommen nichts; danach greift der Ereignisfilter.
    """
    rows = (await db.execute(
        select(Webhook, User.role)
        .join(User, User.id == Webhook.user_id)
        .where(
            Webhook.is_active.is_(True),
            User.is_active.is_(True),
            or_(Webhook.user_id == actor_user_id, Webhook.scope == "zones"),
        )
        .order_by(Webhook.id)
    )).all()
    # Rolle des BESITZERS (Empfaenger), nicht des Anfragenden -> keine Rechtepruefung des Aufrufers
    need_acl = {
        w.user_id for w, owner_role in rows
        if w.scope == "zones" and owner_role != "admin" and w.user_id != actor_user_id
    }
    allowed: set[int] = set()
    if zone and need_acl:
        allowed = set((await db.execute(
            select(UserZoneAccess.user_id).where(
                UserZoneAccess.zone_name == zone,
                UserZoneAccess.user_id.in_(sorted(need_acl)),
            )
        )).scalars().all())
    out: list[Webhook] = []
    for w, owner_role in rows:
        if w.user_id != actor_user_id:  # fremde Aktion -> nur Zonen-Scope
            if w.scope != "zones" or not zone:
                continue
            if owner_role != "admin" and w.user_id not in allowed:
                continue
        if not webhook_wants(w.events, event):
            continue
        out.append(w)
    return out


def _wakeup_after_commit(session) -> None:
    session.info.pop(_WAKEUP_KEY, None)
    try:
        from app.services import webhook_worker

        webhook_worker.notify_worker()
    except Exception as exc:  # noqa: BLE001 - Weckruf ist optional (der Worker pollt ohnehin)
        logger.debug("Webhook-Worker konnte nicht geweckt werden: %s", exc)


def _register_wakeup_after_commit(db: AsyncSession) -> None:
    """Weckt den Worker nach dem naechsten Commit der Session (einmal je Session-Transaktion).

    Bei Rollback kein Weckruf noetig – der Worker findet faellige Zeilen auch per Poll.
    """
    try:
        sync_session = db.sync_session
        if sync_session.info.get(_WAKEUP_KEY):
            return
        sync_session.info[_WAKEUP_KEY] = True
        sa_event.listen(sync_session, "after_commit", _wakeup_after_commit, once=True)
    except Exception as exc:  # noqa: BLE001 - z. B. Test-Session ohne sync_session
        logger.debug("Weckruf nach Commit nicht registriert: %s", exc)


def _delivery_row(wh: Webhook, *, event: str, event_id: str, delivery_id: str, zone: Optional[str],
                  audit_log_id: Optional[int], raw: bytes, now) -> WebhookDelivery:
    secret = wh.secret
    url = wh.url
    secret_ok = bool(secret) and not is_unreadable(secret)
    url_ok = bool(url) and not is_unreadable(url)
    row = WebhookDelivery(
        delivery_id=delivery_id,
        event_id=event_id,
        webhook_id=wh.id,
        user_id=wh.user_id,
        event=event,
        zone_name=zone,
        audit_log_id=audit_log_id,
        body=raw.decode("ascii"),
        signature=sign(secret, raw) if secret_ok else "",
        status="queued",
        attempts=0,
        max_attempts=MAX_ATTEMPTS,
        next_attempt_at=now,
        created_at=now,
    )
    if not secret_ok:
        row.status, row.last_error_code, row.last_error = "dead", ERROR_SECRET_UNREADABLE, TEXT_SECRET_UNREADABLE
    elif not url_ok:
        # [S10] Ziel-URL ist verschluesselt; unlesbar -> sichtbar tot statt still verworfen
        row.status, row.last_error_code, row.last_error = "dead", ERROR_URL_UNREADABLE, TEXT_URL_UNREADABLE
    return row


async def enqueue_event(
    db: AsyncSession,
    event: str,
    *,
    actor: Optional[User],
    data: dict,
    zone: Optional[str] = None,
    server: Optional[str] = None,
    audit_log_id: Optional[int] = None,
    actor_via: Optional[str] = None,
) -> int:
    """Ereignis fuer alle passenden Webhooks einreihen; Rueckgabe = Anzahl angelegter Zustellungen.

    Unbekanntes Ereignis -> ERROR-Log, 0. Jeder Fehler -> ERROR-Log (SAVEPOINT zurueckgerollt), 0.
    ``actor_via`` Default: Auth-Kontext des Requests (``session`` | ``panel_token`` | …).
    """
    if event not in EVENT_CATALOG:
        logger.error("Unbekanntes Webhook-Ereignis %s – nicht eingereiht", event)
        return 0
    zone_norm = normalize_zone_name(zone) or None
    via = actor_via if actor_via is not None else get_auth_via()
    actor_id = getattr(actor, "id", None) if actor is not None else None
    actor_name = getattr(actor, "username", None) if actor is not None else None
    # Ausstehende Aenderungen des Aufrufers VOR dem SAVEPOINT flushen: deren Fehler gehoeren dem Aufrufer
    # und duerfen nicht als "Webhook nicht eingereiht" verschluckt werden (wie services.audit.write_audit).
    await db.flush()
    count = 0
    try:
        async with db.begin_nested():  # SAVEPOINT: Fehler verwirft nur die Outbox-Zeilen
            hooks = await select_recipients(db, event=event, actor_user_id=actor_id, zone=zone_norm)
            if hooks:
                event_id = str(uuid4())
                now = utcnow()
                occurred = now.replace(tzinfo=timezone.utc)
                rows = []
                for wh in hooks:
                    delivery_id = str(uuid4())
                    raw = build_payload(
                        event=event, event_id=event_id, delivery_id=delivery_id, occurred_at=occurred,
                        actor_user_id=actor_id, actor_username=actor_name, actor_via=via,
                        zone=zone_norm, server=server, audit_log_id=audit_log_id, data=data,
                    )
                    rows.append(_delivery_row(wh, event=event, event_id=event_id, delivery_id=delivery_id,
                                              zone=zone_norm, audit_log_id=audit_log_id, raw=raw, now=now))
                db.add_all(rows)
                await db.flush()
                count = len(rows)
    except Exception:  # noqa: BLE001 - der fachliche Request darf nie am Webhook scheitern
        logger.exception("Webhook-Ereignis %s konnte nicht eingereiht werden", event)
        return 0
    if count:
        _register_wakeup_after_commit(db)
        _notify_enqueue_observers(event, count)
    return count
