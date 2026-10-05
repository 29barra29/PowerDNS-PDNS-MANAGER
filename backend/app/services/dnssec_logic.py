"""DNSSEC-Fachlogik ohne I/O (F4 5.1).

Rein und ohne App-Imports ausser ``dnssec_parse``: Algorithmen und Parameter pruefen, NSEC3-Parameter
parsen, PowerDNS-Versionen bewerten, Schluessel-Sichten bilden, Schutzregeln simulieren, die
Rollover-Phase ableiten, den DS-Status je Schluessel bestimmen und die Hinweise fuer den Status bauen.
Ausserdem ein kleiner SOA-Helfer fuer die Serial-Erhoehung nach Schluesselaenderungen [D10].

Maßgeblich fuer "KSK/CSK" ist das SEP-Bit im DNSKEY (Flags 257), nicht PowerDNS' ``keytype``:
PowerDNS meldet ``keytype`` abhaengig vom aktiven Schluesselsatz (Spec 12 Nr. 2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Optional

from app.services.dnssec_parse import _ALGORITHM_NAMES, parse_dnskey_rdata, parse_ds_line

# ---------------------------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------------------------
ALGORITHMS: dict[str, dict] = {
    "ECDSAP256SHA256": {"number": 13, "rsa": False, "recommended": True, "build_dependent": False},
    "ECDSAP384SHA384": {"number": 14, "rsa": False, "recommended": False, "build_dependent": False},
    "ED25519": {"number": 15, "rsa": False, "recommended": False, "build_dependent": True},
    "ED448": {"number": 16, "rsa": False, "recommended": False, "build_dependent": True},
    "RSASHA256": {"number": 8, "rsa": True, "recommended": False, "build_dependent": False},
    "RSASHA512": {"number": 10, "rsa": True, "recommended": False, "build_dependent": False},
}
ALGORITHM_ALIASES: dict[str, str] = {  # lower-case Eingabe -> Mnemonic
    "ecdsap256sha256": "ECDSAP256SHA256", "ecdsa256": "ECDSAP256SHA256", "13": "ECDSAP256SHA256",
    "ecdsap384sha384": "ECDSAP384SHA384", "ecdsa384": "ECDSAP384SHA384", "14": "ECDSAP384SHA384",
    "ed25519": "ED25519", "15": "ED25519", "ed448": "ED448", "16": "ED448",
    "rsasha256": "RSASHA256", "8": "RSASHA256", "rsasha512": "RSASHA512", "10": "RSASHA512",
}
# Mnemonics, wie PowerDNS sie in GET-Antworten liefert (auch fuer Bestandsschluessel mit Alt-Algorithmen)
PDNS_ALGORITHM_NUMBERS: dict[str, int] = {
    "RSAMD5": 1, "DH": 2, "DSA": 3, "RSASHA1": 5, "DSANSEC3SHA1": 6, "RSASHA1NSEC3SHA1": 7,
    "RSASHA256": 8, "RSASHA512": 10, "ECCGOST": 12, "ECDSAP256SHA256": 13, "ECDSAP384SHA384": 14,
    "ED25519": 15, "ED448": 16,
}
DEPRECATED_ALGORITHM_NUMBERS = frozenset({1, 3, 5, 6, 7, 12})  # RFC 8624: nicht mehr zum Signieren
NSEC3_INCAPABLE_ALGORITHM_NUMBERS = frozenset({1, 3, 5})
RSA_BITS_ALLOWED = (2048, 3072, 4096)
RSA_BITS_DEFAULT = 2048
NSEC3_MAX_ITERATIONS = 50
NSEC3_WARN_ITERATIONS = 10
MAX_KEYS_PER_ZONE = 6
SEP_FLAG = 257
MAX_SALT_HEX = 510
SERIAL_MODULUS = 2 ** 32
PRIMARY_KINDS = frozenset({"master", "primary", "producer"})  # Zonen mit Secondaries per AXFR/NOTIFY

ALGORITHM_ERROR = (
    "Algorithmus '{v}' wird nicht unterstützt. Erlaubt: ECDSAP256SHA256, ECDSAP384SHA384, ED25519, "
    "ED448, RSASHA256, RSASHA512."
)
BITS_RSA_ERROR = "Für RSA sind 2048, 3072 oder 4096 Bit erlaubt."
BITS_NON_RSA_ERROR = "bits ist nur bei RSA-Algorithmen erlaubt."
SALT_ERROR = (
    "Salt muss leer bzw. '-' sein oder aus Hex-Zeichen in gerader Anzahl bestehen (max. 255 Byte)."
)
ITERATIONS_ERROR = "NSEC3-Iterationen müssen zwischen 0 und 50 liegen (empfohlen: 0)."
NSEC3PARAM_FORMAT_ERROR = (
    "nsec3param muss das Format 'Algorithmus Flags Iterationen Salt' haben, z. B. '1 0 0 -'."
)
NSEC3PARAM_HASH_ERROR = "NSEC3-Hash-Algorithmus muss 1 (SHA-1) sein."
NSEC3PARAM_FLAGS_ERROR = "NSEC3-Flags müssen 0 oder 1 (Opt-Out) sein."

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?")


# ---------------------------------------------------------------------------------------------
# Algorithmen und Parameter
# ---------------------------------------------------------------------------------------------
def normalize_algorithm(value: str | int) -> str:
    """Mnemonic aus Name/Alias/Zahl (case-insensitiv); nur die Allowlist ``ALGORITHMS``."""
    if isinstance(value, bool):
        raise ValueError(ALGORITHM_ERROR.format(v=value))
    raw = str(value if value is not None else "").strip()
    name = ALGORITHM_ALIASES.get(raw.lower())
    if name is None:
        raise ValueError(ALGORITHM_ERROR.format(v=raw))
    return name


def _mnemonic_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name or "").upper())


def algorithm_number(name: str | int | None) -> Optional[int]:
    """Algorithmusnummer zu einem PowerDNS-Mnemonic (auch Alt-Algorithmen) oder einer Zahl; sonst ``None``."""
    if name is None or isinstance(name, bool):
        return None
    if isinstance(name, int):
        return name
    s = str(name).strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s.upper() in ALGORITHMS:
        return ALGORITHMS[s.upper()]["number"]
    key = _mnemonic_key(s)
    if key in PDNS_ALGORITHM_NUMBERS:
        return PDNS_ALGORITHM_NUMBERS[key]
    for number, label in _ALGORITHM_NAMES.items():
        if _mnemonic_key(label.split(" ")[0]) == key:
            return number
    return None


def algorithm_label(number: Optional[int], name: Optional[str] = None) -> str:
    """Anzeige "ECDSAP256SHA256 (13)" fuer Hinweise und Fehlertexte."""
    if number is None:
        return str(name or "?")
    for mnemonic, info in ALGORITHMS.items():
        if info["number"] == number:
            return f"{mnemonic} ({number})"
    for mnemonic, n in PDNS_ALGORITHM_NUMBERS.items():
        if n == number:
            return f"{name or mnemonic} ({number})"
    return f"{name or 'Algorithmus'} ({number})"


def is_rsa(algorithm: str) -> bool:
    info = ALGORITHMS.get(str(algorithm or "").upper())
    return bool(info and info["rsa"])


def validate_bits(algorithm: str, bits: Optional[int]) -> Optional[int]:
    """RSA: ``None`` -> 2048, sonst 2048/3072/4096. Andere Algorithmen: ``bits`` muss ``None`` sein."""
    if is_rsa(algorithm):
        if bits is None:
            return RSA_BITS_DEFAULT
        if isinstance(bits, bool) or bits not in RSA_BITS_ALLOWED:
            raise ValueError(BITS_RSA_ERROR)
        return int(bits)
    if bits is not None:
        raise ValueError(BITS_NON_RSA_ERROR)
    return None


def normalize_salt(salt: Optional[str]) -> str:
    """``None``/""/"-" -> "-" (kein Salt); sonst Hex in gerader Anzahl, max. 510 Zeichen, klein."""
    s = (salt or "").strip()
    if s in ("", "-"):
        return "-"
    if not _HEX_RE.match(s) or len(s) % 2 or len(s) > MAX_SALT_HEX:
        raise ValueError(SALT_ERROR)
    return s.lower()


def validate_iterations(n: Any) -> int:
    if isinstance(n, bool):
        raise ValueError(ITERATIONS_ERROR)
    try:
        value = int(n)
    except (TypeError, ValueError):
        raise ValueError(ITERATIONS_ERROR) from None
    if isinstance(n, float) and n != value:
        raise ValueError(ITERATIONS_ERROR)
    if not 0 <= value <= NSEC3_MAX_ITERATIONS:
        raise ValueError(ITERATIONS_ERROR)
    return value


@dataclass(frozen=True)
class Nsec3Params:
    optout: bool
    iterations: int
    salt: str  # "-" = kein Salt

    def to_param(self) -> str:
        return f"1 {1 if self.optout else 0} {self.iterations} {self.salt}"


def parse_nsec3param(value: Optional[str]) -> Optional[Nsec3Params]:
    """Strenges Parsen (Eingaben): ``None``/leer -> ``None`` (NSEC), sonst genau vier Tokens mit Grenzen."""
    s = (value or "").strip()
    if not s:
        return None
    parts = s.split()
    if len(parts) != 4:
        raise ValueError(NSEC3PARAM_FORMAT_ERROR)
    if parts[0] != "1":
        raise ValueError(NSEC3PARAM_HASH_ERROR)
    if parts[1] not in ("0", "1"):
        raise ValueError(NSEC3PARAM_FLAGS_ERROR)
    if not parts[2].isdigit():
        raise ValueError(ITERATIONS_ERROR)
    return Nsec3Params(optout=parts[1] == "1", iterations=validate_iterations(parts[2]),
                       salt=normalize_salt(parts[3]))


def lax_parse_nsec3param(value: Optional[str]) -> Optional[Nsec3Params]:
    """Lesen von PowerDNS-Werten (Status): keine Grenzpruefung, Fehler -> ``None``."""
    parts = (value or "").split()
    if len(parts) != 4:
        return None
    try:
        flags = int(parts[1])
        iterations = int(parts[2])
    except ValueError:
        return None
    salt = parts[3].strip().lower() or "-"
    return Nsec3Params(optout=bool(flags & 1), iterations=iterations, salt=salt)


def nsec_info(meta: dict, keys: list["KeyView"]) -> dict:
    """``DnssecNsecInfo`` aus den Zonen-Metadaten (``mode`` None, wenn die Zone keine Schluessel hat)."""
    param = str((meta or {}).get("nsec3param") or "").strip()
    parsed = lax_parse_nsec3param(param) if param else None
    mode = None
    if keys:
        mode = "nsec3" if param else "nsec"
    return {
        "mode": mode,
        "nsec3param": param or None,
        "iterations": parsed.iterations if parsed else None,
        "salt": parsed.salt if parsed else None,
        "opt_out": parsed.optout if parsed else None,
        "narrow": bool((meta or {}).get("nsec3narrow")),
    }


def nsec_warnings(params: Optional[Nsec3Params]) -> list[str]:
    """Warn-Codes fuer NSEC3-Parameter (gleiche Codes wie die Status-Hinweise)."""
    if params is None:
        return []
    out = []
    if params.iterations > NSEC3_WARN_ITERATIONS:
        out.append("nsec3_iterations_high")
    elif params.iterations >= 1:
        out.append("nsec3_iterations_nonzero")
    if params.salt != "-":
        out.append("nsec3_salt")
    return out


# ---------------------------------------------------------------------------------------------
# PowerDNS-Version
# ---------------------------------------------------------------------------------------------
def parse_pdns_version(v: Optional[str]) -> Optional[tuple[int, int, int]]:
    """``"4.9.4-1+deb12"`` -> ``(4, 9, 4)``; nicht erkennbar -> ``None``."""
    m = _VERSION_RE.match(v or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def supports_published(version: Optional[tuple], raw_keys: Iterable[dict]) -> Optional[bool]:
    """``published`` gibt es ab PowerDNS 4.3. Ohne Version: aus den Schluesseln ableiten (leer -> ``None``)."""
    if version is not None:
        return tuple(version) >= (4, 3, 0)
    keys = [k for k in (raw_keys or []) if isinstance(k, dict)]
    if not keys:
        return None
    return any("published" in k for k in keys)


# ---------------------------------------------------------------------------------------------
# Schluessel-Sicht
# ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class KeyView:
    id: int
    keytype: str
    flags: Optional[int]
    active: bool
    published: Optional[bool]
    algorithm_number: Optional[int]

    @property
    def is_sep(self) -> bool:
        if self.flags is not None:
            return self.flags == SEP_FLAG
        return self.keytype in ("ksk", "csk")

    @property
    def published_eff(self) -> bool:
        return self.published is not False


def key_view_from_pdns(raw: dict) -> KeyView:
    """KeyView aus einem PowerDNS-Cryptokey (Flags/Algorithmus aus dem DNSKEY, Fallback ``algorithm``)."""
    parsed = parse_dnskey_rdata(raw.get("dnskey")) or {}
    flags = parsed.get("flags") if not parsed.get("error") else None
    alg = parsed.get("algorithm") if not parsed.get("error") else None
    if alg is None:
        alg = algorithm_number(raw.get("algorithm"))
    published = raw.get("published") if "published" in raw else None
    return KeyView(
        id=int(raw.get("id") or 0),
        keytype=str(raw.get("keytype") or "").lower(),
        flags=flags,
        active=bool(raw.get("active")),
        published=None if published is None else bool(published),
        algorithm_number=alg,
    )


# ---------------------------------------------------------------------------------------------
# Schutzregeln
# ---------------------------------------------------------------------------------------------
PROTECTION_CODES = ("last_active_key", "last_active_sep", "last_published_sep")
PROTECTION_MESSAGES = {
    "last_active_key": (
        "Schlüssel {key_id} ist der letzte aktive Schlüssel der Zone. Ohne aktiven Schlüssel ist die Zone nicht "
        "mehr signiert – solange beim Registrar ein DS steht, liefern validierende Resolver SERVFAIL. Zum "
        "Abschalten „DNSSEC deaktivieren“ verwenden (DS vorher beim Registrar entfernen) oder mit force=true "
        "erzwingen."
    ),
    "last_active_sep": (
        "Schlüssel {key_id} ist der letzte aktive KSK/CSK. Der DS beim Registrar zeigt auf einen solchen "
        "Schlüssel – ohne ihn schlägt die Validierung fehl. Erst einen neuen KSK/CSK aktivieren (Rollover) "
        "oder mit force=true erzwingen."
    ),
    "last_published_sep": (
        "Schlüssel {key_id} ist der letzte veröffentlichte aktive KSK/CSK. Ohne ihn im DNSKEY-RRset schlägt "
        "die Validierung fehl. Erst einen Ersatz veröffentlichen oder mit force=true erzwingen."
    ),
}


def check_key_change(keys: list[KeyView], key_id: int, *, active: Optional[bool] = None,
                     published: Optional[bool] = None, delete: bool = False) -> list[str]:
    """Simuliert den Zielzustand und liefert die verletzten Schutzregeln (Reihenfolge = PROTECTION_CODES).

    Deaktivieren/Loeschen des letzten ZSK ist bewusst KEINE Verletzung – PowerDNS signiert dann mit dem
    SEP-Schluessel (CSK-Verhalten).
    """
    before = list(keys)
    after = [k for k in before if not (delete and k.id == key_id)]
    after = [
        replace(
            k,
            active=(active if active is not None else k.active),
            published=(published if published is not None else k.published),
        ) if k.id == key_id else k
        for k in after
    ]

    def any_active(ks):
        return any(k.active for k in ks)

    def any_active_sep(ks):
        return any(k.active and k.is_sep for k in ks)

    def any_pub_active_sep(ks):
        return any(k.active and k.is_sep and k.published_eff for k in ks)

    violations = []
    if any_active(before) and not any_active(after):
        violations.append("last_active_key")
    if any_active_sep(before) and not any_active_sep(after):
        violations.append("last_active_sep")
    if any_pub_active_sep(before) and not any_pub_active_sep(after):
        violations.append("last_published_sep")
    return violations


# ---------------------------------------------------------------------------------------------
# Rollover und DS-Status
# ---------------------------------------------------------------------------------------------
def _track(phase: str, *, old: Optional[int] = None, new: Optional[int] = None,
           next_: Optional[str] = None) -> dict:
    return {"phase": phase, "old_key_id": old, "new_key_id": new, "next_action": next_}


def derive_track(keys: list[KeyView]) -> Optional[dict]:
    """Phase fuer eine Rolle (alle Schluessel gleicher Rolle); leere Liste -> ``None``."""
    ks = sorted(keys, key=lambda k: k.id)
    if not ks:
        return None
    act = [k for k in ks if k.active]
    ina = [k for k in ks if not k.active]
    if any(k.active and not k.published_eff for k in ks):
        return _track("complex", next_="manual")
    if len(act) == 1 and not ina:
        return _track("idle", old=act[0].id, next_="start")
    if len(act) == 1 and len(ina) == 1:
        if ina[0].id > act[0].id and ina[0].published_eff:
            return _track("new_prepublished", old=act[0].id, new=ina[0].id, next_="activate_new")
        if ina[0].id < act[0].id:
            return _track("old_retired", old=ina[0].id, new=act[0].id, next_="delete_old")
        return _track("complex", next_="manual")
    if len(act) == 2 and not ina:
        return _track("both_active", old=act[0].id, new=act[1].id, next_="deactivate_old")
    if not act:
        return _track("no_active")
    return _track("complex", next_="manual")


def derive_rollover(keys: list[KeyView]) -> dict:
    """``{"algorithm_rollover", "sep", "zsk"}``; ein Algorithmuswechsel sperrt den Assistenten (``complex``)."""
    algos = {k.algorithm_number for k in keys if k.algorithm_number is not None}
    algorithm_rollover = len(algos) > 1 or any(k.active and not k.published_eff for k in keys)
    sep = derive_track([k for k in keys if k.is_sep])
    zsk = derive_track([k for k in keys if not k.is_sep])
    if algorithm_rollover:
        if sep is not None:
            sep = {**sep, "phase": "complex", "next_action": "manual"}
        if zsk is not None:
            zsk = {**zsk, "phase": "complex", "next_action": "manual"}
    return {"algorithm_rollover": algorithm_rollover, "sep": sep, "zsk": zsk}


ROLLOVER_ACTIVE_PHASES = ("new_prepublished", "both_active", "old_retired")


def rollover_roles(rollover: dict) -> dict[int, str]:
    """``{old_key_id: "old", new_key_id: "new"}`` aus laufenden Rollover-Spuren (ohne idle)."""
    roles: dict[int, str] = {}
    for name in ("sep", "zsk"):
        track = (rollover or {}).get(name)
        if not track or track.get("phase") not in ROLLOVER_ACTIVE_PHASES:
            continue
        if track.get("old_key_id") is not None:
            roles[int(track["old_key_id"])] = "old"
        if track.get("new_key_id") is not None:
            roles[int(track["new_key_id"])] = "new"
    return roles


def ds_status_map(keys: list[KeyView]) -> dict[int, str]:
    """DS-Status je Schluessel (Spec 3.2): current/new/retired/inactive/unpublished_active, sonst not_sep."""
    active_sep_ids = [k.id for k in keys if k.is_sep and k.active]
    max_active = max(active_sep_ids) if active_sep_ids else None
    out: dict[int, str] = {}
    for k in keys:
        if not k.is_sep:
            out[k.id] = "not_sep"
        elif k.active and k.published_eff:
            out[k.id] = "current"
        elif k.active:
            out[k.id] = "unpublished_active"
        elif k.published_eff and max_active is not None and k.id > max_active:
            out[k.id] = "new"
        elif max_active is not None and k.id < max_active:
            out[k.id] = "retired"
        else:
            out[k.id] = "inactive"
    return out


def has_sha1_ds(raw_keys: Iterable[dict]) -> bool:
    """Liefert ein SEP-Schluessel einen DS mit Digest-Typ 1 (SHA-1)?"""
    for raw in raw_keys or []:
        for line in (raw or {}).get("ds") or []:
            if isinstance(line, str) and parse_ds_line(line).get("digest_type") == 1:
                return True
    return False


# ---------------------------------------------------------------------------------------------
# Hinweise
# ---------------------------------------------------------------------------------------------
_LEVEL_ORDER = {"danger": 0, "warning": 1, "info": 2}


def _servers(peers: Optional[list[dict]], states: tuple[str, ...]) -> list[str]:
    return sorted({str(p.get("server")) for p in (peers or []) if p.get("state") in states})


def build_hints(*, keys: list[KeyView], meta: dict, nsec: dict, supports_published: Optional[bool],
                version: Optional[str], server: str, server_writable: bool, peers: Optional[list[dict]],
                rollover: dict, raw_keys: Optional[list[dict]] = None) -> list[dict]:
    """Hinweis-Codes fuer den Status (Tabelle Spec 3.2), sortiert danger -> warning -> info.

    ``params`` enthalten nur einfache Strings/Zahlen; Serverlisten als ``", ".join(sorted(...))``.
    """
    meta = meta or {}
    hints: list[dict] = []

    def add(code: str, level: str, **params):
        hints.append({"code": code, "level": level, "params": params})

    kind = str(meta.get("kind") or "")
    signed = any(k.active for k in keys)
    active_sep = [k for k in keys if k.active and k.is_sep]

    if meta.get("presigned"):
        add("presigned_zone", "info")
    if keys and not signed:
        add("keys_inactive_only", "warning")
    if signed and not active_sep:
        add("no_active_sep", "danger")
    if len(active_sep) >= 2:
        add("multiple_active_sep", "info")
    for track_name in ("sep", "zsk"):
        track = (rollover or {}).get(track_name)
        if track and track.get("phase") in ROLLOVER_ACTIVE_PHASES:
            add("rollover_in_progress", "info", track=track_name, phase=track["phase"])
    if (rollover or {}).get("algorithm_rollover"):
        add("algorithm_rollover", "warning")
    if any(k.active and k.published is False for k in keys):
        add("active_unpublished", "info")
    deprecated = sorted({k.algorithm_number for k in keys
                         if k.active and k.algorithm_number in DEPRECATED_ALGORITHM_NUMBERS})
    if deprecated:
        add("deprecated_algorithm", "danger", algorithm=", ".join(algorithm_label(n) for n in deprecated))
    if (nsec or {}).get("mode") == "nsec3":
        it = nsec.get("iterations")
        if isinstance(it, int) and it > NSEC3_WARN_ITERATIONS:
            add("nsec3_iterations_high", "danger", iterations=it)
        elif isinstance(it, int) and it >= 1:
            add("nsec3_iterations_nonzero", "warning", iterations=it)
        if nsec.get("salt") not in (None, "-"):
            add("nsec3_salt", "info")
        if nsec.get("opt_out"):
            add("nsec3_optout", "info")
    if supports_published is False:
        add("published_unsupported", "info", version=version or "")
    if not version:
        add("version_unknown", "info")
    if signed and meta.get("api_rectify") is False:
        add("api_rectify_off", "warning")
    divergent = _servers(peers, ("different_keys",))
    if divergent:
        add("peers_divergent", "danger", servers=", ".join(divergent))
    unsigned = _servers(peers, ("unsigned",))
    if signed and unsigned:
        add("peers_unsigned", "danger", servers=", ".join(unsigned))
    unreachable = _servers(peers, ("unreachable", "error"))
    if unreachable:
        add("peers_unreachable", "warning", servers=", ".join(unreachable))
    if not server_writable:
        add("server_read_only", "info", server=server)
    if signed and kind.lower() in PRIMARY_KINDS:
        add("secondaries_serial", "info")
    if kind.lower() in ("slave", "secondary") and keys:
        add("secondary_zone_signing", "warning")
    if raw_keys is not None and has_sha1_ds(raw_keys):
        add("sha1_ds", "info")
    return sorted(hints, key=lambda h: _LEVEL_ORDER[h["level"]])  # stabil: Tabellenreihenfolge je Stufe


# ---------------------------------------------------------------------------------------------
# Serial-Erhoehung nach Schluesselaenderungen [D10]
# ---------------------------------------------------------------------------------------------
def is_primary_kind(kind: Optional[str]) -> bool:
    """Master/Primary/Producer: Zonen, die Secondaries per AXFR/NOTIFY versorgen."""
    return str(kind or "").strip().lower() in PRIMARY_KINDS


def bump_soa_content(content: str) -> tuple[str, int, int]:
    """SOA-Inhalt mit Serial + 1 (Serien-Arithmetik RFC 1982, 0 wird uebersprungen).

    Rueckgabe ``(neuer_inhalt, alter_serial, neuer_serial)``; ``ValueError`` bei unlesbarem SOA.
    """
    parts = (content or "").split()
    if len(parts) != 7 or not parts[2].isdigit():
        raise ValueError(f"SOA-Inhalt nicht lesbar: {content!r}")
    old = int(parts[2]) % SERIAL_MODULUS
    new = (old + 1) % SERIAL_MODULUS or 1
    parts[2] = str(new)
    return " ".join(parts), old, new


def soa_serial(content: Optional[str]) -> Optional[int]:
    parts = (content or "").split()
    if len(parts) == 7 and parts[2].isdigit():
        return int(parts[2])
    return None
