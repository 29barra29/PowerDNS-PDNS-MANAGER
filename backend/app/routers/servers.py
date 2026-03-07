"""API routes for server management."""
from fastapi import APIRouter, HTTPException
from app.services.pdns_client import pdns_manager, PowerDNSAPIError
from app.schemas.dns import ServerInfo, ServerListResponse

router = APIRouter(prefix="/servers", tags=["Servers"])


@router.get("", response_model=ServerListResponse)
async def list_servers():
    """List all configured PowerDNS servers and their status."""
    servers = []
    
    for name, client in pdns_manager.get_all_clients().items():
        try:
            info = await client.get_server_info()
            zones = await client.list_zones()
            servers.append(ServerInfo(
                name=name,
                url=client.url,
                is_reachable=True,
                version=info.get("version"),
                daemon_type=info.get("daemon_type"),
                zone_count=len(zones),
            ))
        except Exception as e:
            servers.append(ServerInfo(
                name=name,
                url=client.url,
                is_reachable=False,
            ))
    
    return ServerListResponse(servers=servers)


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
