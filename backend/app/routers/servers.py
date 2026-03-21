"""API routes for server management."""
import asyncio
from fastapi import APIRouter, HTTPException
from app.services.pdns_client import (
    pdns_manager,
    PowerDNSAPIError,
    STATUS_PROBE_TIMEOUT,
    ZONES_LIST_PROBE_TIMEOUT,
)
from app.schemas.dns import ServerInfo, ServerListResponse

router = APIRouter(prefix="/servers", tags=["Servers"])


async def _probe_server_status(name: str, client) -> ServerInfo:
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
        )
    except Exception:
        return ServerInfo(
            name=name,
            url=client.url,
            is_reachable=False,
        )


@router.get("", response_model=ServerListResponse)
async def list_servers():
    """List all configured PowerDNS servers and their status."""
    pairs = list(pdns_manager.get_all_clients().items())
    if not pairs:
        return ServerListResponse(servers=[])
    servers = await asyncio.gather(*[_probe_server_status(n, c) for n, c in pairs])
    return ServerListResponse(servers=list(servers))


@router.get("/{server_name}")
async def get_server_info(server_name: str):
    """Get detailed information about a specific server."""
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
            "statistics": stats[:20],  # Top 20 stats
        }
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/{server_name}/statistics")
async def get_server_statistics(server_name: str):
    """Get full server statistics."""
    try:
        client = pdns_manager.get_client(server_name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    try:
        return await client.get_statistics()
    except PowerDNSAPIError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
