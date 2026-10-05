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

Verwaltung der Zustellungen (Router ``routers/webhooks.py``, F6 3.4–3.8): ``create_test_delivery``,
``retry_delivery`` (``RetryNotAllowed`` -> 409), ``resign_pending`` (Secret-Rotation), ``cancel_pending``
(Deaktivierung), ``delete_for_webhook``/``delete_for_user``, ``stats_for_user`` und ``queue_depth`` (F13).
Alle Funktionen arbeiten in der Session des Aufrufers und committen nicht selbst.

Statusmodell einer Zustellung: ``queued`` (wartet) -> ``in_progress`` (vom Worker geclaimt) -> ``succeeded`` |
``failed`` (erneut eingeplant) | ``dead`` (endgueltig). ``cancelled`` setzt nur ``services.access_revocation``
(Widerruf aller Zugaenge, [S9]) per direktem UPDATE fuer ``queued``-Zeilen; der Worker behandelt es als endgueltig.
"""
from __future__ import annotations

import logging
from datetime import timezone
from typing import Callable, Optional
from uuid import uuid4

from sqlalchemy import delete as sql_delete, event as sa_event, func, or_, select, update
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
DELIVERY_STATUSES = ("queued", "in_progress", "succeeded", "failed", "dead", "cancelled")
RETRYABLE_STATUSES = ("failed", "dead", "succeeded", "cancelled")
TEST_EVENT = "webhook.test"
TEST_MESSAGE = "Test-Zustellung aus PDNS Manager"
_WAKEUP_KEY = "webhook_wakeup"
_LISTENER_KEY = "webhook_wakeup_listener"

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
    """``after_commit``-Listener: weckt den Worker, wenn in dieser Transaktion Zustellungen entstanden sind."""
    if not session.info.pop(_WAKEUP_KEY, None):
        return
    try:
        from app.services import webhook_worker

        webhook_worker.notify_worker()
    except Exception as exc:  # noqa: BLE001 - Weckruf ist optional (der Worker pollt ohnehin)
        logger.debug("Webhook-Worker konnte nicht geweckt werden: %s", exc)


def _register_wakeup_after_commit(db: AsyncSession) -> None:
    """Weckt den Worker nach dem naechsten Commit der Session (einmal je Commit, nicht je Ereignis).

    Der Listener wird je Session nur einmal registriert und reagiert nur, wenn seit dem letzten Commit
    Zustellungen eingereiht wurden. Bei Rollback ist kein Weckruf noetig – der Worker pollt ohnehin.
    """
    try:
        sync_session = db.sync_session
        sync_session.info[_WAKEUP_KEY] = True
        if not sync_session.info.get(_LISTENER_KEY):
            sa_event.listen(sync_session, "after_commit", _wakeup_after_commit)
            sync_session.info[_LISTENER_KEY] = True
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


# --------------------------------------------------------------------------- Verwaltung (F6 3.4–3.8)
class RetryNotAllowed(Exception):
    """Manueller Retry nicht moeglich (Router -> 409 mit diesem Text)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def secret_readable(wh: Webhook) -> bool:
    """Secret vorhanden und entschluesselbar (F5: unlesbar = ``UnreadableSecret("")``)."""
    return bool(wh.secret) and not is_unreadable(wh.secret)


def url_readable(wh: Webhook) -> bool:
    """Ziel-URL vorhanden und entschluesselbar [S10]."""
    return bool(wh.url) and not is_unreadable(wh.url)


async def create_test_delivery(db: AsyncSession, wh: Webhook, *, actor: Optional[User]) -> WebhookDelivery:
    """Zeile fuer die synchrone Test-Zustellung (Ereignis ``webhook.test``, nur dieser Webhook).

    Status ``in_progress`` mit ``attempts=1``/``max_attempts=1`` – der Worker holt sie nie ab; der Router sendet
    selbst und wendet ``webhook_worker.apply_result`` an (Ergebnis ``succeeded`` oder ``dead``). Ist Secret oder
    URL unlesbar, ist die Zeile sofort ``dead`` (``secret_unreadable``/``url_unreadable``) und es wird nichts
    gesendet. Ignoriert Ereignisfilter, Ausloeser und ``is_active``. Nur ``flush``, kein Commit.
    """
    now = utcnow()
    event_id, delivery_id = str(uuid4()), str(uuid4())
    actor_id = getattr(actor, "id", None) if actor is not None else None
    raw = build_payload(
        event=TEST_EVENT, event_id=event_id, delivery_id=delivery_id, occurred_at=now.replace(tzinfo=timezone.utc),
        actor_user_id=actor_id, actor_username=getattr(actor, "username", None) if actor is not None else None,
        actor_via=get_auth_via(), zone=None, server=None, audit_log_id=None,
        data={"webhook_id": wh.id, "webhook_name": wh.name, "message": TEST_MESSAGE},
    )
    row = _delivery_row(wh, event=TEST_EVENT, event_id=event_id, delivery_id=delivery_id, zone=None,
                        audit_log_id=None, raw=raw, now=now)
    row.max_attempts = 1
    if row.status == "queued":
        row.status, row.attempts, row.last_attempt_at = "in_progress", 1, now
    db.add(row)
    await db.flush()
    return row


async def retry_delivery(db: AsyncSession, wh: Webhook, d: WebhookDelivery, *,
                         now=None) -> WebhookDelivery:
    """Zustellung erneut einplanen (F6 3.8); wirft ``RetryNotAllowed`` mit deutschem Text.

    - ``failed``: sofort faellig, Budget unveraendert.
    - ``dead``/``succeeded``/``cancelled``: wieder ``queued`` mit genau einem weiteren Versuch
      (``max_attempts = attempts + 1``), neu signiert mit dem aktuellen Secret – Body und Delivery-ID bleiben.
    Der Worker wird nach dem Commit geweckt.
    """
    now = now or utcnow()
    if not wh.is_active:
        raise RetryNotAllowed("Webhook ist deaktiviert – bitte zuerst aktivieren")
    if d.status in ("queued", "in_progress"):
        raise RetryNotAllowed("Diese Zustellung ist bereits eingeplant oder wird gerade gesendet")
    if d.status not in RETRYABLE_STATUSES:
        raise RetryNotAllowed(f"Zustellung mit Status {d.status} kann nicht erneut gesendet werden")
    if d.status == "failed":
        d.next_attempt_at = now
    else:
        if not secret_readable(wh):
            raise RetryNotAllowed("Webhook-Secret nicht lesbar – bitte zuerst „Secret erneuern“")
        if not url_readable(wh):
            raise RetryNotAllowed("Webhook-Ziel-URL nicht lesbar – bitte zuerst die URL neu eintragen")
        d.status = "queued"
        d.max_attempts = int(d.attempts or 0) + 1
        d.next_attempt_at = now
        d.delivered_at = None
        d.signature = sign(wh.secret, (d.body or "").encode("ascii"))
    await db.flush()
    _register_wakeup_after_commit(db)
    return d


async def resign_pending(db: AsyncSession, wh: Webhook) -> int:
    """Offene Zustellungen (``queued``/``failed``) mit dem aktuellen Secret neu signieren (Rotation)."""
    if not secret_readable(wh):
        return 0
    rows = (await db.execute(
        select(WebhookDelivery.id, WebhookDelivery.body)
        .where(WebhookDelivery.webhook_id == wh.id, WebhookDelivery.status.in_(("queued", "failed")))
    )).all()
    for pk, body in rows:
        await db.execute(
            update(WebhookDelivery).where(WebhookDelivery.id == pk)
            .values(signature=sign(wh.secret, (body or "").encode("ascii")))
            .execution_options(synchronize_session=False)
        )
    return len(rows)


async def cancel_pending(db: AsyncSession, webhook_id: int) -> int:
    """Bei Deaktivierung: offene Zustellungen (``queued``/``failed``) verwerfen -> ``dead``/``webhook_inactive``."""
    res = await db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id, WebhookDelivery.status.in_(("queued", "failed")))
        .values(status="dead", last_error_code="webhook_inactive", last_error="Webhook deaktiviert")
        .execution_options(synchronize_session=False)
    )
    return int(res.rowcount or 0)


async def delete_for_webhook(db: AsyncSession, webhook_id: int) -> int:
    res = await db.execute(sql_delete(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id)
                           .execution_options(synchronize_session=False))
    return int(res.rowcount or 0)


async def delete_for_user(db: AsyncSession, user_id: int) -> int:
    res = await db.execute(sql_delete(WebhookDelivery).where(WebhookDelivery.user_id == user_id)
                           .execution_options(synchronize_session=False))
    return int(res.rowcount or 0)


async def queue_depth(db: AsyncSession) -> dict[str, int]:
    """Anzahl Zustellungen je Status (fuer Monitoring/F13)."""
    rows = (await db.execute(
        select(WebhookDelivery.status, func.count()).group_by(WebhookDelivery.status)
    )).all()
    return {str(st): int(n) for st, n in rows}


async def stats_for_user(db: AsyncSession, user_id: int) -> dict[int, dict[str, int]]:
    """``{webhook_id: {status: anzahl}}`` fuer alle Zustellungen eines Benutzers (eine Abfrage)."""
    rows = (await db.execute(
        select(WebhookDelivery.webhook_id, WebhookDelivery.status, func.count())
        .where(WebhookDelivery.user_id == user_id)
        .group_by(WebhookDelivery.webhook_id, WebhookDelivery.status)
    )).all()
    out: dict[int, dict[str, int]] = {}
    for wid, st, n in rows:
        out.setdefault(int(wid), {})[str(st)] = int(n)
    return out
