"""Webhook-Zustellung im Hintergrund (F6 5.7, Bauplan B.8/B.9).

Ein asyncio-Task (gestartet von ``services.background``) holt faellige Zeilen aus ``webhook_deliveries`` und
stellt sie zu:

1. **Claim** (``claim_due``): ``SELECT … FOR UPDATE SKIP LOCKED`` auf ``status IN ('queued','failed')`` mit
   ``next_attempt_at <= jetzt``, dann ``status='in_progress'``, ``attempts+1``, ``last_attempt_at=jetzt`` – in
   einer kurzen Transaktion. SKIP LOCKED verhindert Doppelversand auch bei parallelen Claims (MariaDB >= 10.6).
2. **Versand** (``_process``): Werte lesen, Session schliessen (keine DB-Verbindung waehrend HTTP),
   ``webhook_sender.send_delivery`` mit dem gespeicherten Body und der gespeicherten Signatur, Ziel-URL
   entschluesselt aus ``webhooks.url``.
3. **Ergebnis** (``apply_result``): ``succeeded`` | ``failed`` (Backoff ``30 s * 2^Versuche`` + bis zu 10 %
   Zufall, ``Retry-After`` wird respektiert, max. 1 h) | ``dead`` (Budget ``max_attempts`` erschoepft oder
   endgueltiger Fehler wie 410/SSRF). Bei 6 Versuchen: sofort, ~1, ~2, ~4, ~8, ~16 min -> nach ~31 min ``dead``.

Weitere Aufgaben: Startup-Reset (alle ``in_progress`` -> ``failed``/``dead``, ``interrupted``; setzt EINEN Worker
voraus), Stale-Reset nach 300 s, Housekeeping (``succeeded``/``dead``/``cancelled`` nach 30 Tagen loeschen, in
Bloecken). Zeilen mit Status ``cancelled`` (gesetzt per direktem UPDATE durch ``services.access_revocation``
beim Widerruf aller Zugaenge, [S9]) sind endgueltig: der Worker holt sie nie ab, setzt sie nie zurueck und
ueberschreibt sie nicht, falls der Widerruf waehrend eines Versands kommt.

Modul-API (verbindlich, ``services.background`` und die UI nutzen sie): ``start_worker()``,
``await stop_worker()``, ``notify_worker()`` (Weckruf nach Commit, no-op ohne Worker), ``worker_state()``,
``await reset_in_progress_on_startup()``; dazu ``apply_result`` (auch fuer die Test-Zustellung im Router),
``record_attempt`` (Metrik + Beobachter) und ``register_attempt_observer``.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Callable, Optional

from sqlalchemy import delete as sql_delete, or_, select, update

from app.core import metrics as prom
from app.core.config import settings
from app.core.database import async_session
from app.core.secrets import is_unreadable
from app.core.timeutil import iso_utc, utcnow
from app.models.models import User, Webhook, WebhookDelivery
from app.services import webhook_sender
from app.services.webhook_outbox import (
    ERROR_SECRET_UNREADABLE,
    ERROR_URL_UNREADABLE,
    MAX_ATTEMPTS,
    TEXT_SECRET_UNREADABLE,
    TEXT_URL_UNREADABLE,
)
from app.services.webhook_service import url_host

logger = logging.getLogger(__name__)

POLL_SECONDS = 5.0
CLAIM_BATCH = 20
MAX_PARALLEL = 4
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 3600
JITTER_FRACTION = 0.1
STALE_IN_PROGRESS_SECONDS = 300
HOUSEKEEPING_INTERVAL_SECONDS = 3600
HOUSEKEEPING_BATCH = 1000
RETENTION_DAYS = 30
# Unter dem 10-s-Budget von background.stop_all: der Worker bricht seinen Task selbst ab, bevor
# background den Stopp-Aufruf abbricht (sonst liefe der Task hinter asyncio.shield weiter).
STOP_TIMEOUT_SECONDS = 8.0
ERROR_LOG_INTERVAL_SECONDS = 60.0

PENDING_STATUSES = ("queued", "failed")
TERMINAL_STATUSES = ("succeeded", "dead", "cancelled")

TEXT_INTERRUPTED = "Zustellung unterbrochen (Neustart oder Zeitüberschreitung)"
TEXT_WEBHOOK_DELETED = "Webhook gelöscht"
TEXT_WEBHOOK_INACTIVE = "Webhook deaktiviert"
TEXT_OWNER_INACTIVE = "Besitzer deaktiviert oder gelöscht"

AttemptObserver = Callable[[str, float, Optional[int]], None]  # (status nach dem Versuch, Dauer s, HTTP-Code)
_attempt_observers: list[AttemptObserver] = []


# --------------------------------------------------------------------------- Backoff und Ergebnis
def compute_next_delay(attempts: int, retry_after: Optional[int] = None) -> float:
    """Wartezeit vor dem naechsten Versuch; ``attempts`` = bisher erfolgte Versuche (1..)."""
    exp = max(0, int(attempts or 0))
    base = float(min(BACKOFF_BASE_SECONDS * (2 ** min(exp, 30)), BACKOFF_MAX_SECONDS))
    delay = base + random.uniform(0, base * JITTER_FRACTION)
    if retry_after:
        delay = max(delay, float(min(retry_after, BACKOFF_MAX_SECONDS)))
    return delay


def apply_result(d: WebhookDelivery, wh: Optional[Webhook], r: webhook_sender.SendResult, now: datetime) -> str:
    """Ergebnis eines Versuchs auf Zustellung und Webhook-Zaehler anwenden; Rueckgabe = neuer Status."""
    d.last_status_code = r.status_code
    d.last_duration_ms = r.duration_ms
    d.last_response_excerpt = (r.excerpt or None) and r.excerpt[:1024]
    if r.ok:
        d.status, d.delivered_at, d.last_error_code, d.last_error = "succeeded", now, None, None
        if wh is not None:
            wh.last_success_at = now
            wh.consecutive_failures = 0
    else:
        d.last_error_code = (r.error_code or "internal_error")[:32]
        d.last_error = (r.error or "")[:512]
        if r.permanent or (d.attempts or 0) >= (d.max_attempts or 0):
            d.status = "dead"
        else:
            d.status = "failed"
            d.next_attempt_at = now + timedelta(seconds=compute_next_delay(d.attempts or 0, r.retry_after))
        if wh is not None:
            wh.last_failure_at = now
            wh.consecutive_failures = int(wh.consecutive_failures or 0) + 1
    return d.status


def register_attempt_observer(cb: AttemptObserver) -> None:
    """Rueckruf ``cb(status, dauer_s, http_code)`` nach jedem Zustellversuch (z. B. eigene Metriken)."""
    if cb not in _attempt_observers:
        _attempt_observers.append(cb)


def record_attempt(status: str, duration_s: float = 0.0, http_code: Optional[int] = None) -> None:
    """Metrik ``pdnsmgr_webhook_deliveries_total`` (B.10-Mapping) und Beobachter; wirft nie.

    ``status``: ``succeeded`` | ``failed`` | ``dead`` | ``cancelled`` (verworfen, ohne Versand).
    """
    try:
        prom.record_webhook_delivery(status)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Webhook-Metrik fehlgeschlagen: %s", exc)
    for cb in list(_attempt_observers):
        try:
            cb(status, duration_s, http_code)
        except Exception as exc:  # noqa: BLE001 - Beobachter duerfen die Zustellung nie stoeren
            logger.debug("Webhook-Versuchs-Beobachter fehlgeschlagen: %s", exc)


# --------------------------------------------------------------------------- DB-Schritte
async def claim_due(limit: int = CLAIM_BATCH, *, now: Optional[datetime] = None) -> list[int]:
    """Faellige Zustellungen sperren und als ``in_progress`` markieren; Rueckgabe = IDs."""
    now = now or utcnow()
    async with async_session() as s:
        ids = list((await s.execute(
            select(WebhookDelivery.id)
            .where(WebhookDelivery.status.in_(PENDING_STATUSES), WebhookDelivery.next_attempt_at <= now)
            .order_by(WebhookDelivery.next_attempt_at, WebhookDelivery.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )).scalars().all())
        if ids:
            await s.execute(
                update(WebhookDelivery)
                .where(WebhookDelivery.id.in_(ids))
                .values(status="in_progress", attempts=WebhookDelivery.attempts + 1, last_attempt_at=now)
                .execution_options(synchronize_session=False)
            )
        await s.commit()
    return ids


async def reset_stale(*, older_than_seconds: Optional[int], now: Optional[datetime] = None) -> int:
    """``in_progress`` -> ``failed`` (sofort faellig, ``interrupted``) bzw. ``dead``, wenn das Budget erschoepft ist.

    ``older_than_seconds=None``: alle (Startup, ein Worker); sonst nur Zeilen, deren Claim (``last_attempt_at``)
    aelter ist. Rueckgabe = Anzahl zurueckgesetzter Zeilen.
    """
    now = now or utcnow()
    conds = [WebhookDelivery.status == "in_progress"]
    if older_than_seconds is not None:
        cutoff = now - timedelta(seconds=older_than_seconds)
        conds.append(or_(WebhookDelivery.last_attempt_at.is_(None), WebhookDelivery.last_attempt_at < cutoff))
    async with async_session() as s:
        dead = await s.execute(
            update(WebhookDelivery)
            .where(*conds, WebhookDelivery.attempts >= WebhookDelivery.max_attempts)
            .values(status="dead", last_error_code="interrupted", last_error=TEXT_INTERRUPTED)
            .execution_options(synchronize_session=False)
        )
        retry = await s.execute(
            update(WebhookDelivery)
            .where(*conds, WebhookDelivery.attempts < WebhookDelivery.max_attempts)
            .values(status="failed", next_attempt_at=now, last_error_code="interrupted", last_error=TEXT_INTERRUPTED)
            .execution_options(synchronize_session=False)
        )
        await s.commit()
    n_dead, n_retry = int(dead.rowcount or 0), int(retry.rowcount or 0)
    for _ in range(n_dead):
        record_attempt("dead")
    for _ in range(n_retry):
        record_attempt("failed")
    return n_dead + n_retry


async def reset_in_progress_on_startup() -> int:
    """Beim Start: alle ``in_progress`` zuruecksetzen (sie gehoeren zu einem beendeten Prozess)."""
    return await reset_stale(older_than_seconds=None)


async def housekeeping(now: Optional[datetime] = None, *, retention_days: int = RETENTION_DAYS) -> int:
    """Abgeschlossene Zustellungen (``succeeded``/``dead``/``cancelled``) aelter als ``retention_days`` loeschen.

    In Bloecken zu ``HOUSEKEEPING_BATCH`` IDs, je Block eine Transaktion (kurze Sperren). Rueckgabe = geloescht.
    """
    cutoff = (now or utcnow()) - timedelta(days=retention_days)
    total = 0
    while True:
        async with async_session() as s:
            ids = list((await s.execute(
                select(WebhookDelivery.id)
                .where(WebhookDelivery.status.in_(TERMINAL_STATUSES), WebhookDelivery.created_at < cutoff)
                .order_by(WebhookDelivery.id)
                .limit(HOUSEKEEPING_BATCH)
            )).scalars().all())
            if not ids:
                break
            await s.execute(
                sql_delete(WebhookDelivery).where(WebhookDelivery.id.in_(ids))
                .execution_options(synchronize_session=False)
            )
            await s.commit()
        total += len(ids)
        if len(ids) < HOUSEKEEPING_BATCH:
            break
    return total


def _discard(d: WebhookDelivery, code: str, text: str) -> None:
    d.status, d.last_error_code, d.last_error = "dead", code, text


async def _process(pk: int) -> Optional[str]:
    """Eine geclaimte Zustellung senden und das Ergebnis speichern; Rueckgabe = neuer Status (oder None)."""
    # Schritt 1: lesen, Vorbedingungen pruefen – danach Session schliessen (keine DB-Verbindung waehrend HTTP)
    async with async_session() as s:
        d = await s.get(WebhookDelivery, pk)
        if d is None or d.status != "in_progress":
            return None  # geloescht oder extern beendet (z. B. cancelled durch access_revocation)
        wh = await s.get(Webhook, d.webhook_id)
        owner = await s.get(User, d.user_id) if wh is not None else None
        reason: Optional[tuple[str, str, bool]] = None  # (code, text, verworfen?)
        if wh is None:
            reason = ("webhook_deleted", TEXT_WEBHOOK_DELETED, True)
        elif not wh.is_active:
            reason = ("webhook_inactive", TEXT_WEBHOOK_INACTIVE, True)
        elif owner is None or not owner.is_active:
            reason = ("owner_inactive", TEXT_OWNER_INACTIVE, True)
        elif is_unreadable(wh.url) or not (wh.url or "").strip():
            reason = (ERROR_URL_UNREADABLE, TEXT_URL_UNREADABLE, False)  # [S10]
        elif not d.signature:
            reason = (ERROR_SECRET_UNREADABLE, TEXT_SECRET_UNREADABLE, False)
        if reason is not None:
            _discard(d, reason[0], reason[1])
            await s.commit()
            record_attempt("cancelled" if reason[2] else "dead")
            return "dead"
        url = str(wh.url).strip()
        body = d.body or ""
        signature, event, delivery_id = d.signature, d.event, d.delivery_id
        attempt, webhook_id = int(d.attempts or 1), d.webhook_id

    # Schritt 2: senden
    result = await webhook_sender.send_delivery(
        url=url,
        body=body.encode("ascii", "replace"),
        headers=webhook_sender.build_request_headers(event=event, delivery_id=delivery_id, attempt=attempt,
                                                     signature=signature),
    )

    # Schritt 3: Ergebnis speichern (Zeile und Webhook neu laden)
    async with async_session() as s:
        d = await s.get(WebhookDelivery, pk)
        if d is None:
            return None  # waehrend des Versands geloescht (Webhook/Benutzer geloescht)
        if d.status != "in_progress":
            # Extern beendet (Widerruf -> cancelled, Deaktivierung -> dead): Status bleibt, nur Diagnose.
            d.last_status_code, d.last_duration_ms = result.status_code, result.duration_ms
            await s.commit()
            record_attempt("cancelled", result.duration_ms / 1000, result.status_code)
            return d.status
        wh = await s.get(Webhook, d.webhook_id)
        status = apply_result(d, wh, result, utcnow())
        await s.commit()

    record_attempt(status, result.duration_ms / 1000, result.status_code)
    if status == "dead":
        logger.warning(
            "Webhook-Zustellung %s (Webhook %s, Ziel %s) endgueltig fehlgeschlagen nach %s Versuch(en): %s",
            delivery_id, webhook_id, url_host(url), attempt, result.error_code,
        )
    return status


# --------------------------------------------------------------------------- Worker
class WebhookWorker:
    """Hintergrund-Schleife: Housekeeping, Stale-Reset, Claim + parallele Zustellung (max. ``MAX_PARALLEL``)."""

    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._sem = asyncio.Semaphore(MAX_PARALLEL)
        self._task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_housekeeping: Optional[float] = None
        self._last_error_log = 0.0
        self.last_loop_at: Optional[datetime] = None
        self.last_error_at: Optional[datetime] = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = self._loop.create_task(self._run(), name="pdnsmgr-webhook-worker")

    async def stop(self, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
        self._stop.set()
        self._wake.set()
        task = self._task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        except Exception:  # noqa: BLE001 - Task endete mit Fehler; beim Herunterfahren egal
            pass

    def wake(self) -> None:
        """Schleife sofort weiterlaufen lassen (threadsicher)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake.set()
        else:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(self._wake.set)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _sleep(self, seconds: float) -> None:
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)
        self._wake.clear()

    async def _maybe_housekeeping(self) -> None:
        now_mono = time.monotonic()
        if self._last_housekeeping is not None and now_mono - self._last_housekeeping < HOUSEKEEPING_INTERVAL_SECONDS:
            return
        self._last_housekeeping = now_mono
        n = await housekeeping()
        if n:
            logger.info("Webhook-Zustellprotokoll: %s Eintraege aelter als %s Tage geloescht", n, RETENTION_DAYS)

    async def _guarded(self, pk: int) -> Optional[str]:
        async with self._sem:
            return await _process(pk)

    async def run_once(self) -> int:
        """Eine Runde: faellige Zeilen claimen und zustellen. Rueckgabe = Anzahl geclaimter Zeilen."""
        ids = await claim_due(CLAIM_BATCH)
        if ids:
            res = await asyncio.gather(*(self._guarded(i) for i in ids), return_exceptions=True)
            for x in res:
                if isinstance(x, asyncio.CancelledError):
                    raise x
                if isinstance(x, Exception):
                    # Zeile bleibt in_progress -> Stale-Reset nach STALE_IN_PROGRESS_SECONDS
                    logger.error("Webhook-Zustellung: unerwarteter Fehler (%s)", type(x).__name__)
        return len(ids)

    def _log_loop_error(self, errors: int) -> None:
        now_mono = time.monotonic()
        if now_mono - self._last_error_log >= ERROR_LOG_INTERVAL_SECONDS:
            self._last_error_log = now_mono
            logger.exception("Webhook-Worker: Fehler in der Schleife (Versuch %s)", errors)

    async def _run(self) -> None:
        errors = 0
        while not self._stop.is_set():
            try:
                self.last_loop_at = utcnow()
                await self._maybe_housekeeping()
                await reset_stale(older_than_seconds=STALE_IN_PROGRESS_SECONDS)
                n = await self.run_once()
                errors = 0
                if n and not self._stop.is_set():
                    continue  # sofort weitere faellige Zeilen holen
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Schleife laeuft weiter (z. B. DB kurz weg)
                errors += 1
                self.last_error_at = utcnow()
                self._log_loop_error(errors)
                await self._sleep(min(60.0, float(2 ** min(errors, 6))))
                continue
            await self._sleep(POLL_SECONDS)


_worker: Optional[WebhookWorker] = None


def start_worker() -> None:
    """Worker im laufenden Event-Loop starten (idempotent)."""
    global _worker
    if _worker is not None and _worker.running:
        return
    _worker = WebhookWorker()
    _worker.start()
    logger.info("Webhook-Worker gestartet (Abfrage alle %ss, max. %s parallele Zustellungen)",
                int(POLL_SECONDS), MAX_PARALLEL)


async def stop_worker() -> None:
    """Worker anhalten (idempotent); laufende Versuche werden nach ``STOP_TIMEOUT_SECONDS`` abgebrochen."""
    w = _worker
    if w is None:
        return
    await w.stop()


def notify_worker() -> None:
    """Weckruf nach einem Commit mit neuen Zustellungen; ohne laufenden Worker wirkungslos."""
    w = _worker
    if w is not None and w.running:
        w.wake()
    return None


def worker_state() -> dict:
    """``{"enabled", "running", "last_loop_at", "last_error_at"}`` fuer UI, ``/health`` (lokal) und Monitoring."""
    w = _worker
    return {
        "enabled": bool(settings.BACKGROUND_WORKERS_ENABLED),
        "running": bool(w is not None and w.running),
        "last_loop_at": iso_utc(w.last_loop_at) if w is not None else None,
        "last_error_at": iso_utc(w.last_error_at) if w is not None else None,
    }


def reset_for_tests() -> None:
    """Modulzustand leeren (nur Tests; einen laufenden Worker vorher mit ``stop_worker`` beenden)."""
    global _worker
    _worker = None
    _attempt_observers.clear()
