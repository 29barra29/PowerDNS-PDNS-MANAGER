"""API-Routen fuer den Serverstatus (lesend). Anmeldung erforderlich.

Sichtbarkeit (F14 3.10, E-F14-1 [S5]):
- ``zone_count`` zaehlt nur die fuer den Aufrufer sichtbaren Zonen (``effective_zone_filter``:
  Benutzer-Zonenrechte geschnitten mit dem Token-Scope; Admin ohne Scope = alle).
- ``url`` (interne PowerDNS-Adresse) und Server-Statistiken (Ringpuffer mit Querynamen ALLER Zonen)
  nur fuer ``is_effective_admin``; ein auf Zonen beschraenkter Token bekommt fuer die Statistik 403.
"""
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    assert_effective_admin,
    assert_not_zone_scoped,
    effective_zone_filter,
    get_current_user,
    is_effective_admin,
)
from app.core.database import DbRead
from app.core.names import normalize_zone_name
from app.models.models import User, ServerConfig
from app.services.pdns_client import (
    pdns_manager,
    PowerDNSAPIError,
    STATUS_PROBE_TIMEOUT,
    ZONES_LIST_PROBE_TIMEOUT,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/servers", tags=["Servers"])

STATISTICS_ZONE_TOKEN_DETAIL = "Server-Statistiken sind mit einem auf Zonen beschränkten API-Token nicht verfügbar"


def _count_zones(zones: list, allowed: Optional[set[str]]) -> int:
    """Anzahl Zonen; bei ``allowed`` nur die sichtbaren (Vergleich normalisiert)."""
    if allowed is None:
        return len(zones)
    return sum(1 for z in zones if normalize_zone_name((z or {}).get("name", "")) in allowed)


async def _probe_server_status(
    name: str,
    client,
    allow_writes: bool,
    allowed: Optional[set[str]],
    show_url: bool,
) -> dict:
    """Kurze Timeouts + parallel nutzbar: UI bleibt bedienbar wenn einzelne PDNS nicht erreichbar sind."""
    out: dict = {"name": name}
    if show_url:
        out["url"] = client.url
    try:
        info = await client.get_server_info(timeout=STATUS_PROBE_TIMEOUT)
        zones = await client.list_zones(timeout=ZONES_LIST_PROBE_TIMEOUT)
        out.update(
            is_reachable=True,
            version=info.get("version"),
            daemon_type=info.get("daemon_type"),
            zone_count=_count_zones(zones, allowed),
            allow_writes=allow_writes,
        )
    except Exception:
        out.update(is_reachable=False, version=None, daemon_type=None, zone_count=None, allow_writes=allow_writes)
    return out


async def _allow_writes_map(db: AsyncSession) -> dict[str, bool]:
    """Returns {server_name: allow_writes} from DB. Servers without a row
    default to True (env-only servers). Defensive against missing column on
    legacy DBs."""
    if not hasattr(ServerConfig, "allow_writes"):
        return {}
    try:
        rows = (await db.execute(select(ServerConfig))).scalars().all()
        return {r.name: bool(getattr(r, "allow_writes", True)) for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load allow_writes flags: %s", exc)
        return {}


@router.get("")
async def list_servers(
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Alle konfigurierten PowerDNS-Server mit Status; ``url`` nur fuer Admins."""
    pairs = list(pdns_manager.get_all_clients().items())
    if not pairs:
        return {"servers": []}
    allowed = await effective_zone_filter(db, current_user)
    show_url = is_effective_admin(current_user)
    aw = await _allow_writes_map(db)
    servers = await asyncio.gather(
        *[_probe_server_status(n, c, aw.get(n, True), allowed, show_url) for n, c in pairs]
    )
    return {"servers": list(servers)}


@router.get("/{server_name}")
async def get_server_info(
    server_name: str,
    db: DbRead,
    current_user: User = Depends(get_current_user),
):
    """Details eines Servers; Statistik und ``url`` nur fuer Admins, ``zone_count`` gefiltert."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    admin = is_effective_admin(current_user)
    allowed = await effective_zone_filter(db, current_user)
    try:
        info = await client.get_server_info()
        stats = await client.get_statistics() if admin else []
        zones = await client.list_zones()
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    out = {
        "name": server_name,
        "info": info,
        "zone_count": _count_zones(zones, allowed),
        "statistics": stats[:20],
    }
    if admin:
        out["url"] = client.url
    return out


@router.get("/{server_name}/statistics")
async def get_server_statistics(
    server_name: str,
    current_user: User = Depends(get_current_user),
):
    """Vollstaendige Server-Statistik – nur fuer Admins (Session oder Token mit Admin-Freigabe)."""
    assert_not_zone_scoped(STATISTICS_ZONE_TOKEN_DETAIL)
    assert_effective_admin(current_user)
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        return await client.get_statistics()
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
