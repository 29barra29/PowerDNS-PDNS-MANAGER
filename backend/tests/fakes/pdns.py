"""In-Memory-PowerDNS fuer Unit-Tests (F7 9.1, Plan B.12).

- ``FakePowerDNSClient`` erbt von ``PowerDNSClient`` und ersetzt nur ``_request`` – damit
  laufen die echten Client-Methoden (``get_zone``, ``update_records``, ``delete_record``,
  ``get_rrsets`` ...) gegen den Fake.
- Jeder Fake-Server hat einen **eigenen** Zonenstand (``self.zones``), damit sich getrennte
  Backends mit unterschiedlichen Peer-Werten nachstellen lassen [D1].
- Fehlerinjektion: ``fail_on_patch`` / ``fail_on_get`` (dauerhaft), ``timeout_after_patch``
  (PATCH wird angewendet, danach Transportfehler – einmalig) [D3], ``timeout_before_patch``
  (Transportfehler ohne Anwendung – einmalig), ``fail_reads`` (die naechsten n Lesezugriffe
  scheitern mit Transportfehler).
- PowerDNS-Semantik des PATCH: REPLACE ohne ``comments``-Key behaelt Kommentare, leere
  Records (ohne Kommentare) loeschen das RRset, DELETE entfernt Records und Kommentare;
  Name ausserhalb der Zone -> 422; alle RRsets eines PATCH atomar.

Fixtures (in Tests importieren: ``from fakes.pdns import fake_pdns, fake_db``):
``fake_pdns`` setzt ``pdns_manager.clients = {"ns1": Fake, "ns2": Fake}`` und leert
``pdns_manager.unloaded``; ``fake_db`` liefert eine ``FakeDB`` ohne ServerConfig-Zeilen
(alle Server gelten als env-only und damit schreibbar).
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional
from urllib.parse import unquote

import pytest

from app.services.pdns_client import PowerDNSAPIError, PowerDNSClient, pdns_manager


def _norm(name: str) -> str:
    n = (name or "").strip().lower()
    if n and not n.endswith("."):
        n += "."
    return n


def make_zone(name: str, rrsets: Iterable[dict] = (), *, serial: int = 2026100501, kind: str = "Native",
              dnssec: bool = False) -> dict:
    """Zonen-JSON im PowerDNS-Format (``rrsets`` mit name/type/ttl/records/comments)."""
    z = _norm(name)
    out = []
    for rr in rrsets:
        r = {
            "name": _norm(rr["name"]),
            "type": str(rr["type"]).upper(),
            "ttl": int(rr.get("ttl", 3600)),
            "records": [
                {"content": rec["content"] if isinstance(rec, dict) else str(rec),
                 "disabled": bool(rec.get("disabled", False)) if isinstance(rec, dict) else False}
                for rec in rr.get("records", [])
            ],
            "comments": list(rr.get("comments", [])),
        }
        out.append(r)
    return {"id": z, "name": z, "kind": kind, "serial": serial, "edited_serial": serial,
            "dnssec": dnssec, "rrsets": out}


def rr(name: str, rtype: str, *contents: str, ttl: int = 3600, comments: Optional[list] = None,
       disabled: Iterable[str] = ()) -> dict:
    """Kurzform fuer ein RRset in ``make_zone``."""
    dis = set(disabled)
    return {"name": name, "type": rtype, "ttl": ttl,
            "records": [{"content": c, "disabled": c in dis} for c in contents],
            "comments": comments or []}


class FakePowerDNSClient(PowerDNSClient):
    """Fake-Server mit eigenem Zonenstand (siehe Modul-Docstring)."""

    def __init__(self, name: str, zones: Iterable[dict] = (), *, url: str = "http://fake-pdns",
                 api_key: str = "fake-key", filter_rrsets: bool = True,
                 normalize_content: Optional[Callable[[str, str], str]] = None):
        super().__init__(name, url, api_key)
        self.zones: dict[str, dict] = {}
        for z in zones:
            self.add_zone(z)
        self.calls: list[tuple[str, str, Any, Any, float]] = []
        self.patches: list[list[dict]] = []
        self.filter_rrsets = filter_rrsets  # False = alte PowerDNS (ignoriert rrset_name/rrset_type)
        self.normalize_content = normalize_content
        self.fail_on_patch: Optional[PowerDNSAPIError] = None
        self.fail_on_get: Optional[PowerDNSAPIError] = None
        self.timeout_after_patch: bool = False
        self.timeout_before_patch: bool = False
        self.timeout_status: int = 504
        self.fail_reads: int = 0
        self.server_info = {"type": "Server", "id": "localhost", "daemon_type": "authoritative", "version": "4.9.0"}
        self.config: list[dict] = [{"name": "enable-lua-records", "type": "ConfigSetting", "value": "no"}]

    # ------------------------------------------------------------------ Hilfen
    def add_zone(self, zone: dict) -> None:
        self.zones[_norm(zone.get("id") or zone["name"])] = copy.deepcopy(zone)

    def zone(self, zone_id: str) -> dict:
        return self.zones[_norm(zone_id)]

    def rrset(self, zone_id: str, name: str, rtype: str) -> Optional[dict]:
        for r in self.zone(zone_id)["rrsets"]:
            if _norm(r["name"]) == _norm(name) and r["type"].upper() == rtype.upper():
                return r
        return None

    def values(self, zone_id: str, name: str, rtype: str) -> list[str]:
        r = self.rrset(zone_id, name, rtype)
        return sorted(rec["content"] for rec in (r or {}).get("records", []))

    def count(self, method: str, prefix: str = "/zones") -> int:
        return sum(1 for m, ep, *_ in self.calls if m == method and ep.startswith(prefix))

    def _transport_error(self) -> PowerDNSAPIError:
        if self.timeout_status == 502:
            return PowerDNSAPIError(
                502, f"Verbindungsfehler zu PowerDNS-Server '{self.name}' (ReadError) – Ergebnis unklar, "
                     "bitte Zone neu laden.", self.name, transport_error=True)
        return PowerDNSAPIError(self.timeout_status, f"Timeout connecting to PowerDNS server '{self.name}'",
                                self.name, transport_error=True)

    def _not_found(self, zone_id: str) -> PowerDNSAPIError:
        return PowerDNSAPIError(404, json.dumps({"error": f"Could not find domain '{zone_id}'"}), self.name)

    # ------------------------------------------------------------------ _request
    async def _request(self, method, endpoint, json_data=None, params=None, timeout=30.0):
        self.calls.append((method, endpoint, copy.deepcopy(json_data), dict(params or {}), timeout))
        if endpoint == "" and method == "GET":
            return copy.deepcopy(self.server_info)
        if endpoint == "/config" and method == "GET":
            return copy.deepcopy(self.config)
        if endpoint == "/zones" and method == "GET":
            return [{"id": z["id"], "name": z["name"], "kind": z.get("kind", "Native"),
                     "serial": z.get("serial", 0), "dnssec": z.get("dnssec", False)} for z in self.zones.values()]
        if endpoint == "/zones" and method == "POST":
            z = make_zone(json_data["name"], json_data.get("rrsets", []), kind=json_data.get("kind", "Native"))
            if _norm(z["name"]) in self.zones:
                raise PowerDNSAPIError(409, json.dumps({"error": "Domain already exists"}), self.name)
            self.add_zone(z)
            return copy.deepcopy(z)
        if not endpoint.startswith("/zones/"):
            raise PowerDNSAPIError(404, json.dumps({"error": "Not Found"}), self.name)

        rest = endpoint[len("/zones/"):]
        zone_part, _, sub = rest.partition("/")
        zid = _norm(unquote(zone_part))
        zone = self.zones.get(zid)

        if method == "GET" and self.fail_reads > 0:
            self.fail_reads -= 1
            raise self._transport_error()
        if method == "GET" and self.fail_on_get is not None:
            raise self.fail_on_get
        if zone is None:
            raise self._not_found(zone_part)

        if sub == "notify" and method == "PUT":
            return {"result": "Notification queued"}
        if sub == "cryptokeys" and method == "GET":
            return copy.deepcopy(zone.get("cryptokeys", []))
        if sub == "rectify" and method == "PUT":
            return {"result": "Rectified"}
        if sub:
            raise PowerDNSAPIError(404, json.dumps({"error": "Not Found"}), self.name)

        if method == "GET":
            out = copy.deepcopy(zone)
            p = params or {}
            if str(p.get("rrsets", "")).lower() == "false":
                out.pop("rrsets", None)
            elif self.filter_rrsets and p.get("rrset_name"):
                n = _norm(p["rrset_name"])
                t = (p.get("rrset_type") or "").upper()
                out["rrsets"] = [r for r in out["rrsets"] if _norm(r["name"]) == n and (not t or r["type"] == t)]
            return out
        if method == "PATCH":
            return self._patch(zid, zone, (json_data or {}).get("rrsets", []))
        if method == "PUT":
            for k, v in (json_data or {}).items():
                if k != "rrsets":
                    zone[k] = v
            return None
        if method == "DELETE":
            del self.zones[zid]
            return None
        raise PowerDNSAPIError(405, json.dumps({"error": "Method not allowed"}), self.name)

    def _patch(self, zid: str, zone: dict, rrsets: list[dict]):
        if self.timeout_before_patch:
            self.timeout_before_patch = False
            raise self._transport_error()
        if self.fail_on_patch is not None:
            raise self.fail_on_patch
        # Validierung vor der Anwendung (PATCH ist atomar)
        for r in rrsets:
            n = _norm(r.get("name", ""))
            if not (n == zid or n.endswith("." + zid)):
                raise PowerDNSAPIError(
                    422, json.dumps({"error": f"RRset {r.get('name')} IN {r.get('type')}: Name is out of zone"}), self.name)
            ct = str(r.get("changetype", "")).upper()
            if ct not in ("REPLACE", "DELETE"):
                raise PowerDNSAPIError(422, json.dumps({"error": "Changetype not understood"}), self.name)
            if ct == "REPLACE" and r.get("records") and r.get("ttl") is None:
                raise PowerDNSAPIError(422, json.dumps({"error": "Key 'ttl' not present or not an Integer"}), self.name)
        self.patches.append(copy.deepcopy(rrsets))
        for r in rrsets:
            n = _norm(r["name"])
            t = str(r["type"]).upper()
            ct = str(r["changetype"]).upper()
            idx = next((i for i, x in enumerate(zone["rrsets"]) if _norm(x["name"]) == n and x["type"] == t), None)
            existing = zone["rrsets"][idx] if idx is not None else None
            if ct == "DELETE":
                if idx is not None:
                    zone["rrsets"].pop(idx)
                continue
            records = [
                {"content": self._normalize(t, rec["content"]), "disabled": bool(rec.get("disabled", False))}
                for rec in (r.get("records") or [])
            ]
            comments = list(r["comments"]) if "comments" in r else list((existing or {}).get("comments", []))
            if not records and not comments:
                if idx is not None:
                    zone["rrsets"].pop(idx)
                continue
            new = {"name": n, "type": t, "ttl": int(r.get("ttl") or (existing or {}).get("ttl") or 0),
                   "records": records, "comments": comments}
            if idx is None:
                zone["rrsets"].append(new)
            else:
                zone["rrsets"][idx] = new
        zone["serial"] = int(zone.get("serial", 0)) + 1
        zone["edited_serial"] = zone["serial"]
        if self.timeout_after_patch:
            self.timeout_after_patch = False
            raise self._transport_error()
        return None

    def _normalize(self, rtype: str, content: str) -> str:
        return self.normalize_content(rtype, content) if self.normalize_content else content


# ---------------------------------------------------------------------- Fake-DB
class FakeResult:
    def __init__(self, rows: Iterable[Any] = ()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeDB:
    """Minimal-Session: ``execute`` liefert die ServerConfig-Zeilen (oder ``execute_handler``)."""

    def __init__(self, server_configs: Iterable[Any] = (), objects: Optional[dict] = None,
                 execute_handler: Optional[Callable[[Any], Any]] = None):
        self.server_configs = list(server_configs)
        self.objects = dict(objects or {})
        self.execute_handler = execute_handler
        self.fail_execute: Optional[BaseException] = None
        self.executed: list[Any] = []
        self.added: list[Any] = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        self.executed.append(stmt)
        if self.fail_execute is not None:
            raise self.fail_execute
        if self.execute_handler is not None:
            return self.execute_handler(stmt)
        return FakeResult(self.server_configs)

    async def get(self, model, ident):
        return self.objects.get((model, ident)) or self.objects.get((getattr(model, "__name__", model), ident))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def begin_nested(self):
        return _Nested()


def server_config(name: str, *, allow_writes: bool = True, is_active: bool = True,
                  url: str = "http://fake-pdns", api_key: str = "fake-key") -> SimpleNamespace:
    """ServerConfig-aehnliche Zeile fuer ``FakeDB``."""
    return SimpleNamespace(name=name, allow_writes=allow_writes, is_active=is_active, url=url, api_key=api_key)


# ---------------------------------------------------------------------- Fixtures
@pytest.fixture
def fake_pdns(monkeypatch):
    """Zwei Fake-Server ``ns1``/``ns2`` mit getrennten Zonenstaenden im globalen ``pdns_manager``."""
    ns1 = FakePowerDNSClient("ns1")
    ns2 = FakePowerDNSClient("ns2")
    monkeypatch.setattr(pdns_manager, "clients", {"ns1": ns1, "ns2": ns2})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    return SimpleNamespace(ns1=ns1, ns2=ns2, manager=pdns_manager)


@pytest.fixture
def fake_db():
    """FakeDB ohne ServerConfig-Zeilen (alle Server env-only = schreibbar)."""
    return FakeDB()
