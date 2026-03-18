"""PowerDNS API Client Service.

This service handles all communication with PowerDNS servers via their HTTP API.
Supports multiple PowerDNS servers (e.g., DE and FR).
"""
import httpx
import logging
from typing import Optional
from app.core.config import settings

logger = logging.getLogger(__name__)


class PowerDNSClient:
    """Client for interacting with a single PowerDNS server's API."""

    def __init__(self, name: str, url: str, api_key: str):
        self.name = name
        self.url = url
        self.api_key = api_key
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: dict = None,
        params: dict = None,
        timeout: float = 30.0,
    ) -> dict | list | None:
        """Make an HTTP request to the PowerDNS API."""
        url = f"{self.url}/api/v1/servers/localhost{endpoint}"
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=json_data,
                    params=params,
                )
                
                if response.status_code == 204:
                    return None
                
                if response.status_code >= 400:
                    error_body = response.text
                    logger.error(
                        f"[{self.name}] PowerDNS API error {response.status_code}: {error_body}"
                    )
                    raise PowerDNSAPIError(
                        status_code=response.status_code,
                        detail=error_body,
                        server=self.name,
                    )
                
                if response.headers.get("content-type", "").startswith("application/json"):
                    return response.json()
                return response.text
                
            except httpx.ConnectError as e:
                logger.error(f"[{self.name}] Connection failed: {e}")
                raise PowerDNSAPIError(
                    status_code=503,
                    detail=f"Cannot connect to PowerDNS server '{self.name}' at {self.url}",
                    server=self.name,
                )
            except httpx.TimeoutException as e:
                logger.error(f"[{self.name}] Request timeout: {e}")
                raise PowerDNSAPIError(
                    status_code=504,
                    detail=f"Timeout connecting to PowerDNS server '{self.name}'",
                    server=self.name,
                )

    # ========================
    # Server Info
    # ========================
    async def get_server_info(self) -> dict:
        """Get PowerDNS server information."""
        return await self._request("GET", "")

    async def get_statistics(self) -> list:
        """Get PowerDNS server statistics."""
        return await self._request("GET", "/statistics")

    async def get_config(self) -> list:
        """Get PowerDNS server configuration."""
        return await self._request("GET", "/config")

    # ========================
    # Zone Management
    # ========================
    async def list_zones(self) -> list:
        """List all zones."""
        return await self._request("GET", "/zones")

    async def get_zone(self, zone_id: str) -> dict:
        """Get a specific zone with all records."""
        return await self._request("GET", f"/zones/{zone_id}")

    async def create_zone(self, zone_data: dict) -> dict:
        """Create a new zone.
        
        zone_data example:
        {
            "name": "example.com.",
            "kind": "Native",
            "nameservers": ["ns1.example.com.", "ns2.example.com."],
            "soa_edit_api": "DEFAULT",
        }
        """
        return await self._request("POST", "/zones", json_data=zone_data)

    async def update_zone(self, zone_id: str, zone_data: dict) -> None:
        """Update zone metadata (kind, masters, etc.)."""
        return await self._request("PUT", f"/zones/{zone_id}", json_data=zone_data)

    async def delete_zone(self, zone_id: str) -> None:
        """Delete a zone."""
        return await self._request("DELETE", f"/zones/{zone_id}")

    async def notify_zone(self, zone_id: str) -> None:
        """Send NOTIFY to all slaves for a zone."""
        return await self._request("PUT", f"/zones/{zone_id}/notify")

    async def get_zone_axfr(self, zone_id: str) -> str:
        """Export a zone in AXFR format (zonefile)."""
        return await self._request("GET", f"/zones/{zone_id}/export")

    async def rectify_zone(self, zone_id: str) -> None:
        """Rectify a zone (fix DNSSEC-related data)."""
        return await self._request("PUT", f"/zones/{zone_id}/rectify")

    # ========================
    # Record Management
    # ========================
    async def update_records(self, zone_id: str, rrsets: list[dict]) -> None:
        """Update records in a zone using RRsets.
        
        rrsets example:
        [
            {
                "name": "test.example.com.",
                "type": "A",
                "ttl": 3600,
                "changetype": "REPLACE",
                "records": [
                    {"content": "192.168.1.1", "disabled": False}
                ]
            }
        ]
        """
        return await self._request(
            "PATCH",
            f"/zones/{zone_id}",
            json_data={"rrsets": rrsets},
        )

    async def add_record(
        self,
        zone_id: str,
        name: str,
        record_type: str,
        content: list[str],
        ttl: int = 3600,
        disabled: bool = False,
    ) -> None:
        """Add or replace a record set."""
        rrsets = [
            {
                "name": name,
                "type": record_type,
                "ttl": ttl,
                "changetype": "REPLACE",
                "records": [
                    {"content": c, "disabled": disabled} for c in content
                ],
            }
        ]
        return await self.update_records(zone_id, rrsets)

    async def delete_record(
        self, zone_id: str, name: str, record_type: str
    ) -> None:
        """Delete a record set."""
        rrsets = [
            {
                "name": name,
                "type": record_type,
                "changetype": "DELETE",
            }
        ]
        return await self.update_records(zone_id, rrsets)

    # ========================
    # DNSSEC
    # ========================
    async def get_cryptokeys(self, zone_id: str) -> list:
        """Get all DNSSEC keys for a zone."""
        return await self._request("GET", f"/zones/{zone_id}/cryptokeys")

    async def add_cryptokey(self, zone_id: str, key_data: dict) -> dict:
        """Add a DNSSEC key to a zone.
        
        key_data example:
        {
            "keytype": "ksk",
            "active": True,
            "algorithm": "ECDSAP256SHA256",
            "bits": 256,
        }
        """
        return await self._request(
            "POST", f"/zones/{zone_id}/cryptokeys", json_data=key_data
        )

    async def get_cryptokey(self, zone_id: str, key_id: int) -> dict:
        """Get a specific DNSSEC key."""
        return await self._request("GET", f"/zones/{zone_id}/cryptokeys/{key_id}")

    async def activate_cryptokey(self, zone_id: str, key_id: int) -> None:
        """Activate a DNSSEC key."""
        return await self._request(
            "PUT",
            f"/zones/{zone_id}/cryptokeys/{key_id}",
            json_data={"active": True},
        )

    async def deactivate_cryptokey(self, zone_id: str, key_id: int) -> None:
        """Deactivate a DNSSEC key."""
        return await self._request(
            "PUT",
            f"/zones/{zone_id}/cryptokeys/{key_id}",
            json_data={"active": False},
        )

    async def delete_cryptokey(self, zone_id: str, key_id: int) -> None:
        """Delete a DNSSEC key."""
        return await self._request(
            "DELETE", f"/zones/{zone_id}/cryptokeys/{key_id}"
        )

    async def enable_dnssec(
        self,
        zone_id: str,
        algorithm: str = "ECDSAP256SHA256",
        nsec3param: str = "1 0 1 ab",
    ) -> dict:
        """Enable DNSSEC for a zone by creating a CSK (Combined Signing Key)."""
        # Create a CSK (automatically creates KSK+ZSK)
        key_data = {
            "keytype": "csk",
            "active": True,
            "algorithm": algorithm,
        }
        result = await self.add_cryptokey(zone_id, key_data)
        
        # Set NSEC3 parameters
        await self.update_zone(zone_id, {
            "nsec3param": nsec3param,
            "api_rectify": True,
        })
        
        # Rectify zone
        await self.rectify_zone(zone_id)
        
        return result

    async def disable_dnssec(self, zone_id: str) -> None:
        """Disable DNSSEC for a zone by removing all crypto keys."""
        keys = await self.get_cryptokeys(zone_id)
        for key in keys:
            await self.delete_cryptokey(zone_id, key["id"])

    # ========================
    # Metadata
    # ========================
    async def get_metadata(self, zone_id: str) -> list:
        """Get all metadata for a zone."""
        return await self._request("GET", f"/zones/{zone_id}/metadata")

    async def get_metadata_kind(self, zone_id: str, kind: str) -> dict:
        """Get specific metadata for a zone."""
        return await self._request("GET", f"/zones/{zone_id}/metadata/{kind}")

    async def set_metadata(self, zone_id: str, kind: str, value: list[str]) -> None:
        """Set metadata for a zone."""
        return await self._request(
            "PUT",
            f"/zones/{zone_id}/metadata/{kind}",
            json_data={"metadata": value},
        )

    # ========================
    # Search
    # ========================
    async def search(self, query: str, max_results: int = 100, object_type: str = "all") -> list:
        """Search for zones and/or records. Query is treated as substring: e.g. 'mygtg' finds 'mygtg.de'."""
        q = (query or "").strip()
        # PowerDNS supports * (any chars) and ? (one char). Wrap in * so partial match works.
        if q and "*" not in q and "?" not in q:
            q = f"*{q}*"
        return await self._request(
            "GET",
            "/search-data",
            params={
                "q": q,
                "max": max_results,
                "object_type": object_type,
            },
        )


class PowerDNSAPIError(Exception):
    """Exception raised when PowerDNS API returns an error."""

    def __init__(self, status_code: int, detail: str, server: str = "unknown"):
        self.status_code = status_code
        self.detail = detail
        self.server = server
        super().__init__(f"[{server}] PowerDNS API Error {status_code}: {detail}")


class PowerDNSManager:
    """Manages multiple PowerDNS server connections.

    Supports loading servers from:
    1. Database (ServerConfig table) - primary source
    2. Environment variables (PDNS_SERVERS) - fallback for initial setup
    """

    def __init__(self):
        self.clients: dict[str, PowerDNSClient] = {}
        self._load_from_env()

    def _load_from_env(self):
        """Load PowerDNS servers from environment (initial/fallback)."""
        for server in settings.get_pdns_servers():
            self.clients[server["name"]] = PowerDNSClient(
                name=server["name"],
                url=server["url"],
                api_key=server["api_key"],
            )
        if self.clients:
            logger.info(f"Loaded {len(self.clients)} PowerDNS servers from env: {list(self.clients.keys())}")
        else:
            logger.info("No PowerDNS servers in env. Configure them via the admin panel.")

    def load_from_db_configs(self, configs: list):
        """Load servers from database ServerConfig objects.
        Called during app startup after DB is available.
        """
        db_count = 0
        for cfg in configs:
            if not cfg.is_active:
                continue
            self.clients[cfg.name] = PowerDNSClient(
                name=cfg.name,
                url=cfg.url,
                api_key=cfg.api_key,
            )
            db_count += 1
        if db_count:
            logger.info(f"Loaded {db_count} PowerDNS servers from database")

    def add_server(self, name: str, url: str, api_key: str):
        """Dynamically add a server connection."""
        self.clients[name] = PowerDNSClient(name=name, url=url, api_key=api_key)
        logger.info(f"Added PowerDNS server '{name}' ({url})")

    def remove_server(self, name: str):
        """Remove a server connection."""
        if name in self.clients:
            del self.clients[name]
            logger.info(f"Removed PowerDNS server '{name}'")

    def update_server(self, name: str, url: str, api_key: str):
        """Update an existing server connection."""
        self.clients[name] = PowerDNSClient(name=name, url=url, api_key=api_key)
        logger.info(f"Updated PowerDNS server '{name}' ({url})")

    def get_client(self, server_name: str) -> PowerDNSClient:
        """Get a specific PowerDNS client by server name."""
        if server_name not in self.clients:
            available = list(self.clients.keys())
            raise ValueError(
                f"Server '{server_name}' not found. Available servers: {available}"
            )
        return self.clients[server_name]

    def get_all_clients(self) -> dict[str, PowerDNSClient]:
        """Get all PowerDNS clients."""
        return self.clients

    def list_servers(self) -> list[str]:
        """List all configured server names."""
        return list(self.clients.keys())


# Global instance
pdns_manager = PowerDNSManager()
