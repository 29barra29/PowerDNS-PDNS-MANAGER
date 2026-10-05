"""Hintergrund-Aufgaben des Backends (Bauplan B.9).

Ein Schalter fuer alles: ``settings.BACKGROUND_WORKERS_ENABLED`` (Default an; die Tests setzen ihn in
``conftest.py`` auf ``false``). ``main.py`` ruft ``start_all()`` im lifespan nach dem Laden der Server
und ``await stop_all()`` beim Herunterfahren.

Aufgaben:
- ``webhook_worker``: Startup-Reset unterbrochener Zustellungen, dann ``webhook_worker.start_worker()``
  (der Worker verwaltet seine eigene Schleife; Status ueber ``webhook_worker.worker_state()``).
- ``audit_purge``: erster Lauf 5 min nach dem Start, danach stuendlich
  ``services.audit.purge_expired_audit_logs()`` (Aufbewahrungsfrist aus den Einstellungen; 0 = aus).

Single-Worker-Betrieb (ein uvicorn-Prozess) ist Voraussetzung – wie bisher.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Awaitable, Callable, Optional

from app.core.config import settings
from app.core.timeutil import iso_utc, utcnow

logger = logging.getLogger(__name__)

AUDIT_PURGE_FIRST_DELAY = 300.0  # Sekunden nach dem Start
AUDIT_PURGE_INTERVAL = 3600.0
STOP_TIMEOUT = 10.0

_tasks: dict[str, asyncio.Task] = {}
_status: dict[str, dict[str, Any]] = {}
_started = False


def _mark(name: str, **values: Any) -> None:
    _status.setdefault(name, {"last_run_at": None, "last_error_at": None})
    _status[name].update(values)


def _spawn(name: str, coro_factory: Callable[[], Awaitable[None]]) -> None:
    _mark(name)
    _tasks[name] = asyncio.get_running_loop().create_task(coro_factory(), name=f"pdnsmgr-{name}")


async def _start_webhook_worker() -> None:
    from app.services import webhook_worker

    try:
        n = await webhook_worker.reset_in_progress_on_startup()
        if n:
            logger.info("Webhook-Worker: %s unterbrochene Zustellungen neu eingeplant", n)
    except Exception as exc:  # noqa: BLE001 - Start des Workers trotzdem versuchen
        _mark("webhook_worker", last_error_at=iso_utc(utcnow()))
        logger.error("Webhook-Worker: Startup-Reset fehlgeschlagen: %s", exc)
    try:
        webhook_worker.start_worker()
        _mark("webhook_worker", last_run_at=iso_utc(utcnow()))
    except Exception:  # noqa: BLE001
        _mark("webhook_worker", last_error_at=iso_utc(utcnow()))
        logger.exception("Webhook-Worker konnte nicht gestartet werden")


async def _audit_purge_once() -> None:
    from app.services.audit import purge_expired_audit_logs

    try:
        await purge_expired_audit_logs()
        _mark("audit_purge", last_run_at=iso_utc(utcnow()))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - naechster Lauf in einer Stunde
        _mark("audit_purge", last_error_at=iso_utc(utcnow()))
        logger.exception("Audit-Bereinigung im Hintergrund fehlgeschlagen – naechster Versuch in einer Stunde")


async def _audit_purge_loop(first_delay: Optional[float] = None, interval: Optional[float] = None) -> None:
    """Erster Lauf nach ``AUDIT_PURGE_FIRST_DELAY``, dann alle ``AUDIT_PURGE_INTERVAL`` Sekunden."""
    await asyncio.sleep(AUDIT_PURGE_FIRST_DELAY if first_delay is None else first_delay)
    while True:
        await _audit_purge_once()
        await asyncio.sleep(AUDIT_PURGE_INTERVAL if interval is None else interval)


def start_all() -> None:
    """Hintergrund-Aufgaben starten (idempotent; nur mit ``BACKGROUND_WORKERS_ENABLED``)."""
    global _started
    if not settings.BACKGROUND_WORKERS_ENABLED:
        logger.warning(
            "Hintergrund-Aufgaben deaktiviert (BACKGROUND_WORKERS_ENABLED=false): keine Webhook-Zustellung, "
            "keine automatische Audit-Bereinigung – Ereignisse werden nur gesammelt."
        )
        return
    if _started:
        return
    _started = True
    _spawn("webhook_worker", _start_webhook_worker)
    _spawn("audit_purge", _audit_purge_loop)
    logger.info("Hintergrund-Aufgaben gestartet: %s", ", ".join(sorted(_tasks)))


async def stop_all(timeout: float = STOP_TIMEOUT) -> None:
    """Alle Aufgaben abbrechen und hoechstens ``timeout`` Sekunden auf ihr Ende warten (nie werfen)."""
    global _started
    from app.services import webhook_worker

    try:
        await asyncio.wait_for(webhook_worker.stop_worker(), timeout)
    except Exception as exc:  # noqa: BLE001 - Herunterfahren darf nicht scheitern
        logger.warning("Webhook-Worker nicht sauber beendet: %s", type(exc).__name__)
    tasks = [t for t in _tasks.values() if not t.done()]
    for t in tasks:
        t.cancel()
    if tasks:
        with suppress(Exception):
            done, pending = await asyncio.wait(tasks, timeout=timeout)
            if pending:
                logger.warning("Hintergrund-Aufgaben nach %.0f s nicht beendet: %s", timeout,
                               ", ".join(t.get_name() for t in pending))
    _tasks.clear()
    _started = False


def state() -> dict:
    """``{"enabled", "tasks": {name: {"running", "last_run_at", "last_error_at"}}}`` (fuer /health lokal und F12/F13)."""
    tasks: dict[str, dict[str, Any]] = {}
    for name in sorted(set(_status) | set(_tasks)):
        t = _tasks.get(name)
        info = dict(_status.get(name, {"last_run_at": None, "last_error_at": None}))
        info["running"] = bool(t is not None and not t.done())
        tasks[name] = info
    if "webhook_worker" in tasks:
        try:
            from app.services import webhook_worker

            ws = webhook_worker.worker_state()
            tasks["webhook_worker"]["running"] = bool(ws.get("running"))
            tasks["webhook_worker"]["last_error_at"] = ws.get("last_error_at") or tasks["webhook_worker"]["last_error_at"]
        except Exception:  # noqa: BLE001
            pass
    return {"enabled": bool(settings.BACKGROUND_WORKERS_ENABLED), "tasks": tasks}


def reset_for_tests() -> None:
    """Modulzustand leeren (nur Tests; laufende Tasks vorher mit ``stop_all`` beenden)."""
    global _started
    _tasks.clear()
    _status.clear()
    _started = False
