"""PowerDNS API Client Service.

This service handles all communication with PowerDNS servers via their HTTP API.
Supports multiple PowerDNS servers (e.g., DE and FR).
"""
import json
import logging
import time
from typing import Optional

import httpx

from app.core import metrics as prom
from app.core.config import settings

logger = logging.getLogger(__name__)

# Kurze Timeouts nur für Status-/Listen-APIs (Dashboard, Einstellungen), damit die UI bei offline-Servern nicht Minuten wartet.
# Volle Operationen (Zonen anlegen, …) nutzen weiterhin den Default in _request (30s).
STATUS_PROBE_TIMEOUT = 8.0
ZONES_LIST_PROBE_TIMEOUT = 25.0


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
        """Make an HTTP request to the PowerDNS API.

        Jeder Aufruf wird in ``pdnsmgr_pdns_api_requests_total`` (Server, Methode,
        Status-Klasse) gezaehlt. Transportfehler nach dem Verbindungsaufbau
        (ReadError, RemoteProtocolError, Timeouts, ...) werden zu
        ``PowerDNSAPIError`` mit ``transport_error=True`` – das Ergebnis eines
        schreibenden Aufrufs ist dann unklar (Fan-out prueft per Re-Read nach).
        """
        url = f"{self.url}/api/v1/servers/localhost{endpoint}"
        t0 = time.perf_counter()
        status_label = "error"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                try:
                    response = await client.request(
                        method=method,
                        url=url,
                        headers=self.headers,
                        json=json_data,
                        params=params,
                    )
                    status_label = f"{response.status_code // 100}xx"

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
                    status_label = "connect_error"
                    logger.error(f"[{self.name}] Connection failed: {e}")
                    raise PowerDNSAPIError(
                        status_code=503,
                        detail=f"Cannot connect to PowerDNS server '{self.name}' at {self.url}",
                        server=self.name,
                    )
                except httpx.TimeoutException as e:
                    status_label = "timeout"
                    logger.error(f"[{self.name}] Request timeout: {e}")
                    raise PowerDNSAPIError(
                        status_code=504,
                        detail=f"Timeout connecting to PowerDNS server '{self.name}'",
                        server=self.name,
                        transport_error=True,
                    )
                except httpx.HTTPError as e:
                    # ReadError, RemoteProtocolError, UnsupportedProtocol, ProxyError, ... (nach
                    # ConnectError/TimeoutException, die beide Unterklassen von HTTPError sind)
                    status_label = "transport_error"
                    logger.error(f"[{self.name}] Transport error: {type(e).__name__}: {e}")
                    raise PowerDNSAPIError(
                        status_code=502,
                        detail=(
                            f"Verbindungsfehler zu PowerDNS-Server '{self.name}' ({type(e).__name__}) "
                            "– Ergebnis unklar, bitte Zone neu laden."
                        ),
                        server=self.name,
                        transport_error=True,
                    )
        finally:
            prom.observe_pdns_request(self.name, method, status_label, time.perf_counter() - t0)

    # ========================
    # Server Info
    # ========================
    async def get_server_info(self, timeout: float = 30.0) -> dict:
        """Get PowerDNS server information."""
        return await self._request("GET", "", timeout=timeout)

    async def get_statistics(self) -> list:
        """Get PowerDNS server statistics."""
        return await self._request("GET", "/statistics")

    async def get_config(self, timeout: float = 30.0) -> list:
        """Get PowerDNS server configuration."""
        return await self._request("GET", "/config", timeout=timeout)

    # ========================
    # Zone Management
    # ========================
    async def list_zones(self, timeout: float = 30.0) -> list:
        """List all zones."""
        return await self._request("GET", "/zones", timeout=timeout)

    async def get_zone(self, zone_id: str, *, timeout: float = 30.0) -> dict:
        """Get a specific zone with all records."""
        return await self._request("GET", f"/zones/{zone_id}", timeout=timeout)

    async def get_zone_meta(self, zone_id: str, *, timeout: float = 30.0) -> dict:
        """Zonen-Metadaten ohne RRsets (``?rrsets=false``, PowerDNS >= 4.3).

        Aeltere PowerDNS-Versionen ignorieren den Parameter und liefern die volle Zone –
        funktional gleich, nur langsamer.
        """
        return await self._request("GET", f"/zones/{zone_id}", params={"rrsets": "false"}, timeout=timeout)

    async def get_zone_rrset(self, zone_id: str, name: str, rtype: str, *, timeout: float = 30.0) -> dict:
        """Zone mit serverseitig gefiltertem RRset (``rrset_name``/``rrset_type``).

        Neuere PowerDNS-Versionen filtern, aeltere liefern die volle Zone – Aufrufer
        filtern deshalb IMMER lokal (``rrsets.rrset_snapshot``).
        """
        params = {"rrset_name": _abs_name(name), "rrset_type": (rtype or "").upper()}
        return await self._request("GET", f"/zones/{zone_id}", params=params, timeout=timeout)

    async def get_rrsets(
        self, zone_id: str, name: str, rtype: str | None = None, *, timeout: float = 10.0
    ) -> list[dict]:
        """RRsets eines Namens (optional eines Typs), lokal gefiltert (case-insensitiv)."""
        n = _abs_name(name)
        params = {"rrset_name": n}
        t = (rtype or "").upper() or None
        if t:
            params["rrset_type"] = t
        zone = await self._request("GET", f"/zones/{zone_id}", params=params, timeout=timeout)
        rrsets = (zone.get("rrsets") or []) if isinstance(zone, dict) else []
        out = []
        for rr in rrsets:
            if _abs_name(str(rr.get("name", ""))) != n:
                continue
            if t is not None and str(rr.get("type", "")).upper() != t:
                continue
            out.append(rr)
        return out

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

    async def notify_zone(self, zone_id: str, *, timeout: float = 10.0) -> dict:
        """NOTIFY an alle Secondaries der Zone senden (PUT ``/zones/{id}/notify``).

        PowerDNS antwortet mit ``{"result": "Notification queued"}``; andere Antworten
        werden in ``{"result": <text>}`` verpackt, eine leere Antwort ergibt ``{}``.
        """
        result = await self._request("PUT", f"/zones/{zone_id}/notify", timeout=timeout)
        if isinstance(result, dict):
            return result
        if result:
            return {"result": str(result)}
        return {}

    async def get_zone_axfr(self, zone_id: str) -> str:
        """Export a zone in AXFR format (zonefile)."""
        return await self._request("GET", f"/zones/{zone_id}/export")

    async def rectify_zone(self, zone_id: str) -> None:
        """Rectify a zone (fix DNSSEC-related data)."""
        return await self._request("PUT", f"/zones/{zone_id}/rectify")

    # ========================
    # Record Management
    # ========================
    async def update_records(self, zone_id: str, rrsets: list[dict], *, timeout: float = 30.0) -> None:
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
            timeout=timeout,
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
        self, zone_id: str, name: str, record_type: str, content: str | None = None
    ) -> None:
        """Delete a record set – or, with ``content``, only that single value.

        PowerDNS kennt nur RRset-weite DELETEs. Fuer einen Einzelwert holen wir das
        aktuelle RRset, entfernen den Wert und schreiben den Rest per REPLACE zurueck
        (TTL, disabled-Flags und Kommentare bleiben erhalten). Ist der Wert der letzte
        im RRset, wird das RRset per DELETE entfernt. Fehlt das RRset oder der Wert,
        wird RecordNotFoundError (404) geworfen. Ohne ``content`` bleibt DELETE idempotent.
        """
        if content is not None:
            zone = await self.get_zone(zone_id)
            for rrset in zone.get("rrsets", []):
                if (
                    str(rrset.get("name", "")).lower() == name.lower()
                    and str(rrset.get("type", "")).upper() == record_type.upper()
                ):
                    records = rrset.get("records", [])
                    remaining = [r for r in records if r.get("content") != content]
                    if len(remaining) == len(records):
                        raise RecordNotFoundError(
                            404, f"Wert '{content}' nicht im RRset {name} {record_type} vorhanden",
                            getattr(self, "name", getattr(self, "server_name", "unknown")),
                        )
                    if remaining:
                        replace = {
                            "name": name,
                            "type": record_type,
                            "ttl": rrset.get("ttl"),
                            "changetype": "REPLACE",
                            "records": [
                                {"content": r.get("content"), "disabled": bool(r.get("disabled", False))}
                                for r in remaining
                            ],
                        }
                        if rrset.get("comments"):
                            replace["comments"] = rrset["comments"]
                        return await self.update_records(zone_id, [replace])
                    break
            else:
                raise RecordNotFoundError(
                    404, f"RRset {name} {record_type} nicht vorhanden",
                    getattr(self, "name", getattr(self, "server_name", "unknown")),
                )
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
    async def get_cryptokeys(self, zone_id: str, timeout: float = 30.0) -> list:
        """Get all DNSSEC keys for a zone."""
        return await self._request("GET", f"/zones/{zone_id}/cryptokeys", timeout=timeout)

    async def update_cryptokey(self, zone_id: str, key_id: int, data: dict) -> None:
        """Schluessel-Eigenschaften setzen (``active``/``published``), PUT ``/cryptokeys/{id}``."""
        return await self._request("PUT", f"/zones/{zone_id}/cryptokeys/{key_id}", json_data=data)

    async def set_nsec3(self, zone_id: str, nsec3param: str, narrow: bool) -> None:
        """NSEC3-Parameter setzen; leerer ``nsec3param`` schaltet auf NSEC zurueck."""
        return await self.update_zone(zone_id, {
            "nsec3param": nsec3param,
            "nsec3narrow": bool(narrow) if nsec3param else False,
            "api_rectify": True,
        })

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
        """Search for zones and/or records. Query is treated as substring: e.g. 'example' finds 'example.de'."""
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
    """Exception raised when PowerDNS API returns an error.

    ``transport_error`` ist True, wenn die Verbindung nach dem Senden abbrach bzw. in ein
    Timeout lief – das Ergebnis eines schreibenden Aufrufs ist dann unklar.
    """

    def __init__(self, status_code: int, detail: str, server: str = "unknown", *, transport_error: bool = False):
        self.status_code = status_code
        self.detail = detail
        self.server = server
        self.transport_error = bool(transport_error)
        super().__init__(f"[{server}] PowerDNS API Error {status_code}: {detail}")

    @property
    def pdns_message(self) -> str:
        """Lesbarer Fehlertext: ``{"error": "..."}`` aus dem PowerDNS-Body, sonst der Rohtext (max. 500 Zeichen)."""
        raw = self.detail if isinstance(self.detail, str) else str(self.detail)
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("error"), str):
                return data["error"][:500]
        except (ValueError, TypeError):
            pass
        return (raw or "").strip()[:500]


def pdns_error_text(exc: PowerDNSAPIError) -> str:
    """Einheitlicher Fehlertext fuer Antworten: ``PowerDNS (<server>): <meldung>``."""
    return f"PowerDNS ({getattr(exc, 'server', 'unknown')}): {exc.pdns_message}"


def _abs_name(name: str) -> str:
    """RR-Name fuer PowerDNS-Filter: lower + Trailing-Dot (PowerDNS erwartet absolute Namen)."""
    n = (name or "").strip().lower()
    if n and not n.endswith("."):
        n += "."
    return n


class RecordNotFoundError(PowerDNSAPIError):
    """Der zu loeschende Wert bzw. das RRset existiert nicht (Zone selbst ist vorhanden).

    Eigene Klasse, damit die Fan-out-Handler das nicht mit "Zone fehlt auf diesem Server"
    verwechseln und dem Nutzer ein sauberes 404 statt 502 liefern.
    """


class PowerDNSManager:
    """Manages multiple PowerDNS server connections.

    Supports loading servers from:
    1. Database (ServerConfig table) - primary source
    2. Environment variables (PDNS_SERVERS) - fallback for initial setup
    """

    def __init__(self):
        self.clients: dict[str, PowerDNSClient] = {}
        # Konfigurierte, aber nicht geladene Server (Name -> Grund, z. B. "api key unreadable").
        # Fan-out, Zonen-Index und Propagation melden sie als "skipped (not loaded: <grund>)".
        self.unloaded: dict[str, str] = {}
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

    def load_from_db_configs(self, configs: list) -> list[str]:
        """Load servers from database ServerConfig objects.
        Called during app startup after DB is available.

        Liefert die Namen der wegen unlesbarem/leerem API-Key NICHT geladenen Server; sie
        stehen danach in ``self.unloaded`` (Name -> Grund). Die DB ist fuehrend, auch
        gegenueber einem PDNS_SERVERS-Env-Client gleichen Namens.
        """
        from app.core.secrets import is_unreadable  # lazy: core.secrets importiert Models

        db_count = 0
        skipped: list[str] = []
        for cfg in configs:
            if not cfg.is_active:
                continue
            key = cfg.api_key
            if is_unreadable(key) or not (key or "").strip():
                reason = "api key unreadable" if is_unreadable(key) else "api key empty"
                self.mark_unloaded(cfg.name, reason)
                logger.error(
                    "PowerDNS-Server '%s' nicht geladen: API-Key %s – bitte unter Einstellungen -> Server neu eintragen.",
                    cfg.name, "nicht entschluesselbar" if is_unreadable(key) else "leer",
                )
                skipped.append(cfg.name)
                continue
            self.clients[cfg.name] = PowerDNSClient(
                name=cfg.name,
                url=cfg.url,
                api_key=key,
            )
            self.unloaded.pop(cfg.name, None)
            db_count += 1
        if db_count:
            logger.info(f"Loaded {db_count} PowerDNS servers from database")
        return skipped

    def mark_unloaded(self, name: str, reason: str) -> None:
        """Server als konfiguriert, aber nicht geladen markieren (entfernt einen evtl. vorhandenen Client)."""
        self.clients.pop(name, None)
        self.unloaded[name] = str(reason or "unknown")

    def add_server(self, name: str, url: str, api_key: str):
        """Dynamically add a server connection."""
        self.clients[name] = PowerDNSClient(name=name, url=url, api_key=api_key)
        self.unloaded.pop(name, None)
        logger.info(f"Added PowerDNS server '{name}' ({url})")

    def remove_server(self, name: str):
        """Remove a server connection (auch eine evtl. Markierung als nicht geladen)."""
        self.unloaded.pop(name, None)
        if name in self.clients:
            del self.clients[name]
            logger.info(f"Removed PowerDNS server '{name}'")

    def update_server(self, name: str, url: str, api_key: str):
        """Update an existing server connection."""
        self.clients[name] = PowerDNSClient(name=name, url=url, api_key=api_key)
        self.unloaded.pop(name, None)
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
