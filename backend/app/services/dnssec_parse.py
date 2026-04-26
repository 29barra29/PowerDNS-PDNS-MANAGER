"""Hilfen zum Parsen von DS- und DNSKEY-Daten (PowerDNS API / Registrar-Formulare)."""
from __future__ import annotations

import re
from typing import Any, Optional

# IANA DNSSEC algorithm numbers (DNSKEY) — subset + „rest“
_ALGORITHM_NAMES: dict[int, str] = {
    1: "RSAMD5 (deprecated)",
    3: "DSA/SHA-1",
    5: "RSASHA-1",
    6: "DSA-NSEC3-SHA1",
    7: "RSASHA1-NSEC3-SHA1",
    8: "RSASHA-256",
    10: "RSASHA-512",
    12: "ECC-GOST",
    13: "ECDSAP256SHA256",
    14: "ECDSAP384SHA384",
    15: "ED25519",
    16: "ED448",
}

# Digest types (DS record) — English mnemonics (UI may translate)
_DIGEST_NAMES: dict[int, str] = {
    1: "SHA-1",
    2: "SHA-256 (most registrars)",
    3: "GOST (rare)",
    4: "SHA-384",
}


def _algorithm_name(n: int) -> str:
    return _ALGORITHM_NAMES.get(n, f"Algorithm {n}")


def _digest_type_name(n: int) -> str:
    return _DIGEST_NAMES.get(n, f"Digest-Typ {n}")


def parse_ds_line(ds: str) -> dict[str, Any]:
    """Parst eine DS-Zeile: „KeyTag Algo DigestType HexDigest“.

    Liefert u. a. ``recommended: True`` für Digest-Typ 2 (SHA-256) — das wählen
    die meisten Registrare.
    """
    s = (ds or "").strip()
    if not s:
        return {"error": "empty", "raw": s}
    parts = s.split()
    if len(parts) < 4:
        return {"error": "format", "raw": s, "parts": parts}

    try:
        key_tag = int(parts[0])
        algorithm = int(parts[1])
        digest_type = int(parts[2])
    except ValueError:
        return {"error": "numeric", "raw": s}

    digest_hex = parts[3]
    if len(parts) > 4:
        digest_hex = " ".join(parts[3:])  # falls Leerzeichen im Hex (unüblich)

    return {
        "key_tag": key_tag,
        "algorithm": algorithm,
        "algorithm_name": _algorithm_name(algorithm),
        "digest_type": digest_type,
        "digest_type_name": _digest_type_name(digest_type),
        "digest_hex": digest_hex.replace(" ", "").lower(),
        "recommended": digest_type == 2,
        "raw": s,
    }


def parse_dnskey_rdata(dnskey: Optional[str]) -> Optional[dict[str, Any]]:
    """Parst PowerDNS ``dnskey``-String: „Flags Protokoll Algorithmus Base64...“

    Häufige Flags: 256 = ZSK, 257 = KSK (SEP-Bit + Zone-Key-Bit).
    """
    s = (dnskey or "").strip()
    if not s:
        return None

    m = re.match(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(.+)$", s, re.DOTALL)
    if not m:
        return {"raw": s, "error": "unparseable"}

    flags = int(m.group(1))
    protocol = int(m.group(2))
    algorithm = int(m.group(3))
    public_b64 = m.group(4).strip()

    if flags == 257:
        role = "KSK (257)"
    elif flags == 256:
        role = "ZSK (256)"
    else:
        role = f"Flags {flags}"

    return {
        "flags": flags,
        "flags_role": role,
        "protocol": protocol,
        "algorithm": algorithm,
        "algorithm_name": _algorithm_name(algorithm),
        "public_key_base64": public_b64,
        "raw": s,
    }
