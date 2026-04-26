"""API routes for server management (read-only status). Auth required."""
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import User, ServerConfig
from app.services.pdns_client import (
    pdns_manager,
    PowerDNSAPIError,
    STATUS_PROBE_TIMEOUT,
    ZONES_LIST_PROBE_TIMEOUT,
)
from app.schemas.dns import ServerInfo, ServerListResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/servers", tags=["Servers"])


async def _probe_server_status(name: str, client, allow_writes: bool) -> ServerInfo:
    """Kurze Timeouts + parallel nutzbar: UI bleibt bedienbar wenn einzelne PDNS nicht erreichbar sind."""
    try:
        info = await client.get_server_info(timeout=STATUS_PROBE_TIMEOUT)
        zones = await client.list_zones(timeout=ZONES_LIST_PROBE_TIMEOUT)
        return ServerInfo(
            name=name,
            url=client.url,
            is_reachable=True,
            version=info.get("version"),
            daemon_type=info.get("daemon_type"),
            zone_count=len(zones),
            allow_writes=allow_writes,
        )
    except Exception:
        return ServerInfo(
            name=name,
            url=client.url,
            is_reachable=False,
            allow_writes=allow_writes,
        )


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


@router.get("", response_model=ServerListResponse)
async def list_servers(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all configured PowerDNS servers and their status (Auth)."""
    pairs = list(pdns_manager.get_all_clients().items())
    if not pairs:
        return ServerListResponse(servers=[])
    aw = await _allow_writes_map(db)
    servers = await asyncio.gather(
        *[_probe_server_status(n, c, aw.get(n, True)) for n, c in pairs]
    )
    return ServerListResponse(servers=list(servers))


@router.get("/{server_name}")
async def get_server_info(
    server_name: str,
    _: User = Depends(get_current_user),
):
    """Get detailed information about a specific server (Auth)."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        info = await client.get_server_info()
        stats = await client.get_statistics()
        zones = await client.list_zones()

        return {
            "name": server_name,
            "url": client.url,
            "info": info,
            "zone_count": len(zones),
            "statistics": stats[:20],
        }
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/{server_name}/statistics")
async def get_server_statistics(
    server_name: str,
    _: User = Depends(get_current_user),
):
    """Get full server statistics (Auth)."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        return await client.get_statistics()
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
