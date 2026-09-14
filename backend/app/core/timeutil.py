"""Zeitstempel-Helfer: DB-Werte sind naive UTC-Datetimes (MariaDB DATETIME).

Ohne Offset interpretiert JavaScript ``new Date("2026-09-14T12:00:00")`` als
Ortszeit – die UI zeigt dann 1-2 Stunden falsch an. Deshalb geben wir immer
``+00:00`` mit.
"""
from datetime import datetime, timezone
from typing import Optional


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
