"""Fan-out von RRset-Aenderungen auf alle schreibbaren PowerDNS-Server (Plan B.5).

Ein Modul, zwei Betriebsarten von :func:`apply_rrsets`:

- **A – ``rrsets``**: dasselbe REPLACE/DELETE auf allen Zielen. Nur fuer Aufrufer mit
  "Zielzustand setzen"-Semantik (Bulk, Rollback, DynDNS, PTR, DNSSEC-SOA-Bump).
- **B – ``build``**: je Server wird die Zone (bzw. ein RRset) **dieses** Servers gelesen und
  ``build(server_name, zone_json)`` liefert die RRsets fuer genau diesen Server
  (``None`` -> ``skipped (no matching content)``, ``[]`` -> ``skipped (no changes needed)``).
  Einzel-Endpoints nutzen ausschliesslich B, damit Peer-eigene Werte erhalten bleiben [D1].

Reihenfolge: Primary zuerst, Peers sequenziell (gemeinsame DB-Backends lesen so den
bereits geschriebenen Stand). ``require_primary=True`` -> bei Primary-Fehler keine
Peer-Writes. Transportfehler/Timeout am Primary nach gesendetem PATCH -> Nachpruefung per
Re-Read (``primary_outcome``) [D3]. Konfigurierte, aber nicht geladene Server erscheinen
in ``info`` als ``not loaded: <grund>`` -> Status ``skipped (not loaded: <grund>)`` [D4].

Fan-out-Status-Strings (UI-Vertrag): ``saved``, ``deleted``, ``skipped (zone not present)``,
``skipped (read-only)``, ``skipped (no matching content)``, ``skipped (no changes needed)``,
``skipped (not loaded: …)``, ``error: …``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ServerConfig
from app.services.pdns_client import PowerDNSAPIError, PowerDNSClient, pdns_manager
from app.services.rrsets import (
    norm_name,
    rrset_snapshot,
    snapshot_fingerprint,
    snapshot_from_rrset_payload,
)

logger = logging.getLogger(__name__)

STATUS_SAVED = "saved"
STATUS_DELETED = "deleted"
STATUS_ZONE_MISSING = "skipped (zone not present)"
STATUS_NO_MATCH = "skipped (no matching content)"
STATUS_NO_CHANGES = "skipped (no changes needed)"
STATUS_UNCLEAR = "error: unklar – bitte Zone neu laden"
INFO_READ_ONLY = "read-only"
SUCCESS_STATUSES = frozenset({STATUS_SAVED, STATUS_DELETED})

# Nachpruefung nach Transportfehler/Timeout am Primary [D3]
REREAD_ATTEMPTS = 3
REREAD_TIMEOUT = 5.0
REREAD_DELAY = 0.5
REREAD_ZONE_THRESHOLD = 20  # mehr betroffene RRsets -> eine GET /zones/{id} statt einzelner RRset-Reads

PrimaryOutcome = Literal["ok", "failed", "verified_after_timeout", "unknown"]
RRsetBuilder = Callable[[str, dict], Optional[list]]  # (server_name, zone_json_des_Servers) -> rrsets | None | []
Targets = list[tuple[str, PowerDNSClient]]


def not_loaded_info(reason: str) -> str:
    """``info``-Text fuer einen konfigurierten, aber nicht geladenen Server."""
    return f"not loaded: {reason or 'unknown'}"


# =============================================================================
# Schreibbarkeit / Ziele (Logik 1:1 aus records.py, plus nicht geladene Server)
# =============================================================================
async def _active_configs(db: AsyncSession) -> list:
    """Aktive ServerConfig-Zeilen; DB-Fehler -> [] (alle Server gelten dann als schreibbar)."""
    try:
        result = await db.execute(select(ServerConfig).where(ServerConfig.is_active == True))  # noqa: E712
        rows = list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load server_configs for fan-out: %s", exc)
        return []
    return [c for c in rows if getattr(c, "is_active", True)]


def _writable_check(by_name: dict) -> Callable[[str], bool]:
    has_column = hasattr(ServerConfig, "allow_writes")

    def _is_writable(name: str) -> bool:
        cfg = by_name.get(name)
        if cfg is None:
            return True  # nur per env bekannt -> schreibbar
        if not has_column:
            return True
        return bool(getattr(cfg, "allow_writes", True))

    return _is_writable


async def allow_writes_map(db: AsyncSession) -> dict[str, bool]:
    """``{server_name: allow_writes}`` aus der DB. Server ohne Zeile gelten als schreibbar
    (env-only); fehlende Spalte/DB-Fehler -> ``{}`` (Semantik wie ``routers/servers._allow_writes_map``)."""
    if not hasattr(ServerConfig, "allow_writes"):
        return {}
    try:
        rows = (await db.execute(select(ServerConfig))).scalars().all()
        return {r.name: bool(getattr(r, "allow_writes", True)) for r in rows}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load allow_writes flags: %s", exc)
        return {}


async def writable_server_names(db: AsyncSession) -> list[str]:
    """Geladene, schreibbare Server in der Reihenfolge von ``pdns_manager.list_servers()``."""
    aw = await allow_writes_map(db)
    return [n for n in pdns_manager.list_servers() if aw.get(n, True)]


async def is_server_writable(db: AsyncSession, server_name: str) -> bool:
    """Server ist geladen UND (keine DB-Zeile ODER ``allow_writes``); DB-Fehler -> schreibbar (+ Warnung)."""
    if server_name not in pdns_manager.clients:
        return False
    return (await allow_writes_map(db)).get(server_name, True)


async def writable_targets_for_zone(db: AsyncSession, zone_id: str, primary: str) -> tuple[Targets, dict[str, str]]:
    """Welche Server bekommen einen Write fuer ``zone_id``?

    - ``primary`` (der Server aus der URL) muss schreibbar sein, sonst ``([], {primary: "read-only"})``.
    - Alle anderen aktiven, schreibbaren Server sind Fan-out-Ziele (getrennte Backends bleiben synchron).
    - Fehlt die Spalte ``allow_writes`` (sehr alte DB), gilt jeder Server als schreibbar.
    - [D4] Aktive, schreibbare ServerConfig-Zeilen ohne geladenen Client (``pdns_manager.unloaded``)
      kommen als ``not loaded: <grund>`` in ``info``.

    Rueckgabe ``(targets, info)``; ``info`` = Server -> Hinweis (wird zu ``skipped (<hinweis>)``).
    """
    info: dict[str, str] = {}
    configs = await _active_configs(db)
    by_name = {c.name: c for c in configs}
    _is_writable = _writable_check(by_name)
    unloaded = dict(getattr(pdns_manager, "unloaded", {}) or {})

    if primary not in pdns_manager.clients and primary in unloaded:
        info[primary] = not_loaded_info(unloaded[primary])
        return ([], info)
    if not _is_writable(primary):
        info[primary] = INFO_READ_ONLY
        return ([], info)

    targets: Targets = []
    seen: set[str] = set()
    try:
        targets.append((primary, pdns_manager.get_client(primary)))
        seen.add(primary)
    except ValueError as exc:
        info[primary] = str(exc)
        return ([], info)

    for name in pdns_manager.list_servers():
        if name in seen:
            continue
        if not _is_writable(name):
            info[name] = INFO_READ_ONLY
            continue
        try:
            targets.append((name, pdns_manager.get_client(name)))
            seen.add(name)
        except ValueError:
            continue

    # [D4] konfigurierte, aber nicht geladene Server sichtbar machen
    candidates = [c.name for c in configs] + [n for n in unloaded if n not in by_name]
    for name in candidates:
        if name in seen or name in info or name in pdns_manager.clients:
            continue
        if not _is_writable(name):
            continue
        info[name] = not_loaded_info(unloaded.get(name, "unknown"))

    return (targets, info)


def zone_not_found_for(exc: PowerDNSAPIError) -> bool:
    """PowerDNS liefert 404/422, wenn die Zone auf diesem Server fehlt -> im Fan-out still ueberspringen.

    422 bedeutet bei PowerDNS "Input validation failed" (ungueltiger Content, Name ausserhalb
    der Zone, CNAME-Konflikt ...). Nur wenn der Text eindeutig eine fehlende Zone nennt, wird
    uebersprungen – sonst muss der Fehler beim Nutzer ankommen. Transportfehler (502) -> False.
    """
    detail = (exc.detail or "").lower() if isinstance(exc.detail, str) else str(exc.detail or "").lower()
    if exc.status_code == 404:
        return True
    if exc.status_code >= 500:
        return False
    return "could not find domain" in detail or "no such zone" in detail or "not found" in detail


zone_not_found = zone_not_found_for


def read_only_error(server_name: str) -> HTTPException:
    return HTTPException(
        status_code=403,
        detail=(
            f"Server '{server_name}' ist auf 'Speichern: Nein' gesetzt. "
            "Wechsle in der Zonenliste auf einen Server mit aktivem Speichern, "
            "oder aktiviere 'Speichern' für diesen Server in den Einstellungen → DNS-Server."
        ),
    )


def summarize_results(results: dict[str, str], info: dict[str, str]) -> dict:
    """Ergebnisse je Server plus ``info``-Hinweise (als ``skipped (<hinweis>)``) fuer die UI."""
    out = {}
    out.update(results)
    for k, v in info.items():
        if k not in out:
            out[k] = f"skipped ({v})"
    return out


# =============================================================================
# Ergebnis
# =============================================================================
@dataclass
class FanoutResult:
    primary: str
    results: dict[str, str] = field(default_factory=dict)
    info: dict[str, str] = field(default_factory=dict)
    primary_success: bool = False
    primary_error: Optional[PowerDNSAPIError] = None
    primary_outcome: PrimaryOutcome = "failed"
    per_server_rrsets: dict[str, list[dict]] = field(default_factory=dict)  # was je Server gesendet wurde
    errors: dict[str, PowerDNSAPIError] = field(default_factory=dict)  # PowerDNS-Fehler je Server (ohne "zone fehlt")

    @property
    def any_success(self) -> bool:
        return any(v in SUCCESS_STATUSES for v in self.results.values())

    @property
    def all_4xx(self) -> bool:
        """True, wenn es Fehler gab und alle 4xx waren (Eingabefehler statt Serverausfall)."""
        if not self.errors:
            return False
        return all(400 <= e.status_code < 500 for e in self.errors.values())

    @property
    def primary_status(self) -> Optional[str]:
        return self.results.get(self.primary)

    @property
    def summary(self) -> dict:
        return summarize_results(self.results, self.info)


# =============================================================================
# Nachpruefung nach Transportfehler [D3]
# =============================================================================
def _keys_of(rrsets: Iterable[dict]) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for r in rrsets:
        k = (norm_name(r.get("name", "")), str(r.get("type", "")).upper())
        if k not in keys:
            keys.append(k)
    return keys


async def _read_current(client: PowerDNSClient, zone_id: str, keys: list[tuple[str, str]]) -> dict:
    if len(keys) > REREAD_ZONE_THRESHOLD:
        zone = await client.get_zone(zone_id, timeout=REREAD_TIMEOUT)
        return {k: rrset_snapshot(zone, k[0], k[1]) for k in keys}
    out = {}
    for name, rtype in keys:
        lst = await client.get_rrsets(zone_id, name, rtype, timeout=REREAD_TIMEOUT)
        out[(name, rtype)] = rrset_snapshot(lst, name, rtype)
    return out


async def verify_after_transport_error(
    client: PowerDNSClient,
    zone_id: str,
    expected_after: list[dict],
    before: Optional[dict] = None,
) -> PrimaryOutcome:
    """Liest die betroffenen RRsets erneut (max. 3 Versuche, je 5 s) und vergleicht.

    - Zustand == ``expected_after`` -> ``"verified_after_timeout"``
    - Zustand == ``before`` (Vorher-Snapshots je (name, TYPE)) -> ``"failed"``
    - sonst (auch: nicht lesbar, Vorher-Zustand unbekannt) -> ``"unknown"``
    """
    keys = _keys_of(expected_after)
    if not keys:
        return "unknown"
    expected: dict[tuple[str, str], Optional[dict]] = {}
    for r in expected_after:
        expected[(norm_name(r.get("name", "")), str(r.get("type", "")).upper())] = snapshot_from_rrset_payload(r)
    origin = norm_name(zone_id)

    current = None
    for attempt in range(REREAD_ATTEMPTS):
        try:
            current = await _read_current(client, zone_id, keys)
            break
        except PowerDNSAPIError as exc:
            logger.warning("[%s] Nachpruefung nach Transportfehler: Lesen fehlgeschlagen (%s/%s): %s",
                           getattr(client, "name", "?"), attempt + 1, REREAD_ATTEMPTS, exc.status_code)
            if zone_not_found_for(exc):
                return "unknown"
            if attempt + 1 < REREAD_ATTEMPTS and REREAD_DELAY > 0:
                await asyncio.sleep(REREAD_DELAY)
    if current is None:
        return "unknown"

    def fp(snap, k):
        return snapshot_fingerprint(snap, k[1], origin)

    if all(fp(current[k], k) == fp(expected[k], k) for k in keys):
        return "verified_after_timeout"
    if before is not None and all(k in before for k in keys) and all(fp(current[k], k) == fp(before[k], k) for k in keys):
        return "failed"
    return "unknown"


# =============================================================================
# apply_rrsets
# =============================================================================
async def apply_rrsets(
    db: AsyncSession,
    primary: str,
    zone_id: str,
    rrsets: Optional[list[dict]] = None,
    *,
    build: Optional[RRsetBuilder] = None,
    expected_after: Optional[list[dict]] = None,
    timeout: float = 30.0,
    targets: Optional[Targets] = None,
    info: Optional[dict[str, str]] = None,
    require_primary: bool = True,
    read_rrset: Optional[tuple[str, str]] = None,
    before_state: Optional[dict] = None,
    success_status: str = STATUS_SAVED,
) -> FanoutResult:
    """Schreibt RRsets auf Primary und Peers (siehe Modul-Docstring).

    Zusaetzliche (additive) Parameter gegenueber Plan B.5:
    - ``read_rrset=(name, type)``: Betriebsart B liest je Server nur dieses RRset
      (``get_zone_rrset``) statt der ganzen Zone; der Builder bekommt die (ggf. gefilterte) Zone.
    - ``before_state={(name, TYPE): snapshot|None}``: Vorher-Zustand des Primary fuer die
      Nachpruefung in Betriebsart A (B ermittelt ihn selbst aus der gelesenen Zone).
    - ``success_status``: Status bei Erfolg (``"saved"``, Delete-Endpoints ``"deleted"``).
    """
    if (rrsets is None) == (build is None):
        raise ValueError("apply_rrsets: genau eines von 'rrsets' oder 'build' angeben")

    if targets is None:
        targets, auto_info = await writable_targets_for_zone(db, zone_id, primary)
        merged = dict(auto_info)
        merged.update(info or {})
        info = merged
    else:
        info = dict(info or {})

    res = FanoutResult(primary=primary, info=info)
    ordered = sorted(list(targets), key=lambda t: t[0] != primary)  # stabil: Primary zuerst
    has_primary = any(n == primary for n, _ in ordered)
    if not has_primary and ordered:
        logger.debug("apply_rrsets: Primary %s nicht unter den Zielen", primary)

    for name, client in ordered:
        is_primary = name == primary
        if not is_primary and require_primary and not res.primary_success:
            break  # kein Peer-Write ohne bestaetigten Primary (f52)
        await _apply_one(res, name, client, zone_id, rrsets, build, expected_after if is_primary else None,
                         timeout, is_primary, read_rrset, before_state if is_primary else None, success_status)
    return res


apply_rrsets_fanout = apply_rrsets


async def _apply_one(
    res: FanoutResult,
    name: str,
    client: PowerDNSClient,
    zone_id: str,
    rrsets: Optional[list[dict]],
    build: Optional[RRsetBuilder],
    expected_after: Optional[list[dict]],
    timeout: float,
    is_primary: bool,
    read_rrset: Optional[tuple[str, str]],
    before_state: Optional[dict],
    success_status: str,
) -> None:
    def _fail(status: str, exc: Optional[PowerDNSAPIError] = None, *, count_error: bool = True) -> None:
        res.results[name] = status
        if exc is not None and count_error:
            res.errors[name] = exc
        if is_primary:
            res.primary_error = exc
            res.primary_outcome = "failed"
            res.primary_success = False

    before: Optional[dict] = before_state
    if build is not None:
        try:
            if read_rrset is not None:
                zone_json = await client.get_zone_rrset(zone_id, read_rrset[0], read_rrset[1], timeout=timeout)
            else:
                zone_json = await client.get_zone(zone_id, timeout=timeout)
        except PowerDNSAPIError as exc:
            if zone_not_found_for(exc):
                _fail(STATUS_ZONE_MISSING, exc, count_error=False)
            else:
                _fail(f"error: {exc.detail}", exc)
            return
        try:
            payload = build(name, zone_json)
        except Exception as exc:  # noqa: BLE001
            if is_primary:
                raise
            logger.warning("[%s] Fan-out-Builder fehlgeschlagen: %s", name, exc)
            res.results[name] = f"error: {exc}"
            return
        if payload is None:
            _fail(STATUS_NO_MATCH)
            return
        payload = list(payload)
        if not payload:
            res.results[name] = STATUS_NO_CHANGES
            if is_primary:
                res.primary_success = True
                res.primary_outcome = "ok"
            return
        before = {k: rrset_snapshot(zone_json, k[0], k[1]) for k in _keys_of(payload)}
    else:
        payload = list(rrsets or [])

    try:
        await client.update_records(zone_id, payload, timeout=timeout)
    except PowerDNSAPIError as exc:
        if is_primary and exc.status_code in (502, 504):
            outcome = await verify_after_transport_error(client, zone_id, expected_after or payload, before)
            logger.warning("[%s] Transportfehler beim PATCH (%s) – Nachpruefung: %s", name, exc.status_code, outcome)
            if outcome == "verified_after_timeout":
                res.results[name] = success_status
                res.per_server_rrsets[name] = payload
                res.primary_success = True
                res.primary_outcome = "verified_after_timeout"
                res.primary_error = None
                return
            if outcome == "failed":
                _fail(f"error: {exc.detail}", exc)
                return
            _fail(STATUS_UNCLEAR, exc)
            res.primary_outcome = "unknown"
            return
        if zone_not_found_for(exc):
            _fail(STATUS_ZONE_MISSING, exc, count_error=False)
        else:
            _fail(f"error: {exc.detail}", exc)
        return

    res.results[name] = success_status
    res.per_server_rrsets[name] = payload
    if is_primary:
        res.primary_success = True
        res.primary_outcome = "ok"
        res.primary_error = None


# =============================================================================
# Builder mit 2.4.1-Semantik fuer die Einzel-Endpoints (Betriebsart B) [D1]
# =============================================================================
def _item(rec: Any) -> dict:
    if isinstance(rec, dict):
        return {"content": str(rec.get("content", "")), "disabled": bool(rec.get("disabled", False))}
    return {"content": str(getattr(rec, "content", "")), "disabled": bool(getattr(rec, "disabled", False))}


def _find_rrset(zone_json: Optional[dict], name: str, rtype: str) -> Optional[dict]:
    n = norm_name(name)
    t = (rtype or "").upper()
    for rr in (zone_json or {}).get("rrsets") or []:
        if norm_name(str(rr.get("name", ""))) == n and str(rr.get("type", "")).upper() == t:
            return rr
    return None


def create_builder(name: str, rtype: str, ttl: int, records: Iterable[Any]) -> RRsetBuilder:
    """Anlegen: Bestand des Servers + neue Werte (exakter Inhaltsvergleich), REPLACE mit neuer TTL.

    Wie 2.4.1 wird immer geschrieben (Status ``saved``); Kommentare werden nicht gesendet
    (PowerDNS behaelt sie).
    """
    new_items = [_item(r) for r in records]

    def build(server_name: str, zone_json: dict) -> list[dict]:
        rr = _find_rrset(zone_json, name, rtype)
        combined = [_item(r) for r in ((rr or {}).get("records") or [])]
        for item in new_items:
            if not any(ex["content"] == item["content"] for ex in combined):
                combined.append(dict(item))
        return [{"name": name, "type": rtype, "ttl": ttl, "changetype": "REPLACE", "records": combined}]

    return build


def update_builder(name: str, rtype: str, old_content: str, new_content: str, ttl: int,
                   disabled: bool = False) -> RRsetBuilder:
    """Aendern: Wert ``old_content`` durch ``new_content`` ersetzen; fehlt er -> ``None``
    (``skipped (no matching content)``). SOA: ein vorhandener Eintrag wird immer ersetzt."""

    def build(server_name: str, zone_json: dict) -> Optional[list[dict]]:
        rr = _find_rrset(zone_json, name, rtype)
        existing = (rr or {}).get("records") or []
        updated = []
        found = False
        for ex in existing:
            if ex.get("content") == old_content:
                updated.append({"content": new_content, "disabled": bool(disabled)})
                found = True
            else:
                updated.append({"content": ex.get("content"), "disabled": bool(ex.get("disabled", False))})
        if (rtype or "").upper() == "SOA" and existing and not found:
            updated = [{"content": new_content, "disabled": bool(disabled)}]
            found = True
        if not found:
            return None
        return [{"name": name, "type": rtype, "ttl": ttl, "changetype": "REPLACE", "records": updated}]

    return build


def delete_builder(name: str, rtype: str, content: Optional[str] = None) -> RRsetBuilder:
    """Loeschen: mit ``content`` nur diesen Wert entfernen (fehlt er -> ``None``; letzter Wert ->
    DELETE; TTL, disabled-Flags und Kommentare bleiben), ohne ``content`` das ganze RRset (DELETE).
    Aufrufer setzen ``success_status="deleted"``."""

    def build(server_name: str, zone_json: dict) -> Optional[list[dict]]:
        if content is None:
            return [{"name": name, "type": rtype, "changetype": "DELETE"}]
        rr = _find_rrset(zone_json, name, rtype)
        if rr is None:
            return None
        records = rr.get("records") or []
        remaining = [r for r in records if r.get("content") != content]
        if len(remaining) == len(records):
            return None
        if not remaining:
            return [{"name": name, "type": rtype, "changetype": "DELETE"}]
        replace = {
            "name": name,
            "type": rtype,
            "ttl": rr.get("ttl"),
            "changetype": "REPLACE",
            "records": [{"content": r.get("content"), "disabled": bool(r.get("disabled", False))} for r in remaining],
        }
        if rr.get("comments"):
            replace["comments"] = rr["comments"]
        return [replace]

    return build
