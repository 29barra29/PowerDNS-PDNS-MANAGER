"""Zeitstempel-Helfer: DB-Werte sind naive UTC-Datetimes (MariaDB DATETIME).

Ohne Offset interpretiert JavaScript ``new Date("2026-09-14T12:00:00")`` als
Ortszeit – die UI zeigt dann 1-2 Stunden falsch an. Deshalb geben wir immer
``+00:00`` mit.

Ab 3.0 werden alle neuen Tabellen und neuen DATETIME-Spalten Python-seitig als
naive UTC geschrieben (``utcnow()``), nie ueber ``func.now()`` – dann haengt der
Wert nicht von der Zeitzone der Datenbank ab.
"""
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Aktuelle Zeit als naive UTC-Datetime (passend zu MariaDB DATETIME)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_naive() -> datetime:
    """Alias von :func:`utcnow` (Name aus der F7-Spezifikation)."""
    return utcnow()


def to_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Aware -> nach UTC umrechnen und tzinfo entfernen; naive Werte bleiben unveraendert."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()
