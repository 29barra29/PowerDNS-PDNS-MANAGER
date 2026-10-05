"""Zonen-Index: welche schreibbaren PowerDNS-Server fuehren welche Zonen? (F9 5.5)

Hostname -> Zone wird gegen die **echten** Zonen der Server bestimmt (nicht gegen die
ACL-Liste), damit bei einer delegierten Subzone ``home.example.com.`` nie in die
Elternzone geschrieben wird. Genutzt von DynDNS, PTR-Pflege und Propagation.

Prozesslokaler Cache je Server (60 s), bei Lesefehlern hoechstens 10 min alte Werte.
Konfigurierte, aber nicht geladene Server (``pdns_manager.unloaded``) koennen nicht
befragt werden; ``ZoneMatch.unloaded`` nennt sie, damit Aufrufer sie als "nicht
geschrieben" behandeln koennen [D4].
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.core.names import normalize_zone_name
from app.services import fanout
from app.services.acme import find_matching_zone
from app.services.pdns_client import pdns_manager

logger = logging.getLogger(__name__)

CACHE_TTL = 60.0
STALE_MAX = 600.0
LIST_TIMEOUT = 8.0


@dataclass(frozen=True)
class ZoneMatch:
    zone: str  # normalisiert
    servers: tuple[str, ...]  # schreibbare Server mit dieser Zone, Reihenfolge pdns_manager.list_servers()
    unloaded: tuple[str, ...] = ()  # konfigurierte, nicht geladene Server (Zonenstand unbekannt)


_cache: dict[str, tuple[float, frozenset[str]]] = {}
_locks: dict[str, tuple[object, asyncio.Lock]] = {}


def _now() -> float:
    return time.monotonic()


def _lock(name: str) -> asyncio.Lock:
    """Lock je Server, an die laufende Event-Loop gebunden (Tests wechseln die Loop)."""
    loop = asyncio.get_running_loop()
    entry = _locks.get(name)
    if entry is None or entry[0] is not loop:
        entry = (loop, asyncio.Lock())
        _locks[name] = entry
    return entry[1]


def invalidate(server: Optional[str] = None) -> None:
    """Cache eines Servers (oder mit ``None`` aller Server) verwerfen.

    Aufrufer: Zone anlegen/loeschen/importieren, Server anlegen/aendern/loeschen.
    """
    if server is None:
        _cache.clear()
    else:
        _cache.pop(server, None)


def unloaded_servers() -> dict[str, str]:
    """Konfigurierte, aber nicht geladene Server (Name -> Grund)."""
    return dict(getattr(pdns_manager, "unloaded", {}) or {})


async def _zones_of(name: str, client) -> Optional[frozenset[str]]:
    hit = _cache.get(name)
    now = _now()
    if hit is not None and now - hit[0] < CACHE_TTL:
        return hit[1]
    async with _lock(name):
        hit = _cache.get(name)
        now = _now()
        if hit is not None and now - hit[0] < CACHE_TTL:
            return hit[1]
        try:
            zones = await client.list_zones(timeout=LIST_TIMEOUT)
            names = frozenset(
                normalize_zone_name(z.get("name") or z.get("id"))
                for z in (zones or [])
                if isinstance(z, dict) and (z.get("name") or z.get("id"))
            )
        except Exception as exc:  # noqa: BLE001 - Server wird dann ignoriert bzw. Altwert genutzt
            logger.debug("Zonen-Index: Server %s nicht lesbar (%s)", name, type(exc).__name__)
            if hit is not None and now - hit[0] < STALE_MAX:
                return hit[1]
            return None
        _cache[name] = (_now(), names)
        return names


async def writable_zone_map(db) -> dict[str, frozenset[str]]:
    """``{server: zonen}`` fuer alle geladenen, schreibbaren Server (parallel gelesen).

    Nicht erreichbare Server ohne verwertbaren Cache fehlen im Ergebnis.
    """
    names = await fanout.writable_server_names(db)
    pairs = []
    for n in names:
        try:
            pairs.append((n, pdns_manager.get_client(n)))
        except ValueError:
            continue
    results = await asyncio.gather(*(_zones_of(n, c) for n, c in pairs))
    return {n: zs for (n, _), zs in zip(pairs, results) if zs is not None}


async def find_zone(db, fqdn: str, *, arpa_only: bool = False) -> Optional[ZoneMatch]:
    """Laengste passende Zone fuer ``fqdn`` ueber alle schreibbaren Server."""
    zmap = await writable_zone_map(db)
    names: set[str] = set().union(*zmap.values()) if zmap else set()
    if arpa_only:
        names = {z for z in names if z.endswith(".arpa.")}
    zone = find_matching_zone(fqdn, names)
    if not zone:
        return None
    return ZoneMatch(
        zone=zone,
        servers=tuple(n for n, zs in zmap.items() if zone in zs),
        unloaded=tuple(unloaded_servers()),
    )


async def all_zone_names(db, *, arpa_only: bool = False) -> set[str]:
    zmap = await writable_zone_map(db)
    names: set[str] = set().union(*zmap.values()) if zmap else set()
    if arpa_only:
        names = {z for z in names if z.endswith(".arpa.")}
    return names
