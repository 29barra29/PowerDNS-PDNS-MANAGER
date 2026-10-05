"""Test-Hilfen fuer DNSSEC (F4 9): In-Memory-PowerDNS mit Cryptokeys, Ereignis-Recorder, Test-App.

``FakePdnsClient`` erbt von ``fakes.pdns.FakePowerDNSClient`` (Zonen, PATCH-Semantik, Serial, NOTIFY) und ergaenzt
die Cryptokey-API von PowerDNS:

- Schluessel je Zone mit aufsteigenden IDs, ``dnskey`` aus festen Testvektoren (deterministisch je Zone/ID/
  Algorithmus), ``ds`` (Digest 1, 2, 4) fuer SEP-Schluessel (Flags 257), **``privatekey`` in jeder Antwort**
  (damit das Entfernen geprueft werden kann), ``published`` nur ab PowerDNS 4.3.
- Zonen-Metadaten: ``kind``, ``presigned``, ``nsec3param``, ``nsec3narrow``, ``api_rectify``, ``dnssec``.
- ``ops``: logische Aufrufe in Reihenfolge (``get_zone_meta``, ``get_cryptokeys``, ``add_cryptokey``,
  ``update_cryptokey``, ``delete_cryptokey``, ``update_zone``, ``rectify_zone``, ``notify_zone``,
  ``update_records``, ``get_rrsets``, ``get_zone``, ``get_server_info``, ``get_cryptokey``); ``calls`` (geerbt):
  HTTP-Ebene ``(methode, endpoint, json, params, timeout)``.
- Fehlerinjektion ``fail_on={"add_cryptokey#2": PowerDNSAPIError(...)}`` (zweiter Aufruf) bzw. ohne ``#n`` (jeder).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import unquote

import dns.dnssec
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype

from fakes.pdns import FakePowerDNSClient, _norm, make_zone, rr
from app.services.pdns_client import PowerDNSAPIError

PRIVATE = "Private-key-format: v1.2\nAlgorithm: 13 (ECDSAP256SHA256)\nPrivateKey: GEHEIM\n"
ALG_NUMBERS = {"ECDSAP256SHA256": 13, "ECDSAP384SHA384": 14, "ED25519": 15, "ED448": 16, "RSASHA256": 8,
               "RSASHA512": 10, "RSASHA1": 5, "RSASHA1-NSEC3-SHA1": 7}
ALG_BITS = {13: 256, 14: 384, 15: 256, 16: 456}


def pdns_error(status: int, msg: str, server: str = "srv1") -> PowerDNSAPIError:
    return PowerDNSAPIError(status, json.dumps({"error": msg}), server)


def soa(zone: str, serial: int = 2026100501) -> str:
    return f"ns1.{zone} hostmaster.{zone} {serial} 10800 3600 604800 3600"


class FakePdnsClient(FakePowerDNSClient):
    def __init__(self, name: str = "srv1", *, version: Optional[str] = "4.9.4", kind: str = "Native",
                 presigned: bool = False, zones: tuple[str, ...] = ("example.com.",),
                 fail_on: Optional[dict[str, PowerDNSAPIError]] = None, published_field: Optional[bool] = None):
        super().__init__(name, [])
        self.version = version
        self.server_info = {"type": "Server", "id": "localhost", "daemon_type": "authoritative"}
        if version is not None:
            self.server_info["version"] = version
        if published_field is None:
            published_field = version is None or _version_tuple(version) >= (4, 3, 0)
        self.published_field = published_field
        self.fail_on: dict[str, PowerDNSAPIError] = dict(fail_on or {})
        self.ops: list[str] = []
        self.op_counts: dict[str, int] = {}
        self.keys: dict[str, list[dict]] = {}
        self.next_id = 1
        for z in zones:
            self.add_test_zone(z, kind=kind, presigned=presigned)

    # ------------------------------------------------------------------ Aufbau
    def add_test_zone(self, zone: str, *, kind: str = "Native", presigned: bool = False, serial: int = 2026100501):
        z = _norm(zone)
        data = make_zone(z, [rr(z, "SOA", soa(z, serial)), rr(z, "NS", f"ns1.{z}"), rr(f"www.{z}", "A", "192.0.2.1")],
                         kind=kind, serial=serial)
        data.update({"presigned": presigned, "nsec3param": "", "nsec3narrow": False, "api_rectify": False})
        self.add_zone(data)
        self.keys.setdefault(z, [])

    def add_key(self, zone: str, keytype: str = "csk", *, active: bool = True, published: bool = True,
                algorithm: str = "ECDSAP256SHA256", flags: Optional[int] = None, bits: Optional[int] = None,
                key_id: Optional[int] = None) -> dict:
        z = _norm(zone)
        kid = key_id if key_id is not None else self.next_id
        self.next_id = max(self.next_id, kid) + 1
        number = ALG_NUMBERS.get(algorithm.upper(), 13)
        if flags is None:
            flags = 256 if keytype == "zsk" else 257
        pub = base64.b64encode(hashlib.sha512(f"{z}|{kid}|{number}".encode()).digest()).decode()
        dnskey = f"{flags} 3 {number} {pub}"
        key = {"type": "Cryptokey", "id": kid, "keytype": keytype, "active": bool(active), "dnskey": dnskey,
               "algorithm": algorithm.upper(), "bits": bits or ALG_BITS.get(number, 2048), "privatekey": PRIVATE}
        if self.published_field:
            key["published"] = bool(published)
        if flags == 257:
            key["ds"] = _ds_for(z, dnskey)
        self.keys.setdefault(z, []).append(key)
        self._sync_dnssec_flag(z)
        return key

    def _sync_dnssec_flag(self, z: str) -> None:
        if z in self.zones:
            self.zones[z]["dnssec"] = any(k["active"] for k in self.keys.get(z, []))

    # ------------------------------------------------------------------ Lesen fuer Tests
    def key(self, zone: str, key_id: int) -> Optional[dict]:
        return next((k for k in self.keys.get(_norm(zone), []) if k["id"] == key_id), None)

    def key_ids(self, zone: str) -> list[int]:
        return [k["id"] for k in self.keys.get(_norm(zone), [])]

    def meta(self, zone: str) -> dict:
        return self.zones[_norm(zone)]

    def serial(self, zone: str) -> int:
        r = self.rrset(zone, zone, "SOA")
        return int(r["records"][0]["content"].split()[2])

    def bodies(self, method: str, suffix: str) -> list[Any]:
        return [c[2] for c in self.calls if c[0] == method and c[1].endswith(suffix)]

    def writes(self) -> list[str]:
        return [o for o in self.ops if o in WRITE_OPS]

    # ------------------------------------------------------------------ _request
    def _op_name(self, method: str, endpoint: str, params: Optional[dict]) -> str:
        if endpoint == "":
            return "get_server_info"
        parts = endpoint.split("/")  # ["", "zones", "<z>", ...]
        sub = parts[3:] if len(parts) > 3 else []
        if sub[:1] == ["cryptokeys"]:
            if len(sub) == 1:
                return {"GET": "get_cryptokeys", "POST": "add_cryptokey"}.get(method, f"{method} cryptokeys")
            return {"GET": "get_cryptokey", "PUT": "update_cryptokey", "DELETE": "delete_cryptokey"}.get(
                method, f"{method} cryptokey")
        if sub[:1] == ["rectify"]:
            return "rectify_zone"
        if sub[:1] == ["notify"]:
            return "notify_zone"
        if method == "GET":
            p = params or {}
            if str(p.get("rrsets", "")).lower() == "false":
                return "get_zone_meta"
            if p.get("rrset_name"):
                return "get_rrsets"
            return "get_zone"
        return {"PUT": "update_zone", "PATCH": "update_records", "DELETE": "delete_zone"}.get(method, method)

    async def _request(self, method, endpoint, json_data=None, params=None, timeout=30.0):
        op = self._op_name(method, endpoint, params)
        self.op_counts[op] = self.op_counts.get(op, 0) + 1
        self.ops.append(op)
        exc = self.fail_on.get(f"{op}#{self.op_counts[op]}") or self.fail_on.get(op)
        if "/cryptokeys" not in endpoint or exc is not None:
            if exc is not None:
                self.calls.append((method, endpoint, copy.deepcopy(json_data), dict(params or {}), timeout))
                raise exc
            if op == "update_zone" and (json_data or {}).get("nsec3param"):
                z = _norm(unquote(endpoint.split("/")[2]))
                if z in self.zones and not self.keys.get(z):
                    self.calls.append((method, endpoint, copy.deepcopy(json_data), dict(params or {}), timeout))
                    raise pdns_error(422, f"NSEC3PARAMs provided for zone '{z}', but zone is not DNSSEC secured.",
                                     self.name)
            return await super()._request(method, endpoint, json_data, params, timeout)
        self.calls.append((method, endpoint, copy.deepcopy(json_data), dict(params or {}), timeout))
        parts = endpoint.split("/")
        z = _norm(unquote(parts[2]))
        if z not in self.zones:
            raise self._not_found(parts[2])
        keys = self.keys.setdefault(z, [])
        if op == "get_cryptokeys":
            return copy.deepcopy(keys)
        if op == "add_cryptokey":
            body = dict(json_data or {})
            if "privatekey" in body:
                raise pdns_error(422, "Import nicht erwartet", self.name)
            algorithm = str(body.get("algorithm") or "ECDSAP256SHA256")
            key = self.add_key(z, str(body.get("keytype") or "csk"), active=bool(body.get("active")),
                               published=body.get("published", True), algorithm=algorithm, bits=body.get("bits"))
            return copy.deepcopy(key)
        kid = int(parts[4])
        key = next((k for k in keys if k["id"] == kid), None)
        if key is None:
            raise pdns_error(422, f"Could not find key with id {kid}", self.name)
        if op == "get_cryptokey":
            return copy.deepcopy(key)
        if op == "update_cryptokey":
            body = json_data or {}
            if not self.published_field and "active" not in body:
                raise pdns_error(422, "Key 'active' not present", self.name)
            if "active" in body:
                key["active"] = bool(body["active"])
            if "published" in body:
                if not self.published_field:
                    raise pdns_error(422, "unknown field published", self.name)
                key["published"] = bool(body["published"])
            self._sync_dnssec_flag(z)
            return None
        if op == "delete_cryptokey":
            keys.remove(key)
            self._sync_dnssec_flag(z)
            return None
        raise pdns_error(405, "Method not allowed", self.name)


WRITE_OPS = frozenset({"add_cryptokey", "update_cryptokey", "delete_cryptokey", "update_zone", "rectify_zone",
                       "notify_zone", "update_records"})


def _version_tuple(v: str) -> tuple[int, int, int]:
    nums = []
    for part in v.split("-")[0].split(".")[:3]:
        nums.append(int(part) if part.isdigit() else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)  # type: ignore[return-value]


def _ds_for(zone: str, dnskey: str) -> list[str]:
    rdata = dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.DNSKEY, dnskey)
    name = dns.name.from_text(zone)
    return [dns.dnssec.make_ds(name, rdata, alg, policy=dns.dnssec.allow_all_policy).to_text()
            for alg in ("SHA1", "SHA256", "SHA384")]


class EventRecorder:
    """Ersatz fuer ``webhook_outbox.enqueue_event`` (zeichnet Ereignisse auf)."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, db, event, *, actor, data, zone=None, server=None, audit_log_id=None, actor_via=None):
        self.calls.append({"event": event, "actor": getattr(actor, "id", None), "data": data, "zone": zone,
                           "server": server, "audit_log_id": audit_log_id})
        return 1

    def events(self) -> list[str]:
        return [c["event"] for c in self.calls]


class DetachedAudits:
    """Ersatz fuer ``services.audit.write_audit_detached`` (Fehler-Audits)."""

    def __init__(self):
        self.entries: list[SimpleNamespace] = []

    async def __call__(self, action, resource_type, resource_name=None, **kw):
        self.entries.append(SimpleNamespace(action=action, resource_type=resource_type, resource_name=resource_name,
                                            **kw))
        return len(self.entries)

    def actions(self) -> list[str]:
        return [e.action for e in self.entries]
