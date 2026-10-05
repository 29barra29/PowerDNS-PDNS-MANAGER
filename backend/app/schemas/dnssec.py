"""Pydantic-Schemas der DNSSEC-Endpunkte (F4 5.6).

Eingaben: ``NsecOptions`` (gemeinsame NSEC/NSEC3-Felder), ``DNSSECEnable`` (auch ``ZoneCreate.dnssec_options``),
``DNSSECEnableRequest`` (+ ``bump_serial``), ``DNSSECDisable``, ``CryptoKeyCreate``, ``CryptoKeyUpdate``,
``Nsec3Update``. Fehlertexte deutsch (eigene Validatoren statt ``Field(ge/le)`` mit englischen Meldungen).
``bump_serial`` (Plan F4-A [D10]): ``None`` = bei Zonen vom Typ Master/Producer Serial erhoehen + NOTIFY,
``False`` = nicht, sonst ignoriert.

Antworten: ``DnssecStatusResponse`` mit ``DnssecKeyInfo`` u. a. – nie ``privatekey``.
``schemas/dns.py`` re-exportiert ``DNSSECEnable`` und ``CryptoKeyResponse`` (Kompatibilitaet).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services import dnssec_logic as logic

BUMP_SERIAL_DESCRIPTION = (
    "Serial erhoehen und NOTIFY senden (nur Zonen vom Typ Master/Producer; None = ja, False = nein)"
)


def _lower_choice(value, allowed: tuple[str, ...], message: str):
    if value is None:
        return value
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        raise ValueError(message)
    return value.strip().lower()


# =============================================================================================
# Eingaben
# =============================================================================================
class NsecOptions(BaseModel):
    """NSEC/NSEC3-Einstellungen (Standard NSEC3 ``1 0 0 -`` nach RFC 9276)."""

    model_config = ConfigDict(extra="ignore")

    nsec_mode: Literal["nsec", "nsec3"] = "nsec3"
    nsec3_iterations: int = 0
    nsec3_salt: str = "-"
    nsec3_optout: bool = False
    nsec3narrow: bool = False

    @field_validator("nsec_mode", mode="before")
    @classmethod
    def _mode(cls, v):
        return _lower_choice(v, ("nsec", "nsec3"), "nsec_mode muss nsec oder nsec3 sein.")

    @field_validator("nsec3_iterations", mode="before")
    @classmethod
    def _iterations(cls, v):
        return logic.validate_iterations(v)

    @field_validator("nsec3_salt", mode="before")
    @classmethod
    def _salt(cls, v):
        if v is not None and not isinstance(v, str):
            raise ValueError(logic.SALT_ERROR)
        return logic.normalize_salt(v)

    def nsec3_params(self) -> Optional[logic.Nsec3Params]:
        if self.nsec_mode != "nsec3":
            return None
        return logic.Nsec3Params(optout=self.nsec3_optout, iterations=self.nsec3_iterations, salt=self.nsec3_salt)

    def effective_nsec3param(self) -> str:
        """"" bei NSEC, sonst ``"1 <flags> <iterationen> <salt>"``."""
        params = self.nsec3_params()
        return params.to_param() if params else ""

    def effective_narrow(self) -> bool:
        return bool(self.nsec3narrow) if self.nsec_mode == "nsec3" else False


class DNSSECEnable(NsecOptions):
    """Optionen fuer ``POST …/enable`` und ``ZoneCreate.dnssec_options`` (leerer Body = Standardwerte).

    ``nsec3param`` ist das 2.x-Feld: gesetzt, wird es geparst und ueberschreibt ``nsec_mode``/``nsec3_*``
    (``""`` = NSEC). Unbekannte Felder werden ignoriert (Kompatibilitaet).
    """

    key_model: Literal["csk", "ksk_zsk"] = "csk"
    algorithm: str = "ECDSAP256SHA256"
    bits: Optional[int] = None
    nsec3param: Optional[str] = Field(default=None, description="2.x-Format 'Alg Flags Iterationen Salt'")

    @field_validator("key_model", mode="before")
    @classmethod
    def _key_model(cls, v):
        return _lower_choice(v, ("csk", "ksk_zsk"), "key_model muss csk oder ksk_zsk sein.")

    @field_validator("algorithm", mode="before")
    @classmethod
    def _algorithm(cls, v):
        return logic.normalize_algorithm(v)

    @model_validator(mode="after")
    def _legacy_and_bits(self):
        if self.nsec3param is not None:
            params = logic.parse_nsec3param(self.nsec3param)
            if params is None:
                self.nsec_mode = "nsec"
            else:
                self.nsec_mode = "nsec3"
                self.nsec3_optout = params.optout
                self.nsec3_iterations = params.iterations
                self.nsec3_salt = params.salt
            self.nsec3param = self.effective_nsec3param()
        self.bits = logic.validate_bits(self.algorithm, self.bits)
        return self


class DNSSECEnableRequest(DNSSECEnable):
    """Body von ``POST …/enable``: Optionen plus ``bump_serial``."""

    bump_serial: Optional[bool] = Field(default=None, description=BUMP_SERIAL_DESCRIPTION)


class DNSSECDisable(BaseModel):
    """Body von ``POST …/disable`` (optional). ``force`` ist erst mit der Parent-DS-Pruefung (Teil B) relevant."""

    model_config = ConfigDict(extra="ignore")

    force: bool = False
    bump_serial: Optional[bool] = Field(default=None, description=BUMP_SERIAL_DESCRIPTION)


class CryptoKeyCreate(BaseModel):
    """Body von ``POST …/keys``. ``extra="forbid"``: ``privatekey`` u. Ae. ergeben 422 (kein Schluesselimport)."""

    model_config = ConfigDict(extra="forbid")

    keytype: Literal["csk", "ksk", "zsk"]
    algorithm: str = "ECDSAP256SHA256"
    bits: Optional[int] = None
    active: bool = False
    published: bool = True
    bump_serial: Optional[bool] = Field(default=None, description=BUMP_SERIAL_DESCRIPTION)

    @field_validator("keytype", mode="before")
    @classmethod
    def _keytype(cls, v):
        return _lower_choice(v, ("csk", "ksk", "zsk"), "keytype muss csk, ksk oder zsk sein.")

    @field_validator("algorithm", mode="before")
    @classmethod
    def _algorithm(cls, v):
        return logic.normalize_algorithm(v)

    @model_validator(mode="after")
    def _bits(self):
        self.bits = logic.validate_bits(self.algorithm, self.bits)
        return self


class CryptoKeyUpdate(BaseModel):
    """Body von ``PUT …/keys/{id}``: ``active`` und/oder ``published``; ``force`` uebersteuert Schutzregeln."""

    model_config = ConfigDict(extra="forbid")

    active: Optional[bool] = None
    published: Optional[bool] = None
    force: bool = False
    bump_serial: Optional[bool] = Field(default=None, description=BUMP_SERIAL_DESCRIPTION)

    @model_validator(mode="after")
    def _something(self):
        if self.active is None and self.published is None:
            raise ValueError("Bitte active und/oder published angeben.")
        return self


class Nsec3Update(NsecOptions):
    """Body von ``PUT …/nsec3``."""

    model_config = ConfigDict(extra="forbid")

    bump_serial: Optional[bool] = Field(default=None, description=BUMP_SERIAL_DESCRIPTION)


class CryptoKeyResponse(BaseModel):
    """PowerDNS-Cryptokey (ohne ``privatekey``). Aus ``schemas/dns.py`` hierher verschoben (Re-Export dort)."""

    id: int
    type: Optional[str] = None
    keytype: Optional[str] = None
    active: bool = False
    published: Optional[bool] = None
    dnskey: Optional[str] = None
    ds: Optional[list[str]] = None
    cds: Optional[list[str]] = None
    algorithm: Optional[str] = None
    bits: Optional[int] = None


# =============================================================================================
# Antworten (GET …/status)
# =============================================================================================
class DsLine(BaseModel):
    ds: str
    digest_type: Optional[int] = None
    parsed: dict = Field(default_factory=dict)


class DnssecKeyInfo(BaseModel):
    id: int
    keytype: str
    role: Literal["sep", "zsk"]
    flags: Optional[int] = None
    active: bool
    published: Optional[bool] = None
    published_effective: bool
    algorithm: Optional[str] = None
    algorithm_number: Optional[int] = None
    bits: Optional[int] = None
    key_tag: Optional[int] = None
    dnskey: Optional[str] = None
    ds: list[DsLine] = Field(default_factory=list)
    cds: Optional[list[str]] = None
    ds_status: Literal["current", "new", "retired", "inactive", "unpublished_active", "not_sep"]
    rollover_role: Optional[Literal["old", "new"]] = None


class DnssecNsecInfo(BaseModel):
    mode: Optional[Literal["nsec", "nsec3"]] = None
    nsec3param: Optional[str] = None
    iterations: Optional[int] = None
    salt: Optional[str] = None
    opt_out: Optional[bool] = None
    narrow: bool = False


class RolloverTrack(BaseModel):
    phase: Literal["idle", "new_prepublished", "both_active", "old_retired", "no_active", "complex"]
    old_key_id: Optional[int] = None
    new_key_id: Optional[int] = None
    next_action: Optional[Literal["start", "activate_new", "deactivate_old", "delete_old", "manual"]] = None


class DnssecRollover(BaseModel):
    algorithm_rollover: bool = False
    sep: Optional[RolloverTrack] = None
    zsk: Optional[RolloverTrack] = None


class PeerKeyState(BaseModel):
    server: str
    allow_writes: bool = True
    state: Literal["same_keys", "different_keys", "unsigned", "both_unsigned", "secondary", "zone_missing",
                   "unreachable", "error"]
    key_tags: list[int] = Field(default_factory=list)
    zone_kind: Optional[str] = None


class DnssecHint(BaseModel):
    code: str
    level: Literal["info", "warning", "danger"]
    params: dict = Field(default_factory=dict)


class AlgorithmInfo(BaseModel):
    name: str
    number: int
    rsa: bool
    recommended: bool
    build_dependent: bool


class DnssecCapabilities(BaseModel):
    supports_published: Optional[bool] = None
    algorithms: list[AlgorithmInfo] = Field(default_factory=list)
    rsa_bits: list[int] = Field(default_factory=lambda: list(logic.RSA_BITS_ALLOWED))
    nsec3_max_iterations: int = logic.NSEC3_MAX_ITERATIONS
    max_keys: int = logic.MAX_KEYS_PER_ZONE
    parent_ds_check: bool = False


class DnssecStatusResponse(BaseModel):
    zone: str
    server: str
    server_version: Optional[str] = None
    zone_kind: Optional[str] = None
    presigned: bool = False
    manageable: bool = True
    signed: bool = False
    dnssec_flag: bool = False
    api_rectify: Optional[bool] = None
    nsec: DnssecNsecInfo
    keys: list[DnssecKeyInfo] = Field(default_factory=list)
    rollover: DnssecRollover
    key_history: dict[str, dict[str, Optional[str]]] = Field(default_factory=dict)
    peers: Optional[list[PeerKeyState]] = None
    hints: list[DnssecHint] = Field(default_factory=list)
    capabilities: DnssecCapabilities
    server_writable: bool = True
    user_can_write: bool = False
    can_write: bool = False
