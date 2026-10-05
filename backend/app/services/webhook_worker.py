"""Webhook-Zustellung im Hintergrund – Modul-API (Bauplan B.8/B.9, F6 5.7).

Stand Welle 0b: **Platzhalter ohne Zustellung.** Ereignisse werden bereits ueber
``webhook_outbox.enqueue_event`` in ``webhook_deliveries`` gesammelt (Status ``queued``); den Versand
(Claim mit ``SKIP LOCKED``, Backoff, Stale-Reset, Housekeeping, ``apply_result``) baut WS-F6-BE in
Welle 1 hier ein. Die Modul-API ist bereits verbindlich, damit ``services.background``, das Wecken nach
dem Commit (``notify_worker``) und die Statusanzeige (``worker_state``) schon jetzt verdrahtet sind:

- ``start_worker()`` / ``await stop_worker()`` – Start/Stopp durch ``services.background``
- ``notify_worker()`` – Weckruf nach dem Commit einer Session mit neuen Zustellungen (no-op ohne Worker)
- ``worker_state()`` – ``{"enabled", "running", "last_loop_at", "last_error_at"}`` fuer die Webhook-UI
- ``await reset_in_progress_on_startup()`` – setzt beim Start unterbrochene Zustellungen zurueck
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_started = False
_last_loop_at: Optional[str] = None
_last_error_at: Optional[str] = None


def start_worker() -> None:
    """Worker starten. Platzhalter: es wird (noch) nichts zugestellt, nur protokolliert."""
    global _started
    if _started:
        return
    _started = True
    logger.info(
        "Webhook-Zustellung ist in diesem Stand noch nicht aktiv – Ereignisse werden in "
        "webhook_deliveries gesammelt (Status queued) und spaeter zugestellt."
    )


async def stop_worker() -> None:
    """Worker anhalten (idempotent)."""
    global _started
    _started = False


def notify_worker() -> None:
    """Weckruf nach einem Commit mit neuen Zustellungen; ohne laufenden Worker wirkungslos."""
    return None


async def reset_in_progress_on_startup() -> int:
    """Unterbrochene Zustellungen (``in_progress``) beim Start neu einplanen.

    Ohne Zustell-Logik setzt niemand ``in_progress`` – es gibt nichts zurueckzusetzen (Rueckgabe 0).
    """
    return 0


def worker_state() -> dict:
    """Zustand fuer UI und ``/health`` (Loopback): ``running`` bleibt ``False``, solange nichts zugestellt wird."""
    return {
        "enabled": bool(settings.BACKGROUND_WORKERS_ENABLED),
        "running": False,
        "last_loop_at": _last_loop_at,
        "last_error_at": _last_error_at,
    }
