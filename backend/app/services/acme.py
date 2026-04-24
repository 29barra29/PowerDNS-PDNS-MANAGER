"""ACME / DNS-01 Automation Service.

Macht zwei Dinge:

1.  **Token-Verwaltung**: Erzeugt scoped Bearer-Tokens, hashed sie (SHA-256),
    speichert sie in ``acme_tokens``. Der Plaintext-Token verlaesst die App
    genau einmal - direkt nach der Erstellung im API-Response. Danach kann er
    nirgendwo mehr eingesehen werden (gleiches Pattern wie GitHub PATs).

2.  **DNS-01 Records anlegen/loeschen**: ``_acme-challenge.<domain>.`` als TXT
    in PowerDNS upserten oder den passenden Wert wieder rauslo"sen, ueber den
    bestehenden ``pdns_manager``. Wenn mehrere Server die Zone halten und
    ``allow_writes`` sind, wird auf allen geschrieben (PowerDNS-Slaves bekommen
    die Updates per AXFR/IXFR sowieso, aber bei getrennten DBs muss man manuell
    vervielfaeltigen).

Sicherheits-Modell:
- Tokens sind immer auf eine konkrete Liste von Zonen gescoped.
- Token kann ausschliesslich Records mit Namen ``_acme-challenge.*`` und Type
  ``TXT`` schreiben/loeschen. Keine A/AAAA/MX/sonst was.
- TTL wird hartkodiert kurz gesetzt (60s), damit alte Challenges schnell aus
  Resolvern verschwinden.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AcmeToken, ServerConfig
from app.services.pdns_client import pdns_manager, PowerDNSAPIError

logger = logging.getLogger(__name__)


# Tokens haben den Prefix ``dnsmgr_acme_`` damit man sie in Logs / Configs
# sofort als unsere wiedererkennt (analog ``ghp_``, ``glpat-`` etc.).
TOKEN_PREFIX = "dnsmgr_acme_"
# 32 Bytes urlsafe = 43 Zeichen Body. Plus Prefix ~55 Zeichen total.
_TOKEN_BYTES = 32

# DNS-01 challenge specifics
ACME_CHALLENGE_PREFIX = "_acme-challenge."
ACME_TTL_SECONDS = 60


# ---------------------------------------------------------------------------
# Helpers: Zonen-Normalisierung
# ---------------------------------------------------------------------------
def normalize_zone(zone: str) -> str:
    """Lowercase + Trailing-Dot. PowerDNS speichert Zonen so, der ACL-Vergleich
    muss exakt diese Form verwenden."""
    z = (zone or "").strip().lower()
    if not z:
        return z
    if not z.endswith("."):
        z += "."
    return z


def normalize_fqdn(fqdn: str) -> str:
    """Lowercase + Trailing-Dot. ``.example.com`` -> ``example.com.``."""
    return normalize_zone(fqdn)


def find_matching_zone(fqdn: str, allowed_zones: Iterable[str]) -> Optional[str]:
    """Sucht die laengste passende Zone aus ``allowed_zones`` zu ``fqdn``.

    Wenn ``fqdn = smtp.gtgmail.de.`` und ``allowed_zones = ["gtgmail.de.",
    "smtp.gtgmail.de."]`` wird ``smtp.gtgmail.de.`` zurueckgegeben (longest match).

    Gibt ``None`` zurueck wenn keine Zone matcht.
    """
    target = normalize_fqdn(fqdn)
    if not target:
        return None
    candidates = [normalize_zone(z) for z in (allowed_zones or []) if z]
    candidates = [z for z in candidates if z and (target == z or target.endswith("." + z))]
    if not candidates:
        return None
    candidates.sort(key=len, reverse=True)
    return candidates[0]


# ---------------------------------------------------------------------------
# Token-Erzeugung & -Verifikation
# ---------------------------------------------------------------------------
def _hash_token(token: str) -> str:
    """SHA-256 in hex. Konstantes Schema, keine Salts noetig - der Token selbst
    ist 256 Bit Entropie, das reicht ohne Salt fuer Lookup-by-Hash."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_prefix(token: str) -> str:
    """Erste Zeichen, die wir im UI als Wiedererkennung zeigen (z.B.
    ``dnsmgr_acme_AbCd…``). Nicht sicherheitsrelevant."""
    return token[: len(TOKEN_PREFIX) + 4]


def generate_token() -> str:
    """Erzeugt einen neuen Plaintext-Token. Wird nach dem Aufruf NICHT in der DB
    gespeichert - das macht ``create_token``."""
    body = secrets.token_urlsafe(_TOKEN_BYTES)
    return f"{TOKEN_PREFIX}{body}"


async def create_token(
    db: AsyncSession,
    *,
    name: str,
    allowed_zones: List[str],
    created_by_id: Optional[int],
) -> Tuple[AcmeToken, str]:
    """Erzeugt einen Token, persistiert nur den Hash, gibt (Row, Plaintext) zurueck.

    Plaintext NICHT loggen, NICHT in andere DB-Felder schreiben - nur dem User im
    HTTP-Response zeigen.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Token-Name darf nicht leer sein")
    zones = [normalize_zone(z) for z in (allowed_zones or []) if z and z.strip()]
    zones = [z for z in zones if z]
    if not zones:
        raise ValueError("Mindestens eine erlaubte Zone angeben")

    plaintext = generate_token()
    row = AcmeToken(
        name=name,
        token_prefix=_token_prefix(plaintext),
        token_hash=_hash_token(plaintext),
        allowed_zones=zones,
        created_by_id=created_by_id,
        is_active=True,
    )
    db.add(row)
    await db.flush()  # damit row.id gesetzt ist
    return row, plaintext


async def list_tokens(db: AsyncSession) -> List[AcmeToken]:
    """Alle Tokens (ohne Plaintext - der ist eh nicht in der DB)."""
    result = await db.execute(
        select(AcmeToken).order_by(AcmeToken.created_at.desc())
    )
    return list(result.scalars().all())


async def get_token_by_id(db: AsyncSession, token_id: int) -> Optional[AcmeToken]:
    result = await db.execute(select(AcmeToken).where(AcmeToken.id == token_id))
    return result.scalar_one_or_none()


async def delete_token(db: AsyncSession, token_id: int) -> bool:
    row = await get_token_by_id(db, token_id)
    if not row:
        return False
    await db.delete(row)
    return True


async def verify_token(
    db: AsyncSession,
    plaintext: Optional[str],
    *,
    remote_ip: Optional[str] = None,
) -> Optional[AcmeToken]:
    """Schaut den uebergebenen Plaintext-Token in der DB nach (per Hash).

    Aktualisiert ``last_used_at`` / ``last_used_ip`` als Side-Effekt - das hilft
    dem Admin im UI zu sehen, ob/wann der Token zuletzt von certbot benutzt wurde.

    Gibt ``None`` zurueck wenn der Token unbekannt oder deaktiviert ist.
    """
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None
    h = _hash_token(plaintext)
    result = await db.execute(
        select(AcmeToken).where(AcmeToken.token_hash == h, AcmeToken.is_active == True)  # noqa: E712
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    row.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if remote_ip:
        # IP-Spalte ist 64 Zeichen breit, IPv6 + zone passt rein
        row.last_used_ip = remote_ip[:64]
    await db.flush()
    return row


# ---------------------------------------------------------------------------
# PowerDNS: TXT-Record fuer _acme-challenge.<domain>
# ---------------------------------------------------------------------------
def _txt_value(s: str) -> str:
    """ACME-Challenge ist ein nackter Base64URL-String. PowerDNS will TXT-Werte
    als ``"..."`` quoted - sonst wird er beim Anlegen abgelehnt."""
    s = (s or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


async def _writable_clients_for_zone(db: AsyncSession, zone: str):
    """Liefert alle PowerDNS-Clients, auf denen wir die Zone ``zone`` schreiben
    duerfen. Wir bevorzugen Server mit ``allow_writes=True`` - wenn keiner
    explizit darf, fallback auf alle aktiven Server (z.B. Single-Server-Setup
    ohne Konfig)."""
    result = await db.execute(select(ServerConfig).where(ServerConfig.is_active == True))  # noqa: E712
    configs = list(result.scalars().all())

    writable = [c for c in configs if getattr(c, "allow_writes", True)]
    candidates = writable if writable else configs

    out = []
    for cfg in candidates:
        try:
            client = pdns_manager.get_client(cfg.name)
        except ValueError:
            continue
        # Hat der Server die Zone? Wenn nein, ueberspringen - kein Fehler
        try:
            zones = await client.list_zones(timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ACME: Server %s nicht erreichbar (%s) - skip", cfg.name, exc)
            continue
        names = {(z.get("name") or "").lower() for z in (zones or [])}
        if zone.lower() in names:
            out.append((cfg.name, client))
    return out


async def present_challenge(
    db: AsyncSession,
    *,
    fqdn: str,
    validation: str,
    allowed_zones: Iterable[str],
) -> dict:
    """Legt den ``_acme-challenge.<fqdn>`` TXT mit ``validation`` an.

    - Wenn schon TXT-Records mit demselben Namen existieren, wird der neue Wert
      hinzugefuegt (Wildcard + Domain auf gleicher Zone braucht 2 TXTs).
    - Idempotent: ein erneuter Aufruf mit gleichem ``validation`` aendert nichts.
    """
    target_fqdn = normalize_fqdn(fqdn)
    zone = find_matching_zone(target_fqdn, allowed_zones)
    if not zone:
        raise PermissionError(f"Domain {target_fqdn!r} liegt in keiner erlaubten Zone")

    record_name = ACME_CHALLENGE_PREFIX + target_fqdn  # ``_acme-challenge.smtp.gtgmail.de.``
    new_value = _txt_value(validation)

    clients = await _writable_clients_for_zone(db, zone)
    if not clients:
        raise RuntimeError(f"Keine schreibfaehigen PowerDNS-Server fuer Zone {zone}")

    results = []
    for server_name, client in clients:
        try:
            zone_data = await client.get_zone(zone)
        except PowerDNSAPIError as exc:
            results.append({"server": server_name, "ok": False, "error": exc.detail})
            continue

        existing_values: list[str] = []
        for rr in zone_data.get("rrsets", []):
            if rr.get("name") == record_name and rr.get("type") == "TXT":
                existing_values = [r.get("content") for r in rr.get("records", []) if r.get("content")]
                break

        merged = list(existing_values)
        if new_value not in merged:
            merged.append(new_value)

        try:
            await client.update_records(zone, [{
                "name": record_name,
                "type": "TXT",
                "ttl": ACME_TTL_SECONDS,
                "changetype": "REPLACE",
                "records": [{"content": v, "disabled": False} for v in merged],
            }])
            results.append({"server": server_name, "ok": True, "values": len(merged)})
        except PowerDNSAPIError as exc:
            results.append({"server": server_name, "ok": False, "error": exc.detail})

    if not any(r["ok"] for r in results):
        raise RuntimeError(f"ACME-TXT konnte auf keinem Server gesetzt werden: {results}")

    return {
        "zone": zone,
        "record": record_name,
        "ttl": ACME_TTL_SECONDS,
        "servers": results,
    }


async def cleanup_challenge(
    db: AsyncSession,
    *,
    fqdn: str,
    validation: str,
    allowed_zones: Iterable[str],
) -> dict:
    """Entfernt ``validation`` aus dem ``_acme-challenge.<fqdn>`` TXT-Record.

    - Wenn nach Entfernen keine Werte mehr uebrig sind, wird der RR-Set
      komplett geloescht.
    - Idempotent: Aufruf ohne passendem Wert ist OK.
    """
    target_fqdn = normalize_fqdn(fqdn)
    zone = find_matching_zone(target_fqdn, allowed_zones)
    if not zone:
        raise PermissionError(f"Domain {target_fqdn!r} liegt in keiner erlaubten Zone")

    record_name = ACME_CHALLENGE_PREFIX + target_fqdn
    drop_value = _txt_value(validation)

    clients = await _writable_clients_for_zone(db, zone)
    if not clients:
        # Bei cleanup nicht hart fehlschlagen - es kann sein, dass die Zone
        # bereits weg ist (manuell entfernt). certbot will das ueberleben.
        return {"zone": zone, "record": record_name, "servers": []}

    results = []
    for server_name, client in clients:
        try:
            zone_data = await client.get_zone(zone)
        except PowerDNSAPIError as exc:
            results.append({"server": server_name, "ok": False, "error": exc.detail})
            continue

        existing_values: list[str] = []
        for rr in zone_data.get("rrsets", []):
            if rr.get("name") == record_name and rr.get("type") == "TXT":
                existing_values = [r.get("content") for r in rr.get("records", []) if r.get("content")]
                break

        if not existing_values:
            results.append({"server": server_name, "ok": True, "noop": True})
            continue

        remaining = [v for v in existing_values if v != drop_value]

        try:
            if remaining:
                await client.update_records(zone, [{
                    "name": record_name,
                    "type": "TXT",
                    "ttl": ACME_TTL_SECONDS,
                    "changetype": "REPLACE",
                    "records": [{"content": v, "disabled": False} for v in remaining],
                }])
                results.append({"server": server_name, "ok": True, "values": len(remaining)})
            else:
                await client.update_records(zone, [{
                    "name": record_name,
                    "type": "TXT",
                    "changetype": "DELETE",
                }])
                results.append({"server": server_name, "ok": True, "deleted": True})
        except PowerDNSAPIError as exc:
            results.append({"server": server_name, "ok": False, "error": exc.detail})

    return {
        "zone": zone,
        "record": record_name,
        "servers": results,
    }
