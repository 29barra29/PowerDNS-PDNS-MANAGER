"""Normalisierung von Zonen- und RR-Namen (lower + Trailing-Dot).

PowerDNS speichert Namen absolut mit Punkt am Ende; ACL-Tabellen, Audit-Spalten und
Vergleiche verwenden genau diese Form. Ohne App-Imports, damit Models, Services und
Router das Modul zyklenfrei nutzen koennen.
"""
from typing import Optional


def normalize_zone_name(z: Optional[str]) -> str:
    """strip, lower, Trailing-Dot. Leere Eingabe bleibt ``""``."""
    v = (z or "").strip().lower()
    if not v:
        return v
    if not v.endswith("."):
        v += "."
    return v


def normalize_rr_name(n: Optional[str]) -> str:
    """Wie :func:`normalize_zone_name`; eigener Name dokumentiert die Absicht (RR-Owner)."""
    return normalize_zone_name(n)
