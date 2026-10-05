"""DNSSEC-Orchestrierung: PowerDNS-I/O und Status, keine HTTP-Exceptions (F4 5.4).

- ``load_context``: Zonen-Metadaten, Schluessel (ohne ``privatekey``) und PowerDNS-Version (10 min gecacht).
- ``enable_dnssec`` (idempotent, Rollback bei Teilfehlern), ``disable_dnssec`` (erst NSEC3PARAM, dann inaktive,
  dann aktive Schluessel), ``create_key``, ``update_key``, ``set_nsec``.
- ``build_status`` fuer ``GET …/status`` inkl. Peer-Vergleich (``probe_peers``) und Zeitstempeln aus dem
  Audit-Log (``key_history``).
- ``after_key_change`` [D10]: bei Zonen vom Typ Master/Producer nach einer Schluesselaenderung den SOA-Serial
  erhoehen (Fan-out Betriebsart A, nur der Server aus der URL) und NOTIFY senden; Audits ``UPDATE`` (record,
  ``source: "dnssec"``) und ``ZONE_NOTIFY``. Fehler brechen den Aufrufer nie (Ergebnis-Dict).
- ``enable_dnssec_on_new_zone``: DNSSEC beim Zonenanlegen (Aufrufer ``routers/zones.py`` ab F4-B).

Fehler: ``DnssecConflict`` (-> 409), ``DnssecInputError`` (-> 422), ``DnssecStepError`` (Enable-/Rectify-Schritt
mit Rollback-Ergebnis), ``DnssecPartialError`` (Disable bricht nach einzelnen Loeschungen ab),
``PowerDNSAPIError`` (unveraendert durchgereicht).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.names import normalize_zone_name
from app.core.timeutil import iso_utc
from app.models.models import AuditLog
from app.services import dnssec_logic as logic
from app.services import fanout, webhook_outbox
from app.services.audit import write_audit
from app.services.dnssec_parse import compute_key_tag, normalize_dnskey, parse_ds_line
from app.services.pdns_client import STATUS_PROBE_TIMEOUT, PowerDNSAPIError, pdns_error_text, pdns_manager
from app.services.rrsets import rrset_snapshot

logger = logging.getLogger(__name__)

VERSION_CACHE_TTL = 600.0
MAX_PEERS = 10
KEY_HISTORY_LIMIT = 200
KEY_HISTORY_FIELDS = ("created_at", "activated_at", "deactivated_at", "published_at", "unpublished_at")
_VERSION_CACHE: dict[tuple[str, str], tuple[Optional[str], float]] = {}


# =============================================================================================
# Fehler
# =============================================================================================
class DnssecConflict(Exception):
    """Fachlicher Konflikt (Router -> 409); ``message`` ist deutsch."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DnssecInputError(ValueError):
    """Ungueltige Anfrage, die erst mit dem PowerDNS-Zustand erkennbar ist (Router -> 422)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class DnssecStepError(Exception):
    """Ein Schritt (Schluessel anlegen, NSEC/NSEC3 setzen, Rectify) scheiterte; ``rollback`` = "ok" | "failed: …" | None."""

    def __init__(self, step: str, exc: PowerDNSAPIError, rollback: Optional[str]):
        super().__init__(f"{step}: {exc}")
        self.step = step
        self.exc = exc
        self.rollback = rollback


class DnssecPartialError(Exception):
    """Disable: ``deleted`` wurden geloescht, ``failed`` nicht (``exc``)."""

    def __init__(self, deleted: list, failed, exc: PowerDNSAPIError):
        super().__init__(str(exc))
        self.deleted = list(deleted)
        self.failed = failed
        self.exc = exc


def public_pdns_error(exc: PowerDNSAPIError) -> str:
    """Fehlertext fuer HTTP-Antworten: wie ``pdns_error_text``, aber ohne Server-URL bei Verbindungsfehlern.

    ``_request`` nennt bei 503 die interne PowerDNS-URL; die soll ein Zonen-Nutzer nicht sehen (Audit-Eintraege
    behalten den vollen Text).
    """
    server = getattr(exc, "server", "unknown")
    if exc.status_code == 503:
        return f"PowerDNS ({server}): Verbindung zum Server fehlgeschlagen."
    if exc.status_code == 504:
        return f"PowerDNS ({server}): Zeitüberschreitung – Ergebnis unklar, bitte neu laden."
    return pdns_error_text(exc)


def published_unsupported_text(server: str, version: Optional[str]) -> str:
    return (
        "Das Flag „published“ erfordert PowerDNS Authoritative 4.3 oder neuer "
        f"(Server '{server}' meldet {version or 'eine unbekannte Version'})."
    )


# =============================================================================================
# Version, Kontext, Schluesseldarstellung
# =============================================================================================
def reset_version_cache() -> None:
    """Nur fuer Tests."""
    _VERSION_CACHE.clear()


async def get_server_version(client) -> Optional[str]:
    """PowerDNS-Version (``GET /servers/localhost``), 600 s gecacht; Fehler -> ``None`` (nicht gecacht)."""
    key = (str(getattr(client, "name", "?")), str(getattr(client, "url", "")))
    now = time.monotonic()
    hit = _VERSION_CACHE.get(key)
    if hit is not None and now - hit[1] < VERSION_CACHE_TTL:
        return hit[0]
    try:
        info = await client.get_server_info(timeout=STATUS_PROBE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - Version ist nur Zusatzinfo
        logger.info("PowerDNS-Version von %s nicht ermittelbar: %s", key[0], exc)
        return None
    version = None
    if isinstance(info, dict) and info.get("version"):
        version = str(info["version"])
    _VERSION_CACHE[key] = (version, now)
    return version


def strip_private(obj: Any) -> Any:
    """Entfernt ``privatekey`` rekursiv aus dicts/Listen (der Signaturschluessel verlaesst das Panel nie)."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if k != "privatekey"}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj


@dataclass
class ZoneDnssecContext:
    meta: dict
    raw_keys: list[dict]
    keys: list[logic.KeyView]
    version: Optional[str]
    supports_published: Optional[bool]

    @property
    def kind(self) -> Optional[str]:
        return self.meta.get("kind")

    @property
    def presigned(self) -> bool:
        return self.meta.get("presigned") is True

    @property
    def signed(self) -> bool:
        return any(k.active for k in self.keys)

    def key_by_id(self, key_id: int) -> Optional[logic.KeyView]:
        return next((k for k in self.keys if k.id == key_id), None)

    def raw_by_id(self, key_id: int) -> dict:
        return next((k for k in self.raw_keys if int(k.get("id") or 0) == key_id), {})


def _clean_keys(raw) -> list[dict]:
    keys = [strip_private(k) for k in (raw or []) if isinstance(k, dict)]
    return sorted(keys, key=lambda k: int(k.get("id") or 0))


async def load_context(client, zone_id: str, *, with_version: bool = True) -> ZoneDnssecContext:
    """Meta (``rrsets=false``) und Schluessel parallel, danach die (gecachte) Version.

    ``PowerDNSAPIError`` beim Laden von Meta/Schluesseln geht an den Aufrufer.
    """
    meta, raw = await asyncio.gather(client.get_zone_meta(zone_id), client.get_cryptokeys(zone_id))
    version = await get_server_version(client) if with_version else None
    keys_raw = _clean_keys(raw)
    return ZoneDnssecContext(
        meta=meta if isinstance(meta, dict) else {},
        raw_keys=keys_raw,
        keys=[logic.key_view_from_pdns(k) for k in keys_raw],
        version=version,
        supports_published=logic.supports_published(logic.parse_pdns_version(version), keys_raw),
    )


def key_extras(raw: dict) -> dict:
    """Zusatzfelder je Schluessel: ``key_tag``, ``algorithm_number``, ``flags``, ``role``, ``published_effective``."""
    view = logic.key_view_from_pdns(raw)
    return {
        "key_tag": compute_key_tag(raw.get("dnskey")),
        "algorithm_number": view.algorithm_number,
        "flags": view.flags,
        "role": "sep" if view.is_sep else "zsk",
        "published_effective": view.published_eff,
    }


def public_key(raw: dict) -> dict:
    """PowerDNS-Cryptokey wie bisher (``ds`` als Strings, ohne ``privatekey``) plus ``key_extras`` (``/keys``, Antworten)."""
    k = strip_private(raw or {})
    return {**k, **key_extras(k)}


def ds_lines_of(raw: dict) -> list[dict]:
    out = []
    for line in (raw or {}).get("ds") or []:
        if not isinstance(line, str):
            continue
        parsed = parse_ds_line(line)
        out.append({"ds": line, "digest_type": parsed.get("digest_type"), "parsed": parsed})
    return out


def enrich_key(raw: dict, *, ds_status: Optional[str] = None, rollover_role: Optional[str] = None) -> dict:
    """``DnssecKeyInfo`` fuer den Status: ``ds`` als DsLine-Liste, DS-Status und Rollover-Rolle."""
    k = strip_private(raw or {})
    view = logic.key_view_from_pdns(k)
    out = {**k, **key_extras(k)}
    out.update({
        "id": view.id,
        "keytype": view.keytype,
        "active": view.active,
        "published": view.published,
        "ds": ds_lines_of(k),
        "ds_status": ds_status or ("not_sep" if not view.is_sep else "inactive"),
        "rollover_role": rollover_role,
    })
    return out


def _ds_lines(raw_keys: list[dict]) -> list[str]:
    out: list[str] = []
    for k in raw_keys or []:
        out.extend(line for line in (k.get("ds") or []) if isinstance(line, str))
    return out


def key_brief(raw: dict) -> dict:
    """Kurzform fuer Audit/Webhook: ID, Typ, Tag (kein DNSKEY-Blob, nie Privatteil)."""
    return {"key_id": int(raw.get("id") or 0), "keytype": raw.get("keytype"),
            "key_tag": compute_key_tag(raw.get("dnskey"))}


# =============================================================================================
# Aktivieren / Deaktivieren
# =============================================================================================
async def _rollback_enable(client, zone_id: str, created: list[dict]) -> str:
    errs = []
    for k in reversed(created):
        try:
            await client.delete_cryptokey(zone_id, k["id"])
        except PowerDNSAPIError as e:
            errs.append(f"Schlüssel {k.get('id')}: {pdns_error_text(e)}")
        except (KeyError, TypeError):
            errs.append("Schlüssel ohne ID in der PowerDNS-Antwort")
    try:
        await client.set_nsec3(zone_id, "", False)
    except PowerDNSAPIError as e:
        errs.append(f"NSEC3: {pdns_error_text(e)}")
    return "ok" if not errs else "failed: " + "; ".join(errs)


def _match_created(created: list[dict], raw: list[dict]) -> list[dict]:
    by_id = {int(k.get("id") or 0): k for k in raw}
    return [by_id.get(int(c.get("id") or 0), c) for c in created]


async def enable_dnssec(client, zone_id: str, opts, ctx: ZoneDnssecContext) -> dict:
    """Idempotentes Aktivieren (Spec 3.6). Rueckgabe-Dict; Fehler als DnssecConflict/DnssecStepError."""
    zone = normalize_zone_name(zone_id)
    if ctx.signed:
        return {
            "already_enabled": True, "created": [], "raw_keys": ctx.raw_keys,
            "ds_records": _ds_lines(ctx.raw_keys),
            "nsec3param": str(ctx.meta.get("nsec3param") or ""),
            "nsec3narrow": bool(ctx.meta.get("nsec3narrow")), "warnings": [],
        }
    if ctx.keys:
        raise DnssecConflict(
            f"Für die Zone '{zone}' existieren bereits {len(ctx.keys)} Schlüssel, aber keiner ist aktiv. "
            "Aktiviere einen vorhandenen Schlüssel oder lösche die Schlüssel, bevor du DNSSEC neu einrichtest."
        )
    specs = [{"keytype": "csk"}] if opts.key_model == "csk" else [{"keytype": "ksk"}, {"keytype": "zsk"}]
    created: list[dict] = []
    step = ""
    try:
        for s in specs:
            step = f"Schlüssel anlegen ({s['keytype'].upper()})"
            body = {**s, "active": True, "algorithm": opts.algorithm}
            if opts.bits:
                body["bits"] = opts.bits
            created.append(strip_private(await client.add_cryptokey(zone_id, body) or {}))
        step = "NSEC/NSEC3 setzen"
        await client.set_nsec3(zone_id, opts.effective_nsec3param(), opts.effective_narrow())
        step = "Rectify"
        await client.rectify_zone(zone_id)
    except PowerDNSAPIError as e:
        raise DnssecStepError(step, e, await _rollback_enable(client, zone_id, created)) from e
    try:
        raw = _clean_keys(await client.get_cryptokeys(zone_id))
    except PowerDNSAPIError as e:
        logger.warning("DNSSEC aktiviert, Schluessel konnten nicht neu gelesen werden (%s): %s", zone, e.status_code)
        raw = _clean_keys(created)
    return {
        "already_enabled": False,
        "created": _match_created(created, raw),
        "raw_keys": raw,
        "ds_records": _ds_lines(raw),
        "nsec3param": opts.effective_nsec3param(),
        "nsec3narrow": opts.effective_narrow(),
        "warnings": logic.nsec_warnings(opts.nsec3_params()),
    }


async def disable_dnssec(client, zone_id: str, ctx: ZoneDnssecContext) -> dict:
    """NSEC3PARAM entfernen, Schluessel loeschen (inaktive zuerst), Rectify best effort (Spec 3.7).

    Fehler beim NSEC3-Schritt: ``PowerDNSAPIError`` (nichts geloescht). Fehler beim Loeschen: ``DnssecPartialError``.
    """
    before = str(ctx.meta.get("nsec3param") or "").strip()
    if not ctx.keys and not before:
        return {"already_disabled": True, "deleted": [], "rectify_error": None, "nsec3param_before": ""}
    if before or ctx.meta.get("nsec3narrow"):
        await client.set_nsec3(zone_id, "", False)
    deleted: list[logic.KeyView] = []
    for k in sorted(ctx.keys, key=lambda k: (k.active, k.id)):
        try:
            await client.delete_cryptokey(zone_id, k.id)
        except PowerDNSAPIError as e:
            raise DnssecPartialError(deleted, k, e) from e
        deleted.append(k)
    rectify_error = None
    try:
        await client.rectify_zone(zone_id)
    except PowerDNSAPIError as e:
        rectify_error = pdns_error_text(e)
        logger.warning("Rectify nach DNSSEC-Deaktivierung fehlgeschlagen (%s): %s", normalize_zone_name(zone_id),
                       e.status_code)
    return {"already_disabled": False, "deleted": deleted, "rectify_error": rectify_error, "nsec3param_before": before}


# =============================================================================================
# Einzelne Schluessel, NSEC/NSEC3
# =============================================================================================
async def create_key(client, zone_id: str, body, ctx: ZoneDnssecContext) -> tuple[dict, list[str]]:
    """Schluessel anlegen (Spec 3.8). Rueckgabe ``(cryptokey_ohne_privatekey, warn_codes)``."""
    zone = normalize_zone_name(zone_id)
    if not ctx.keys:
        raise DnssecConflict(
            f"DNSSEC ist für die Zone '{zone}' nicht eingerichtet. Bitte zuerst „DNSSEC aktivieren“ verwenden."
        )
    if len(ctx.keys) >= logic.MAX_KEYS_PER_ZONE:
        raise DnssecConflict(
            f"Die Zone '{zone}' hat bereits {len(ctx.keys)} Schlüssel (Maximum {logic.MAX_KEYS_PER_ZONE}). "
            "Lösche zuerst nicht mehr benötigte Schlüssel."
        )
    if body.published is False and ctx.supports_published is False:
        raise DnssecInputError(published_unsupported_text(getattr(client, "name", "?"), ctx.version))
    payload = {"keytype": body.keytype, "active": bool(body.active), "algorithm": body.algorithm}
    if body.bits is not None:
        payload["bits"] = body.bits
    if ctx.supports_published is not False:
        payload["published"] = bool(body.published)
    created = strip_private(await client.add_cryptokey(zone_id, payload) or {})
    if not isinstance(created, dict):
        created = {}
    if not created.get("dnskey") or not created.get("id"):
        try:
            fresh = _clean_keys(await client.get_cryptokeys(zone_id))
        except PowerDNSAPIError as e:
            logger.warning("Neuer Schluessel angelegt, Liste nicht lesbar (%s): %s", zone, e.status_code)
            fresh = []
        if created.get("id"):
            created = next((k for k in fresh if int(k.get("id") or 0) == int(created["id"])), created)
        else:
            old_ids = {k.id for k in ctx.keys}
            new = [k for k in fresh if int(k.get("id") or 0) not in old_ids]
            if new:
                created = new[-1]
    warnings = []
    number = logic.ALGORITHMS.get(body.algorithm, {}).get("number")
    if body.published and not body.active and not any(k.active and k.algorithm_number == number for k in ctx.keys):
        warnings.append("algorithm_prepublish_unsigned")
    return created, warnings


async def update_key(client, zone_id: str, key: logic.KeyView, *, active: bool, published: Optional[bool],
                     ctx: ZoneDnssecContext) -> None:
    """``PUT /cryptokeys/{id}``: ``active`` immer (PowerDNS < 4.3), ``published`` nur wenn geaendert und unterstuetzt."""
    body: dict[str, Any] = {"active": bool(active)}
    if published is not None and ctx.supports_published is not False:
        body["published"] = bool(published)
    await client.update_cryptokey(zone_id, key.id, body)


async def set_nsec(client, zone_id: str, opts, ctx: ZoneDnssecContext) -> dict:
    """NSEC/NSEC3 aendern (Spec 3.12). ``{"unchanged", "before", "after", "warnings"}``.

    ``set_nsec3``-Fehler: ``PowerDNSAPIError``; Rectify-Fehler: ``DnssecStepError("Rectify", e, None)``.
    """
    zone = normalize_zone_name(zone_id)
    if not ctx.keys:
        raise DnssecConflict(
            f"Für die Zone '{zone}' gibt es keine DNSSEC-Schlüssel – NSEC/NSEC3 kann erst nach dem Aktivieren "
            "geändert werden."
        )
    if opts.nsec_mode == "nsec3":
        bad = sorted({k.algorithm_number for k in ctx.keys
                      if k.active and k.algorithm_number in logic.NSEC3_INCAPABLE_ALGORITHM_NUMBERS})
        if bad:
            num = bad[0]
            name = next((str(r.get("algorithm")) for r in ctx.raw_keys
                         if logic.key_view_from_pdns(r).algorithm_number == num and r.get("algorithm")), "?")
            raise DnssecConflict(
                f"Die Zone nutzt Algorithmus {name} ({num}), der kein NSEC3 unterstützt. "
                "Zuerst einen Algorithmuswechsel durchführen."
            )
    before_param = str(ctx.meta.get("nsec3param") or "").strip()
    before = {"nsec3param": before_param, "nsec3narrow": bool(ctx.meta.get("nsec3narrow")),
              "api_rectify": ctx.meta.get("api_rectify")}
    target = opts.effective_nsec3param()
    after = {"nsec3param": target, "nsec3narrow": opts.effective_narrow(), "api_rectify": True}
    current = logic.lax_parse_nsec3param(before_param) if before_param else None
    if before_param and current is None:
        same_params = False
    else:
        same_params = current == opts.nsec3_params()
    warnings = logic.nsec_warnings(opts.nsec3_params())
    if same_params and before["nsec3narrow"] == after["nsec3narrow"] and before["api_rectify"] is True:
        return {"unchanged": True, "before": before, "after": before, "warnings": warnings}
    await client.set_nsec3(zone_id, target, after["nsec3narrow"])
    try:
        await client.rectify_zone(zone_id)
    except PowerDNSAPIError as e:
        raise DnssecStepError("Rectify", e, None) from e
    return {"unchanged": False, "before": before, "after": after, "warnings": warnings}


# =============================================================================================
# Status
# =============================================================================================
def _peer(name: str, aw: dict, state: str, *, key_tags: Optional[list[int]] = None,
          zone_kind: Optional[str] = None) -> dict:
    return {"server": name, "allow_writes": bool(aw.get(name, True)), "state": state,
            "key_tags": list(key_tags or []), "zone_kind": zone_kind}


async def probe_peers(db: AsyncSession, primary: str, zone_id: str, primary_keys: list[dict]) -> list[dict]:
    """Schluesselmengen der Zone auf den anderen geladenen Servern (max. 10, parallel, je 8 s)."""
    names = [n for n in pdns_manager.list_servers() if n != primary][:MAX_PEERS]
    if not names:
        return []
    aw = await fanout.allow_writes_map(db)
    prim = {normalize_dnskey(k.get("dnskey")) for k in primary_keys if k.get("dnskey")}

    async def one(name: str) -> dict:
        try:
            c = pdns_manager.get_client(name)
            meta, raw = await asyncio.gather(
                c.get_zone_meta(zone_id, timeout=STATUS_PROBE_TIMEOUT),
                c.get_cryptokeys(zone_id, timeout=STATUS_PROBE_TIMEOUT),
            )
        except PowerDNSAPIError as e:
            if fanout.zone_not_found(e):
                return _peer(name, aw, "zone_missing")
            return _peer(name, aw, "unreachable" if e.status_code in (502, 503, 504) else "error")
        except Exception as e:  # noqa: BLE001 - ein Peer darf den Status nie brechen
            logger.info("DNSSEC-Peer-Pruefung %s fehlgeschlagen: %s", name, e)
            return _peer(name, aw, "unreachable")
        meta = meta if isinstance(meta, dict) else {}
        keys = [k for k in (raw or []) if isinstance(k, dict)]
        peer = {normalize_dnskey(k.get("dnskey")) for k in keys if k.get("dnskey")}
        if not prim and not peer:
            state = "both_unsigned"
        elif peer == prim:
            state = "same_keys"
        elif not peer:
            secondary = meta.get("presigned") is True or str(meta.get("kind") or "").lower() in ("slave", "secondary")
            state = "secondary" if secondary else "unsigned"
        else:
            state = "different_keys"
        tags = sorted(t for t in (compute_key_tag(k.get("dnskey")) for k in keys) if t is not None)
        return _peer(name, aw, state, key_tags=tags, zone_kind=meta.get("kind"))

    return list(await asyncio.gather(*(one(n) for n in names)))


def _empty_history() -> dict[str, Optional[str]]:
    return {f: None for f in KEY_HISTORY_FIELDS}


async def key_history(db: AsyncSession, zone_norm: str, server_name: str) -> dict[str, dict]:
    """Zeitstempel je Schluessel-ID aus erfolgreichen DNSSEC-Audits dieses Servers (neueste Zeile gewinnt).

    ``KEY_DELETE`` schliesst eine ID (aeltere Zeilen gehoeren zu einem frueheren Schluessel mit derselben ID),
    ``DNSSEC_DISABLE`` beendet die Auswertung (alle aelteren Schluessel sind geloescht). Fehler -> ``{}``.
    """
    actions = ("DNSSEC_ENABLE", "DNSSEC_DISABLE", "KEY_CREATE", "KEY_ACTIVATE", "KEY_DEACTIVATE", "KEY_PUBLISH",
               "KEY_UNPUBLISH", "KEY_UPDATE", "KEY_DELETE")
    try:
        rows = (await db.execute(
            select(AuditLog.timestamp, AuditLog.action, AuditLog.details)
            .where(
                AuditLog.resource_type == "dnssec_key",
                AuditLog.zone_name == zone_norm,
                AuditLog.server_name == server_name,
                AuditLog.status == "success",
                AuditLog.action.in_(actions),
            )
            .order_by(AuditLog.id.desc())
            .limit(KEY_HISTORY_LIMIT)
        )).all()
    except Exception as exc:  # noqa: BLE001 - Verlauf ist nur Zusatzinfo
        logger.warning("DNSSEC-Schluesselverlauf nicht lesbar (%s): %s", zone_norm, exc)
        return {}

    out: dict[str, dict] = {}
    closed: set[int] = set()

    def put(key_id: Any, field: str, ts) -> None:
        try:
            kid = int(key_id)
        except (TypeError, ValueError):
            return
        if kid in closed:
            return
        entry = out.setdefault(str(kid), _empty_history())
        if entry[field] is None:
            entry[field] = iso_utc(ts)

    try:
        for ts, action, details in rows:
            d = details if isinstance(details, dict) else {}
            if action == "DNSSEC_DISABLE":
                break
            if action == "KEY_DELETE":
                try:
                    closed.add(int(d.get("key_id")))
                except (TypeError, ValueError):
                    pass
                continue
            if action == "DNSSEC_ENABLE":
                for k in d.get("keys") or []:
                    if isinstance(k, dict):
                        put(k.get("key_id"), "created_at", ts)
                        put(k.get("key_id"), "activated_at", ts)
                continue
            kid = d.get("key_id")
            if action == "KEY_CREATE":
                put(kid, "created_at", ts)
                if d.get("active"):
                    put(kid, "activated_at", ts)
                if d.get("published") is not False:
                    put(kid, "published_at", ts)
            elif action == "KEY_ACTIVATE":
                put(kid, "activated_at", ts)
            elif action == "KEY_DEACTIVATE":
                put(kid, "deactivated_at", ts)
            elif action == "KEY_PUBLISH":
                put(kid, "published_at", ts)
            elif action == "KEY_UNPUBLISH":
                put(kid, "unpublished_at", ts)
            elif action == "KEY_UPDATE":
                b = d.get("before") if isinstance(d.get("before"), dict) else {}
                a = d.get("after") if isinstance(d.get("after"), dict) else {}
                if b.get("active") is not True and a.get("active") is True:
                    put(kid, "activated_at", ts)
                if b.get("active") is True and a.get("active") is False:
                    put(kid, "deactivated_at", ts)
                if b.get("published") is False and a.get("published") is True:
                    put(kid, "published_at", ts)
                if b.get("published") is not False and a.get("published") is False:
                    put(kid, "unpublished_at", ts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("DNSSEC-Schluesselverlauf nicht auswertbar (%s): %s", zone_norm, exc)
        return {}
    return out


def capabilities(ctx: ZoneDnssecContext) -> dict:
    return {
        "supports_published": ctx.supports_published,
        "algorithms": [{"name": name, **info} for name, info in logic.ALGORITHMS.items()],
        "rsa_bits": list(logic.RSA_BITS_ALLOWED),
        "nsec3_max_iterations": logic.NSEC3_MAX_ITERATIONS,
        "max_keys": logic.MAX_KEYS_PER_ZONE,
        "parent_ds_check": False,  # Teil B (F4-C)
    }


async def build_status(db: AsyncSession, user, server_name: str, zone_id: str, client, *,
                       ctx: ZoneDnssecContext, peers: bool = True, history: bool = True) -> dict:
    """Gesamtstatus (Spec 3.2). Peers, Version und Verlauf brechen den Status nie."""
    from app.core.auth import has_zone_access  # lazy: core.auth importiert viele Module

    zone_norm = normalize_zone_name(zone_id)
    rollover = logic.derive_rollover(ctx.keys)
    roles = logic.rollover_roles(rollover)
    ds_map = logic.ds_status_map(ctx.keys)
    keys_out = [enrich_key(raw, ds_status=ds_map.get(view.id), rollover_role=roles.get(view.id))
                for raw, view in zip(ctx.raw_keys, ctx.keys)]
    server_writable = await fanout.is_server_writable(db, server_name)
    user_can_write = await has_zone_access(db, user, zone_id, write=True)
    peer_list = None
    if peers:
        try:
            peer_list = await probe_peers(db, server_name, zone_id, ctx.raw_keys)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DNSSEC-Peer-Pruefung fehlgeschlagen (%s): %s", zone_norm, exc)
            peer_list = []
    hist: dict = {}
    if history:
        current = {str(k.id) for k in ctx.keys}
        hist = {k: v for k, v in (await key_history(db, zone_norm, server_name)).items() if k in current}
    nsec = logic.nsec_info(ctx.meta, ctx.keys)
    hints = logic.build_hints(
        keys=ctx.keys, meta=ctx.meta, nsec=nsec, supports_published=ctx.supports_published, version=ctx.version,
        server=server_name, server_writable=server_writable, peers=[p for p in (peer_list or [])],
        rollover=rollover, raw_keys=ctx.raw_keys,
    )
    manageable = not ctx.presigned
    api_rectify = ctx.meta.get("api_rectify")
    return {
        "zone": zone_norm,
        "server": server_name,
        "server_version": ctx.version,
        "zone_kind": ctx.kind,
        "presigned": ctx.presigned,
        "manageable": manageable,
        "signed": ctx.signed,
        "dnssec_flag": bool(ctx.meta.get("dnssec")),
        "api_rectify": api_rectify if isinstance(api_rectify, bool) else None,
        "nsec": nsec,
        "keys": keys_out,
        "rollover": rollover,
        "key_history": hist,
        "peers": peer_list,
        "hints": hints,
        "capabilities": capabilities(ctx),
        "server_writable": server_writable,
        "user_can_write": user_can_write,
        "can_write": bool(user_can_write and server_writable and manageable),
    }


# =============================================================================================
# Nach Schluesselaenderungen: Serial erhoehen + NOTIFY [D10]
# =============================================================================================
def no_follow_up() -> dict:
    return {"serial_bumped": False, "serial": None, "serial_error": None, "notified": False, "notify_error": None}


async def after_key_change(db: AsyncSession, client, zone: str, *, bump_serial: Optional[bool],
                           kind: Optional[str] = None, user=None, trigger: Optional[str] = None) -> dict:
    """Serial + 1 (SOA-REPLACE, Fan-out Betriebsart A nur auf dem Server ``client``) und NOTIFY.

    Nur bei Zonen vom Typ Master/Producer und ``bump_serial is not False`` (``None`` = Standard: ja). Audits:
    ``UPDATE`` (resource_type ``record``, SOA vorher/nachher, ``source: "dnssec"``) und ``ZONE_NOTIFY``; Fehler
    gehen als Fehler-Audit (detached) und als ``serial_error``/``notify_error`` in das Ergebnis – nie als Exception.
    Ohne Serial-Erhoehung kein NOTIFY.
    """
    out = no_follow_up()
    if bump_serial is False:
        return out
    zone_norm = normalize_zone_name(zone)
    server = str(getattr(client, "name", "?"))
    user_id = getattr(user, "id", None)
    try:
        if kind is None:
            kind = (await client.get_zone_meta(zone) or {}).get("kind")
        if not logic.is_primary_kind(kind):
            return out
        before_rrsets = await client.get_rrsets(zone, zone_norm, "SOA")
        before = rrset_snapshot(before_rrsets, zone_norm, "SOA")
        if not before or not before.get("records"):
            raise ValueError("Kein SOA-Record in der Zone gefunden.")
        record = before["records"][0]
        new_content, old_serial, new_serial = logic.bump_soa_content(record.get("content", ""))
        payload = [{
            "name": zone_norm, "type": "SOA", "ttl": int(before.get("ttl") or 3600), "changetype": "REPLACE",
            "records": [{"content": new_content, "disabled": bool(record.get("disabled", False))}],
        }]
        res = await fanout.apply_rrsets(
            db, server, zone, rrsets=payload, expected_after=payload, targets=[(server, client)], info={},
            before_state={(zone_norm, "SOA"): before},
        )
    except (PowerDNSAPIError, ValueError) as exc:
        text = pdns_error_text(exc) if isinstance(exc, PowerDNSAPIError) else str(exc)
        out["serial_error"] = text
        await write_audit(db, "UPDATE", "record", zone_norm, user_id=user_id, status="error", error_message=text,
                          details={"zone": zone_norm, "type": "SOA", "source": "dnssec", "trigger": trigger},
                          server_name=server, zone_name=zone_norm)
        logger.warning("DNSSEC: Serial-Erhoehung fuer %s auf %s fehlgeschlagen: %s", zone_norm, server, text)
        return out

    if not res.primary_success:
        err = res.primary_error
        text = pdns_error_text(err) if err is not None else str(res.primary_status or "unbekannter Fehler")
        out["serial_error"] = text
        await write_audit(db, "UPDATE", "record", zone_norm, user_id=user_id, status="error", error_message=text,
                          details={"zone": zone_norm, "type": "SOA", "source": "dnssec", "trigger": trigger,
                                   "fanout": res.summary, "primary_outcome": res.primary_outcome},
                          server_name=server, zone_name=zone_norm)
        return out

    after = rrset_snapshot(payload, zone_norm, "SOA")
    after_source = "computed"
    try:
        reread = rrset_snapshot(await client.get_rrsets(zone, zone_norm, "SOA"), zone_norm, "SOA")
        if reread and reread.get("records"):
            after, after_source = reread, "reread"
            new_serial = logic.soa_serial(reread["records"][0].get("content")) or new_serial
    except PowerDNSAPIError as exc:
        logger.info("SOA nach der Serial-Erhoehung nicht lesbar (%s): %s", zone_norm, exc.status_code)
    out["serial_bumped"] = True
    out["serial"] = new_serial
    await write_audit(
        db, "UPDATE", "record", zone_norm, user_id=user_id, server_name=server, zone_name=zone_norm,
        details={
            "version": 2, "zone": zone_norm,
            "changes": [{"name": zone_norm, "type": "SOA", "before": before, "after": after}],
            "after_source": after_source, "fanout": res.summary, "primary_outcome": res.primary_outcome,
            "source": "dnssec", "trigger": trigger,
            "type": "SOA", "old": record.get("content"), "new": new_content,
            "serial_before": old_serial, "serial_after": new_serial,
        },
    )

    try:
        await client.notify_zone(zone)
    except PowerDNSAPIError as exc:
        out["notify_error"] = pdns_error_text(exc)
        await write_audit(db, "ZONE_NOTIFY", "zone", zone_norm, user_id=user_id, status="error",
                          error_message=exc.pdns_message[:500], server_name=server, zone_name=zone_norm,
                          details={"server": server, "status_code": exc.status_code, "source": "dnssec",
                                   "trigger": trigger})
        logger.warning("DNSSEC: NOTIFY fuer %s auf %s fehlgeschlagen: %s", zone_norm, server, exc.status_code)
        return out
    out["notified"] = True
    await write_audit(db, "ZONE_NOTIFY", "zone", zone_norm, user_id=user_id, server_name=server, zone_name=zone_norm,
                      details={"server": server, "source": "dnssec", "trigger": trigger, "serial": new_serial})
    return out


# =============================================================================================
# Audit-/Ereignisdaten fuer das Aktivieren (Router und Zonenanlage)
# =============================================================================================
def enable_audit_details(zone_norm: str, opts, created: list[dict], *, source: str) -> dict:
    number = logic.ALGORITHMS.get(opts.algorithm, {}).get("number")
    return {
        "zone": zone_norm, "source": source, "algorithm": opts.algorithm, "algorithm_number": number,
        "key_model": opts.key_model, "bits": opts.bits, "nsec_mode": opts.nsec_mode,
        "nsec3param": opts.effective_nsec3param(), "nsec3narrow": opts.effective_narrow(),
        "keys": [key_brief(k) for k in created],
    }


def enabled_event_data(zone_norm: str, server: str, opts, created: list[dict]) -> dict:
    """``dnssec.enabled`` (F6 5.3 + F4 3.6): Schluessel ohne Privatteil, DS je Schluessel."""
    return {
        "zone": zone_norm,
        "server": server,
        "algorithm": opts.algorithm,
        "key_model": opts.key_model,
        "nsec3param": opts.effective_nsec3param() or None,
        "keys": [
            {"id": int(k.get("id") or 0), "keytype": k.get("keytype"), "algorithm": k.get("algorithm"),
             "active": k.get("active"), "published": k.get("published"),
             "key_tag": compute_key_tag(k.get("dnskey")),
             "ds": [line for line in (k.get("ds") or []) if isinstance(line, str)]}
            for k in created
        ],
    }


async def enable_dnssec_on_new_zone(db: AsyncSession, client, server_name: str, zone_name: str, opts,
                                    user) -> Optional[str]:
    """DNSSEC direkt nach dem Anlegen einer Zone (Aufrufer ``routers/zones.create_zone`` ab F4-B).

    Erfolg: Audit ``DNSSEC_ENABLE`` (``source="zone_create"``) + Ereignis ``dnssec.enabled``, Rueckgabe ``None``.
    Fehler (``DnssecConflict``/``DnssecStepError``/``PowerDNSAPIError``): Fehler-Audit, Rueckgabe kurzer deutscher
    Fehlertext fuer ``created; dnssec-error: <text>``. Wirft nie (ausser bei Programmierfehlern).
    """
    zone_norm = normalize_zone_name(zone_name)
    user_id = getattr(user, "id", None)
    base = enable_audit_details(zone_norm, opts, [], source="zone_create")
    try:
        ctx = await load_context(client, zone_name, with_version=False)
        result = await enable_dnssec(client, zone_name, opts, ctx)
    except DnssecConflict as e:
        await write_audit(db, "DNSSEC_ENABLE", "dnssec_key", zone_norm, user_id=user_id, status="error",
                          error_message=e.message, details=base, server_name=server_name, zone_name=zone_norm)
        return e.message
    except DnssecStepError as e:
        text = f"{e.step}: {pdns_error_text(e.exc)}"
        await write_audit(db, "DNSSEC_ENABLE", "dnssec_key", zone_norm, user_id=user_id, status="error",
                          error_message=pdns_error_text(e.exc),
                          details={**base, "step": e.step, "rollback": e.rollback},
                          server_name=server_name, zone_name=zone_norm)
        return text
    except PowerDNSAPIError as e:
        await write_audit(db, "DNSSEC_ENABLE", "dnssec_key", zone_norm, user_id=user_id, status="error",
                          error_message=pdns_error_text(e), details=base, server_name=server_name,
                          zone_name=zone_norm)
        return pdns_error_text(e)
    if result["already_enabled"]:
        return None  # Zone war schon signiert (z. B. gemeinsame Datenbank) – nichts geaendert
    created = result["created"]
    audit = await write_audit(db, "DNSSEC_ENABLE", "dnssec_key", zone_norm, user_id=user_id,
                              details=enable_audit_details(zone_norm, opts, created, source="zone_create"),
                              server_name=server_name, zone_name=zone_norm)
    await webhook_outbox.enqueue_event(
        db, "dnssec.enabled", actor=user, zone=zone_norm, server=server_name,
        data=enabled_event_data(zone_norm, server_name, opts, created),
        audit_log_id=audit.id if audit else None,
    )
    logger.info("DNSSEC DNSSEC_ENABLE zone=%s server=%s key=- user=%s (zone_create)", zone_norm, server_name, user_id)
    return None
