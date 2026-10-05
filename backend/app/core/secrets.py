"""Verschluesselung gespeicherter Geheimnisse (F5, Bauplan B.2).

Alle umkehrbar gespeicherten Geheimnisse (PowerDNS-API-Keys, Webhook-Secrets und -URLs,
TOTP-Geheimnisse, Secret-Settings wie SMTP-Passwort) liegen ab 3.0 nur noch als
``enc:v1:<Fernet-Token>`` in der Datenbank.

Aufrufer importieren das Modul IMMER mit Alias, damit das Stdlib-Modul ``secrets`` nicht
verdeckt wird::

    from app.core import secrets as secret_store

Grundregeln:
- Lesen wirft nie: nicht entschluesselbare Werte werden zum Platzhalter ``UNREADABLE``
  (verhaelt sich wie ``""``, ist per ``is_unreadable`` erkennbar) und werden von SQLAlchemy
  nicht zurueckgeschrieben – der Chiffretext bleibt fuer eine spaetere Wiederherstellung erhalten.
- Schreiben verschluesselt immer (auch Eingaben, die zufaellig mit ``enc:v1:`` beginnen);
  ohne Schluessel wird ``SecretsUnavailableError`` geworfen (Ausnahme: Fallback-Modus, siehe
  ``init_secrets_sync``).
- Nie Klartext, Chiffretext oder Schluessel loggen. Erlaubt: Feld-ID, Zeilen-ID, Namen,
  Anzahlen, Fingerprint (12 Hex), Dateipfad.
- Auf verschluesselten Spalten nie in WHERE/ORDER BY filtern (Fernet ist nicht deterministisch).

Dieses Modul importiert nur ``app.core.config``, ``cryptography`` und ``sqlalchemy`` (keine
Models -> keine Importzyklen; die Models importieren die TypeDecorators von hier).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Optional, Sequence

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import String, Text, bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

if TYPE_CHECKING:  # nur fuer Typangaben, keine Laufzeitabhaengigkeit
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

PREFIX: Final = "enc:v1:"

# Reihenfolge = Anzeige-Reihenfolge im Status (Bauplan A.7).
_SETTING_KEY_ORDER: Final[tuple[str, ...]] = (
    "smtp_password",
    "captcha_secret_key",
    "oidc_client_secret",
    "ldap_bind_password",
    "metrics_token",
)
SECRET_SETTING_KEYS: Final[frozenset[str]] = frozenset(_SETTING_KEY_ORDER)

# Dedizierte Secret-Spalten (Tabelle, Spalte). webhooks.url ist Teil der Liste [S10]:
# bei Slack/Teams/Discord ist die Ziel-URL selbst das Geheimnis.
SECRET_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("server_configs", "api_key"),
    ("webhooks", "secret"),
    ("webhooks", "url"),
    ("users", "totp_secret"),
    ("users", "totp_pending_secret"),
)

# /app/data/.secret_key im Container (analog zur JWT-Datei in config.py, NICHT unter /uploads).
DEFAULT_KEY_FILE: Final[Path] = Path(__file__).resolve().parents[2] / "data" / ".secret_key"
DOCS_URL: Final = "https://pdns-manager.gemtecgames.com/docs/features/verschluesselung/"

LOG_THROTTLE_SECONDS: Final = 600.0
_FINGERPRINT_SALT: Final = b"pdns-manager:key-fingerprint:"
_KEYGEN_HINT: Final = "Erzeugen: openssl rand -base64 32 | tr '+/' '-_'"
_CHOWN_HINT: Final = (
    "docker compose run --rm --no-deps --name pdnsmgr-chown -u root backend chown -R 1001:1001 /app/data"
)
_FALLBACK_FIX_HINT: Final = (
    "docker compose exec -u root backend chown -R 1001:1001 /app/data && docker compose restart backend"
)
_RESET_HINT_LINES: Final[tuple[str, ...]] = (
    "Loesung 2 (kein Schluessel-Backup vorhanden – unlesbare Werte zuruecksetzen):",
    "  docker compose stop backend",
    "  docker compose run --rm --name pdnsmgr-secrets-cli backend python -m app.cli.secrets reset-unreadable",
    "  (Trockenlauf; danach dasselbe mit --yes, anschliessend docker compose up -d)",
)

ColumnClass = Literal["empty", "plaintext", "encrypted", "encrypted_old", "unreadable"]


def _column_id(table: str, column: str) -> str:
    return f"{table}.{column}"


def _setting_id(key: str) -> str:
    return f"system_settings.{key}"


# Raw-SQL je Secret-Spalte (Tabellen-/Spaltennamen ausschliesslich aus Konstanten).
# Spalten: id, Rohwert, Name, Besitzer. Backticks funktionieren in MariaDB und SQLite.
_SCAN_SQL: Final[dict[str, tuple[str, str]]] = {
    "server_configs.api_key": (
        "server",
        "SELECT id, api_key, name, NULL FROM server_configs ORDER BY id",
    ),
    "webhooks.secret": (
        "webhook",
        "SELECT w.id, w.secret, w.name, u.username FROM webhooks w "
        "LEFT JOIN users u ON u.id = w.user_id ORDER BY w.id",
    ),
    "webhooks.url": (
        "webhook",
        "SELECT w.id, w.url, w.name, u.username FROM webhooks w "
        "LEFT JOIN users u ON u.id = w.user_id ORDER BY w.id",
    ),
    "users.totp_secret": (
        "user_totp",
        "SELECT id, totp_secret, username, NULL FROM users ORDER BY id",
    ),
    "users.totp_pending_secret": (
        "user_totp",
        "SELECT id, totp_pending_secret, username, NULL FROM users ORDER BY id",
    ),
}
_SCAN_SETTINGS_SQL: Final = "SELECT id, `key`, `value` FROM system_settings WHERE `key` IN :keys ORDER BY id"
_UPDATE_SQL: Final[dict[str, str]] = {
    _column_id(t, c): f"UPDATE {t} SET {c} = :v WHERE id = :id" for t, c in SECRET_COLUMNS
}
_UPDATE_SETTING_SQL: Final = "UPDATE system_settings SET `value` = :v WHERE id = :id"

if set(_SCAN_SQL) != {_column_id(t, c) for t, c in SECRET_COLUMNS}:  # Konsistenz der Tabellen oben
    raise RuntimeError("_SCAN_SQL und SECRET_COLUMNS passen nicht zusammen")

# ---------------------------------------------------------------------------
# Fehler und Platzhalter
# ---------------------------------------------------------------------------


class SecretsStartupError(RuntimeError):
    """Startabbruch mit klarer Log-Meldung (nie Schluesselwerte im Text).

    Codes: KEY_INVALID | KEY_CONFIG | KEY_FILE_MISSING | KEY_FILE_UNREADABLE | KEY_FILE_INVALID
    | KEY_MISSING | KEY_MISMATCH | SCHEMA_TOO_NARROW | MIGRATION_FAILED
    """

    def __init__(self, code: str, title: str, lines: list[str]):
        self.code = code
        self.title = title
        self.lines = list(lines)
        super().__init__(f"{title} ({code})")

    def log_lines(self) -> list[str]:
        return [
            f"==== PDNS Manager: Start abgebrochen – {self.title} ({self.code}) ====",
            *self.lines,
            f"Doku: {DOCS_URL}",
        ]


class SecretsUnavailableError(RuntimeError):
    """Schreibzugriff auf ein Geheimnis ohne verfuegbaren Schluessel.

    Bewusst KEIN ValueError: der globale ValueError-Handler wuerde den Rohtext als 400 liefern.
    main.py bildet die Ausnahme auf 503 ab (F5 §3.3).
    """


class UnreadableSecret(str):
    """Platzhalter fuer nicht entschluesselbare Werte. Verhaelt sich wie "" (falsy, == "").

    Achtung: String-Operationen (strip, lower, ...) liefern ein normales "" – deshalb
    ``is_unreadable`` immer auf dem Rohattribut pruefen.
    """

    __slots__ = ()

    def __new__(cls, _value: str = "") -> "UnreadableSecret":
        return super().__new__(cls, "")

    def __repr__(self) -> str:
        return "<UnreadableSecret>"


UNREADABLE: Final = UnreadableSecret("")


def is_unreadable(value: object) -> bool:
    return isinstance(value, UnreadableSecret)


def is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


# ---------------------------------------------------------------------------
# Schluessel
# ---------------------------------------------------------------------------


def generate_key() -> str:
    """Neuer Fernet-Schluessel (44 Zeichen urlsafe-Base64)."""
    return Fernet.generate_key().decode("ascii")


def _decode_key_material(raw: str | bytes) -> bytes:
    """32 Byte Schluesselmaterial; akzeptiert urlsafe- und Standard-Base64, sonst ValueError."""
    if isinstance(raw, bytes):
        raw = raw.decode("ascii")
    s = raw.strip()
    if not s:
        raise ValueError("leer")
    # Strikt dekodieren: b64decode ohne validate verwirft fremde Zeichen still.
    decoded = base64.b64decode(s.replace("-", "+").replace("_", "/"), validate=True)
    if len(decoded) != 32:
        raise ValueError("falsche Laenge")
    return decoded


def _try_parse_key(raw: str | bytes | None) -> Optional[bytes]:
    """Kanonischer Fernet-Schluessel (urlsafe-Base64, bytes) oder None. Wirft nie."""
    if raw is None:
        return None
    try:
        canon = base64.urlsafe_b64encode(_decode_key_material(raw))
        Fernet(canon)
        return canon
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        return None


def key_fingerprint(key: str | bytes) -> str:
    """12 Hex-Zeichen, deterministisch, kein Geheimnis (gesalzener SHA-256 des Schluesselmaterials)."""
    material = _decode_key_material(key)
    return hashlib.sha256(_FINGERPRINT_SALT + material).hexdigest()[:12]


def parse_key(raw: str, *, source_name: str) -> bytes:
    """Prueft einen konfigurierten Schluessel; Fehler -> SecretsStartupError("KEY_INVALID").

    Die Meldung nennt nur die Quelle und die Laenge, nie den Wert.
    """
    value = (raw or "").strip()
    canon = _try_parse_key(value)
    if canon is None:
        raise SecretsStartupError(
            "KEY_INVALID",
            "Schluessel ungueltig",
            [
                f"{source_name} ist kein gueltiger Fernet-Schluessel (erwartet: 44 Zeichen "
                f"urlsafe-Base64, gesetzt: {len(value)} Zeichen). {_KEYGEN_HINT}"
            ],
        )
    return canon


class SecretBox:
    """Ver-/Entschluesselung mit Primaerschluessel und optionalen Zusatzschluesseln (Rotation)."""

    def __init__(self, primary: bytes, decrypt_only: Sequence[bytes] = ()) -> None:
        keys: list[bytes] = []
        for k in (primary, *decrypt_only):
            canon = _try_parse_key(k)
            if canon is None:
                raise ValueError("Ungueltiger Schluessel fuer SecretBox")
            if canon not in keys:
                keys.append(canon)
        self._keys = keys
        self._fernets = [Fernet(k) for k in keys]
        self._multi = MultiFernet(self._fernets)
        self.fingerprint: str = key_fingerprint(keys[0])
        self.decrypt_only_count: int = len(keys) - 1
        self.decrypt_only_fingerprints: tuple[str, ...] = tuple(key_fingerprint(k) for k in keys[1:])

    @staticmethod
    def _token(stored: str) -> bytes:
        return stored[len(PREFIX):].encode("ascii")

    def encrypt(self, plaintext: str) -> str:
        """PREFIX + Fernet-Token. Leer bleibt leer."""
        if plaintext == "":
            return ""
        return PREFIX + self._multi.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, stored: str) -> str:
        """Ohne PREFIX unveraendert (Legacy-Klartext); sonst entschluesseln (wirft bei Fehlern)."""
        if not stored.startswith(PREFIX):
            return stored
        return self._multi.decrypt(self._token(stored)).decode("utf-8")

    def classify(self, stored: str | None) -> ColumnClass:
        if stored is None or stored == "":
            return "empty"
        if not stored.startswith(PREFIX):
            return "plaintext"
        try:
            token = self._token(stored)
        except UnicodeError:
            return "unreadable"
        for idx, f in enumerate(self._fernets):
            try:
                f.decrypt(token).decode("utf-8")
            except (InvalidToken, ValueError, TypeError):
                continue
            return "encrypted" if idx == 0 else "encrypted_old"
        return "unreadable"

    def rotate(self, stored: str) -> str:
        """Neu verschluesseln mit dem Primaerschluessel (Token-Zeitstempel bleibt)."""
        return PREFIX + self._multi.rotate(self._token(stored)).decode("ascii")


@dataclass(frozen=True)
class KeyConfig:
    env_primary: str
    env_previous: tuple[str, ...]
    file_path: Path
    file_explicit: bool

    @classmethod
    def from_settings(cls, s: Any = None) -> "KeyConfig":
        """Liest die Schluessel-Konfiguration.

        Die Felder SECRET_ENCRYPTION_KEY* kommen in config.py erst mit Welle 0b; bis dahin (bzw.
        wenn ein Settings-Objekt sie nicht kennt) wird direkt die Umgebung gelesen.
        """
        s = settings if s is None else s

        def _get(name: str) -> str:
            value = getattr(s, name, None)
            if value is None:
                value = os.environ.get(name, "")
            return str(value or "")

        primary = _get("SECRET_ENCRYPTION_KEY").strip()
        previous: list[str] = []
        for part in _get("SECRET_ENCRYPTION_KEY_PREVIOUS").split(","):
            p = part.strip()
            if p and p != primary and p not in previous:
                previous.append(p)
        file_raw = _get("SECRET_ENCRYPTION_KEY_FILE").strip()
        return cls(
            env_primary=primary,
            env_previous=tuple(previous),
            file_path=Path(file_raw) if file_raw else DEFAULT_KEY_FILE,
            file_explicit=bool(file_raw),
        )


@dataclass
class FileKeyState:
    status: Literal["missing", "ok", "unreadable", "invalid"]
    key: Optional[bytes]
    error: Optional[str]
    mode_ok: bool


def read_key_file(path: Path) -> FileKeyState:
    """Liest die Schluesseldatei; korrigiert zu offene Rechte auf 0600 (Ergebnis in mode_ok)."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return FileKeyState("missing", None, None, True)
    except OSError as exc:  # PermissionError, IsADirectoryError, ...
        return FileKeyState("unreadable", None, exc.strerror or type(exc).__name__, True)
    try:
        content = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return FileKeyState("invalid", None, "kein gueltiger Schluessel (Binaerdaten)", True)
    if not content:
        return FileKeyState("invalid", None, "Datei ist leer", True)
    key = _try_parse_key(content)
    if key is None:
        return FileKeyState("invalid", None, "kein gueltiger Fernet-Schluessel", True)
    mode_ok = True
    try:
        if os.stat(path).st_mode & 0o077:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            mode_ok = not (os.stat(path).st_mode & 0o077)
    except OSError:
        mode_ok = False
    return FileKeyState("ok", key, None, mode_ok)


def write_new_key_file(path: Path) -> bytes:
    """Erzeugt einen neuen Schluessel atomar (0600) und verifiziert ihn. Wirft OSError.

    Ueberschreibt nie eine vorhandene Datei (FileExistsError).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"Schluesseldatei existiert bereits: {path}")
    key = Fernet.generate_key()
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        # Reste eines abgebrochenen Laufs mit gleicher PID (Container: oft PID 1) entfernen.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            data = key + b"\n"
            while data:
                written = os.write(fd, data)
                data = data[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        check = read_key_file(path)
        if check.status != "ok" or check.key != _try_parse_key(key):
            raise OSError("Verifikation der neuen Schluesseldatei fehlgeschlagen")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    logger.warning(
        "Neuer Schluessel fuer gespeicherte Geheimnisse erzeugt: %s (Fingerprint %s). "
        "Bitte getrennt vom DB-Backup sichern!",
        path, key_fingerprint(key),
    )
    return key


# ---------------------------------------------------------------------------
# Scan und Startmigration (synchron, mit SQLite testbar)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredSecret:
    field: str              # Spalten-ID, z. B. "server_configs.api_key" oder "system_settings.smtp_password"
    kind: str               # "server" | "webhook" | "user_totp" | "setting"
    row_id: int
    raw: Optional[str]
    name: Optional[str]     # Server-/Webhook-Name, Username oder Setting-Key
    owner: Optional[str]    # nur Webhooks: Username des Besitzers


def scan_values(conn: "Connection") -> list[StoredSecret]:
    """Liest alle Secret-Felder per Raw-SQL (ohne TypeDecorator)."""
    out: list[StoredSecret] = []
    for table, column in SECRET_COLUMNS:
        fid = _column_id(table, column)
        kind, sql = _SCAN_SQL[fid]
        for row in conn.execute(text(sql)):
            out.append(StoredSecret(
                field=fid, kind=kind, row_id=int(row[0]), raw=row[1],
                name=row[2], owner=row[3],
            ))
    stmt = text(_SCAN_SETTINGS_SQL).bindparams(bindparam("keys", expanding=True))
    for row in conn.execute(stmt, {"keys": list(_SETTING_KEY_ORDER)}):
        out.append(StoredSecret(
            field=_setting_id(row[1]), kind="setting", row_id=int(row[0]), raw=row[2],
            name=row[1], owner=None,
        ))
    return out


def update_stored_value(conn: "Connection", item: StoredSecret, new_raw: Optional[str]) -> None:
    """Schreibt einen Rohwert (bereits ver-/entschluesselt) per Raw-SQL zurueck (auch fuer die CLI)."""
    if item.kind == "setting":
        sql = _UPDATE_SETTING_SQL
    else:
        sql = _UPDATE_SQL[item.field]
    conn.execute(text(sql), {"v": new_raw, "id": item.row_id})


@dataclass
class StartupReport:
    mode: Literal["encrypted", "plaintext_fallback"]
    fallback_reason: Optional[str]
    key_source: Optional[Literal["env", "file", "generated"]]
    key_file: str
    fingerprint: Optional[str]
    decrypt_only_keys: int
    key_generated: bool
    generated_reason: Optional[str]
    migrated: dict[str, int]
    rotated: dict[str, int]
    unreadable: list[StoredSecret]
    static_issues: list[str]
    at: datetime
    # Ergaenzungen (nicht in der Spec-Signatur, fuer Status/Logs):
    key_file_explicit: bool = False
    previous_keys: int = 0
    decrypt_only_fingerprints: tuple[str, ...] = ()


@dataclass(frozen=True)
class KeyDecision:
    """Ergebnis der Entscheidungsmatrix F5 §5.3 (ohne Zeile 8, die braucht die Werte)."""

    action: Literal["use", "generate", "replace_invalid", "fallback"]
    primary: Optional[bytes] = None
    decrypt_only: tuple[bytes, ...] = ()
    key_source: Optional[Literal["env", "file", "generated"]] = None
    fallback_reason: Optional[str] = None
    generated_reason: Optional[str] = None
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    previous_keys: int = 0


def _decide(cfg: KeyConfig, file_state: FileKeyState, real_encrypted: int) -> KeyDecision:
    """Entscheidungsmatrix F5 §5.3 (Reihenfolge = Prioritaet). Wirft SecretsStartupError."""
    path = cfg.file_path
    # 0: Env-Schluessel ungueltig
    env_primary = parse_key(cfg.env_primary, source_name="SECRET_ENCRYPTION_KEY") if cfg.env_primary else None
    previous: list[bytes] = []
    for idx, raw in enumerate(cfg.env_previous, start=1):
        k = parse_key(raw, source_name=f"SECRET_ENCRYPTION_KEY_PREVIOUS (Eintrag {idx})")
        if k != env_primary and k not in previous:
            previous.append(k)

    # 1: Env-Schluessel gesetzt
    if env_primary is not None:
        decrypt_only = list(previous)
        issues: list[str] = []
        warnings: list[str] = []
        if file_state.status == "ok" and file_state.key is not None:
            if file_state.key != env_primary:
                if file_state.key not in decrypt_only:
                    decrypt_only.append(file_state.key)
                issues.append("key_file_obsolete")
            if not file_state.mode_ok:
                issues.append("key_file_permissions")
        elif file_state.status in ("unreadable", "invalid"):
            warnings.append(
                f"Schluesseldatei {path} ist {'nicht lesbar' if file_state.status == 'unreadable' else 'ungueltig'} "
                f"({file_state.error}) – wird ignoriert, SECRET_ENCRYPTION_KEY ist gesetzt."
            )
        elif cfg.file_explicit:
            warnings.append(
                f"SECRET_ENCRYPTION_KEY_FILE={path} existiert nicht – wird ignoriert, SECRET_ENCRYPTION_KEY ist gesetzt."
            )
        return KeyDecision(
            "use", env_primary, tuple(decrypt_only), "env",
            issues=tuple(issues), warnings=tuple(warnings), previous_keys=len(previous),
        )

    # 2: Schluesseldatei ok
    if file_state.status == "ok" and file_state.key is not None:
        decrypt_only = tuple(k for k in previous if k != file_state.key)
        issues = () if file_state.mode_ok else ("key_file_permissions",)
        return KeyDecision(
            "use", file_state.key, decrypt_only, "file", issues=issues, previous_keys=len(decrypt_only),
        )

    # 3: nur Zusatzschluessel
    if previous:
        raise SecretsStartupError(
            "KEY_CONFIG",
            "Konfiguration unvollstaendig",
            [
                "SECRET_ENCRYPTION_KEY_PREVIOUS ist gesetzt, aber es gibt keinen aktiven Schluessel "
                f"(SECRET_ENCRYPTION_KEY leer, keine Schluesseldatei {path}). "
                "Den neuen Schluessel als SECRET_ENCRYPTION_KEY eintragen."
            ],
        )

    # 4: expliziter Pfad muss existieren und gueltig sein – es wird nie erzeugt oder auf
    #    Klartext ausgewichen (Docker-Secret-Fehlkonfiguration, F5 §12 Nr. 6).
    if cfg.file_explicit:
        if file_state.status == "missing":
            raise SecretsStartupError(
                "KEY_FILE_MISSING",
                "Schluesseldatei fehlt",
                [
                    f"SECRET_ENCRYPTION_KEY_FILE={path} ist gesetzt, die Datei existiert aber nicht. "
                    "Bei explizitem Pfad wird kein Schluessel automatisch erzeugt."
                ],
            )
        if file_state.status == "invalid":
            raise SecretsStartupError(
                "KEY_FILE_INVALID",
                "Schluesseldatei beschaedigt",
                [
                    f"SECRET_ENCRYPTION_KEY_FILE={path} enthaelt keinen gueltigen Schluessel ({file_state.error}). "
                    "Bei explizitem Pfad wird kein Schluessel automatisch erzeugt. "
                    "Gesicherten Schluessel wiederherstellen."
                ],
            )
        if file_state.status == "unreadable":
            raise _key_file_unreadable_error(path, file_state)

    # 5: Datei nicht lesbar (Default-Pfad)
    if file_state.status == "unreadable":
        if real_encrypted > 0:
            raise _key_file_unreadable_error(path, file_state)
        return KeyDecision("fallback", fallback_reason="key_file_unreadable")

    # 6: Datei ungueltig (Default-Pfad)
    if file_state.status == "invalid":
        if real_encrypted > 0:
            raise SecretsStartupError(
                "KEY_FILE_INVALID",
                "Schluesseldatei beschaedigt",
                [
                    f"{path} enthaelt keinen gueltigen Schluessel, in der Datenbank liegen aber {real_encrypted} "
                    "verschluesselte Werte. Gesicherten Schluessel wiederherstellen (als SECRET_ENCRYPTION_KEY in die .env).",
                    *_RESET_HINT_LINES,
                ],
            )
        return KeyDecision("replace_invalid", key_source="generated", generated_reason="previous_file_invalid")

    # 7: Datei fehlt (Default-Pfad)
    if real_encrypted > 0:
        raise SecretsStartupError(
            "KEY_MISSING",
            "Schluessel fuer gespeicherte Geheimnisse fehlt",
            [
                f"In der Datenbank liegen {real_encrypted} verschluesselte Werte (enc:v1:), aber weder "
                f"SECRET_ENCRYPTION_KEY ist gesetzt noch existiert {path}. Es wurde KEIN neuer Schluessel erzeugt.",
                "Loesung 1 (Schluessel-Backup vorhanden): Inhalt der gesicherten Schluesseldatei als "
                "SECRET_ENCRYPTION_KEY=... in die .env eintragen, dann docker compose up -d",
                *_RESET_HINT_LINES,
            ],
        )
    return KeyDecision("generate", key_source="generated", generated_reason="first_start")


def _key_file_unreadable_error(path: Path, file_state: FileKeyState) -> SecretsStartupError:
    return SecretsStartupError(
        "KEY_FILE_UNREADABLE",
        "Schluesseldatei nicht lesbar",
        [f"{path} kann nicht gelesen werden ({file_state.error}). Rechte korrigieren: {_CHOWN_HINT}"],
    )


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _move_invalid_key_file(path: Path) -> Path:
    target = path.with_name(f"{path.name}.invalid-{_utcnow_naive().strftime('%Y%m%d%H%M%S')}")
    os.replace(path, target)
    logger.warning("Ungueltige Schluesseldatei %s umbenannt nach %s.", path, target)
    return target


def _check_column_widths(conn: "Connection") -> None:
    """Nur MySQL/MariaDB: Secret-Spalten muessen breit genug fuer Chiffretexte sein."""
    if conn.dialect.name not in ("mysql", "mariadb"):
        return
    pairs = ", ".join(f"('{t}','{c}')" for t, c in SECRET_COLUMNS)
    rows = conn.execute(text(
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = DATABASE() AND (TABLE_NAME, COLUMN_NAME) IN ({pairs})"
    )).all()
    found = {(str(r[0]).lower(), str(r[1]).lower()): (str(r[2] or "").lower(), r[3]) for r in rows}
    text_types = ("text", "mediumtext", "longtext")
    problems: list[str] = []
    for table, column in SECRET_COLUMNS:
        info = found.get((table, column))
        if table == "users":
            soll = "VARCHAR(512) oder TEXT"
            ok = info is not None and (
                info[0] in text_types or (info[0] == "varchar" and (info[1] or 0) >= 512)
            )
        else:
            soll = "TEXT"
            ok = info is not None and info[0] in text_types
        if not ok:
            ist = "fehlt" if info is None else (f"{info[0].upper()}({info[1]})" if info[1] else info[0].upper())
            problems.append(f"Spalte {table}.{column}: Ist {ist}, Soll {soll}.")
    if problems:
        raise SecretsStartupError(
            "SCHEMA_TOO_NARROW",
            "Datenbankschema nicht migriert",
            [*problems, "Siehe Meldungen der DB-Migration weiter oben im Log (MODIFY COLUMN fehlgeschlagen?)."],
        )


def init_secrets_sync(conn: "Connection", cfg: KeyConfig) -> tuple[StartupReport, Optional[SecretBox]]:
    """Kernlogik der Startmigration (eine Transaktion, Raw-SQL). Wirft SecretsStartupError."""
    _check_column_widths(conn)
    values = scan_values(conn)
    real_encrypted = sum(1 for v in values if is_encrypted(v.raw))
    file_state = read_key_file(cfg.file_path)
    decision = _decide(cfg, file_state, real_encrypted)
    for line in decision.warnings:
        logger.warning(line)

    report = StartupReport(
        mode="encrypted",
        fallback_reason=None,
        key_source=decision.key_source,
        key_file=str(cfg.file_path),
        fingerprint=None,
        decrypt_only_keys=0,
        key_generated=False,
        generated_reason=None,
        migrated={},
        rotated={},
        unreadable=[],
        static_issues=list(decision.issues),
        at=_utcnow_naive(),
        key_file_explicit=cfg.file_explicit,
        previous_keys=decision.previous_keys,
    )

    primary = decision.primary
    fallback_reason = decision.fallback_reason if decision.action == "fallback" else None
    if decision.action == "replace_invalid":
        try:
            _move_invalid_key_file(cfg.file_path)
            primary = write_new_key_file(cfg.file_path)
        except OSError as exc:
            logger.error("Ungueltige Schluesseldatei %s konnte nicht ersetzt werden: %s", cfg.file_path, exc)
            fallback_reason = "key_file_invalid"
    elif decision.action == "generate":
        try:
            primary = write_new_key_file(cfg.file_path)
        except OSError as exc:
            logger.error("Schluesseldatei %s konnte nicht geschrieben werden: %s", cfg.file_path, exc)
            fallback_reason = "key_file_unwritable"

    if fallback_reason is not None or primary is None:
        report.mode = "plaintext_fallback"
        report.fallback_reason = fallback_reason or "key_file_unwritable"
        report.key_source = None
        return report, None

    if decision.action in ("replace_invalid", "generate"):
        report.key_generated = True
        report.generated_reason = decision.generated_reason

    box = SecretBox(primary, decision.decrypt_only)
    report.fingerprint = box.fingerprint
    report.decrypt_only_keys = box.decrypt_only_count
    report.decrypt_only_fingerprints = box.decrypt_only_fingerprints

    classes = [(v, box.classify(v.raw)) for v in values]
    # 8: Es gibt Chiffretexte, aber keiner ist mit irgendeinem Schluessel lesbar.
    if real_encrypted and not any(c in ("encrypted", "encrypted_old") for _, c in classes):
        extra = ", ".join(box.decrypt_only_fingerprints) or "keine"
        raise SecretsStartupError(
            "KEY_MISMATCH",
            "Schluessel passt nicht",
            [
                f"Keiner der {real_encrypted} verschluesselten Werte laesst sich mit den konfigurierten Schluesseln "
                f"entschluesseln. Aktiv: Fingerprint {box.fingerprint} (Quelle {report.key_source}); "
                f"Zusatzschluessel: {extra}. Den urspruenglichen Schluessel als SECRET_ENCRYPTION_KEY "
                "(oder SECRET_ENCRYPTION_KEY_PREVIOUS) eintragen.",
                *_RESET_HINT_LINES,
            ],
        )

    migrated: Counter[str] = Counter()
    rotated: Counter[str] = Counter()
    for v, c in classes:
        if c == "plaintext":
            update_stored_value(conn, v, box.encrypt(v.raw or ""))
            migrated[v.field] += 1
        elif c == "encrypted_old":
            update_stored_value(conn, v, box.rotate(v.raw or ""))
            rotated[v.field] += 1
        elif c == "unreadable":
            report.unreadable.append(v)
    report.migrated = dict(migrated)
    report.rotated = dict(rotated)
    return report, box


# ---------------------------------------------------------------------------
# Modulzustand, Lese-/Schreibpfad
# ---------------------------------------------------------------------------


@dataclass
class _SecretsState:
    mode: Literal["uninitialized", "encrypted", "plaintext_fallback"] = "uninitialized"
    box: Optional[SecretBox] = None
    report: Optional[StartupReport] = None
    unreadable_counts: Counter[str] = field(default_factory=Counter)
    last_log: dict[str, float] = field(default_factory=dict)
    suppressed: Counter[str] = field(default_factory=Counter)
    lazy_attempted: bool = False


_STATE = _SecretsState()


def _log_throttled(key: str, level: int, msg: str, *args: Any) -> None:
    """Erstes Auftreten pro Key loggen, danach hoechstens alle 600 s (mit Zaehler)."""
    now = time.monotonic()
    last = _STATE.last_log.get(key)
    if last is not None and now - last < LOG_THROTTLE_SECONDS:
        _STATE.suppressed[key] += 1
        return
    skipped = _STATE.suppressed.pop(key, 0)
    if skipped:
        msg = msg + " (%d gleiche Meldungen seit der letzten Ausgabe unterdrueckt)"
        args = (*args, skipped)
    _STATE.last_log[key] = now
    logger.log(level, msg, *args)


def load_box(cfg: Optional[KeyConfig] = None) -> Optional[SecretBox]:
    """Schluessel aus Env/Datei laden wie die Matrix, aber ohne Erzeugen, ohne DB, ohne Abbruch.

    Fuer Lesen/Schreiben vor init_secrets (Tests, CLI). Wirft nie; None = kein Schluessel.
    """
    try:
        cfg = cfg or KeyConfig.from_settings()
        file_state = read_key_file(cfg.file_path)
        env_primary = _try_parse_key(cfg.env_primary) if cfg.env_primary else None
        if cfg.env_primary and env_primary is None:
            _log_throttled("lazy-load-env", logging.ERROR,
                           "SECRET_ENCRYPTION_KEY ist kein gueltiger Fernet-Schluessel – Geheimnisse nicht verfuegbar")
            return None
        previous = [k for k in (_try_parse_key(p) for p in cfg.env_previous) if k is not None]
        if env_primary is not None:
            extra = list(previous)
            if file_state.status == "ok" and file_state.key is not None:
                extra.append(file_state.key)
            return SecretBox(env_primary, extra)
        if file_state.status == "ok" and file_state.key is not None:
            return SecretBox(file_state.key, previous)
        return None
    except Exception:  # noqa: BLE001 - Lesepfad darf nie werfen
        logger.exception("Schluessel fuer gespeicherte Geheimnisse konnte nicht geladen werden")
        return None


def _current_box() -> Optional[SecretBox]:
    """Aktive Box; im Zustand "uninitialized" einmalig lazy aus Env/Datei laden."""
    if _STATE.box is not None:
        return _STATE.box
    if _STATE.mode != "uninitialized" or _STATE.lazy_attempted:
        return None
    _STATE.lazy_attempted = True
    box = load_box()
    if box is not None:
        _STATE.box = box
        _STATE.mode = "encrypted"
    return box


def _unreadable(label: str) -> UnreadableSecret:
    _STATE.unreadable_counts[label] += 1
    _log_throttled(
        f"unreadable:{label}", logging.ERROR,
        "Geheimnis in %s kann nicht entschluesselt werden (falscher/fehlender Schluessel oder "
        "beschaedigter Wert) – bitte neu eintragen", label,
    )
    return UNREADABLE


def encrypt_value(value: Optional[str]) -> Optional[str]:
    """Schreibpfad (TypeDecorator, Settings-Helper, Listener)."""
    if is_unreadable(value):
        raise SecretsUnavailableError("Unlesbarer Platzhalter darf nicht gespeichert werden")
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        raise TypeError("Geheimnisse muessen als String gespeichert werden")
    if _STATE.mode == "plaintext_fallback":
        _log_throttled("fallback-write", logging.WARNING,
                       "Geheimnis wird unverschluesselt gespeichert (Fallback-Modus, Schluessel nicht verfuegbar)")
        return value
    box = _current_box()
    if box is None:
        raise SecretsUnavailableError(
            "Kein Schluessel fuer gespeicherte Geheimnisse verfuegbar (init_secrets nicht gelaufen und weder "
            "SECRET_ENCRYPTION_KEY noch eine gueltige Schluesseldatei vorhanden)"
        )
    return box.encrypt(value)


def decrypt_value(value: Optional[str], *, label: str) -> Optional[str]:
    """Lesepfad – wirft NIE. Unlesbar -> UNREADABLE (+ gedrosseltes ERROR-Log, Zaehler)."""
    if value is None or not isinstance(value, str) or not value.startswith(PREFIX):
        return value  # Legacy-Klartext, leer oder None
    box = _current_box()
    if box is None:
        return _unreadable(label)
    try:
        return box.decrypt(value)
    except Exception:  # noqa: BLE001 - InvalidToken, ValueError, UnicodeError, ...
        return _unreadable(label)


# ---------------------------------------------------------------------------
# TypeDecorators
# ---------------------------------------------------------------------------


class _EncryptedMixin:
    """Transparente Ver-/Entschluesselung; nie in WHERE/ORDER BY verwenden (nicht deterministisch).

    ``label`` (Spalten-ID, z. B. "server_configs.api_key") erscheint nur in Logs/Zaehlern. Es ist ein
    normaler (nicht keyword-only) Parameter, damit SQLAlchemy ihn in den Cache-Key aufnimmt.
    """

    label: str

    def _init_label(self, label: str) -> None:
        if not label:
            raise TypeError("Verschluesselte Spalten brauchen ein label (z. B. label=\"server_configs.api_key\")")
        self.label = label

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return encrypt_value(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return decrypt_value(value, label=self.label)


class EncryptedText(_EncryptedMixin, TypeDecorator):
    """TEXT-Spalte mit transparenter Verschluesselung (api_key, webhooks.secret/url)."""

    impl = Text
    cache_ok = True  # direkt an der Klasse: SQLAlchemy liest cache_ok aus dem Klassen-__dict__

    def __init__(self, label: str = "", **kwargs: Any) -> None:
        self._init_label(label)
        super().__init__(**kwargs)


class EncryptedString(_EncryptedMixin, TypeDecorator):
    """VARCHAR(n)-Spalte mit transparenter Verschluesselung (users.totp_*: 512)."""

    impl = String
    cache_ok = True

    def __init__(self, length: Optional[int] = None, label: str = "", **kwargs: Any) -> None:
        self._init_label(label)
        super().__init__(length, **kwargs)


# ---------------------------------------------------------------------------
# Start (async), Status, Testhilfen
# ---------------------------------------------------------------------------


def _activate(report: StartupReport, box: Optional[SecretBox]) -> None:
    _STATE.mode = report.mode
    _STATE.box = box
    _STATE.report = report
    _STATE.lazy_attempted = True


def _log_summary(report: StartupReport) -> None:
    if report.mode == "plaintext_fallback":
        logger.error(
            "Geheimnis-Verschluesselung NICHT aktiv (Grund: %s): Schluesseldatei %s kann nicht verwendet werden. "
            "Geheimnisse werden bis zur Behebung im Klartext gespeichert. Beheben: %s",
            report.fallback_reason, report.key_file, _FALLBACK_FIX_HINT,
        )
        return
    logger.info(
        "Geheimnis-Verschluesselung aktiv (Quelle: %s, Fingerprint %s, %d Zusatzschluessel); "
        "%d verschluesselt, %d umgeschluesselt, %d nicht lesbar.",
        report.key_source, report.fingerprint, report.decrypt_only_keys,
        sum(report.migrated.values()), sum(report.rotated.values()), len(report.unreadable),
    )
    for item in report.unreadable:
        logger.error(
            "Geheimnis nicht entschluesselbar: %s (ID %s, %s%s) – bitte neu eintragen.",
            item.field, item.row_id, item.name or "-",
            f", Besitzer {item.owner}" if item.owner else "",
        )
    if "key_file_permissions" in report.static_issues:
        logger.warning("Schluesseldatei %s ist fuer andere lesbar und konnte nicht auf 0600 gesetzt werden.",
                       report.key_file)


async def _write_startup_audits(report: StartupReport) -> None:
    """Audit der Startereignisse (detached, status=success, ohne Nutzer, nie Werte)."""
    if report.mode != "encrypted":
        return
    if not (report.key_generated or report.migrated or report.rotated):
        return
    from app.services.audit import write_audit_detached  # spaet importiert: audit -> models -> secrets

    if report.key_generated:
        await write_audit_detached(
            "SECRETS_KEY_GENERATED", "system", "secrets", user_id=None, status="success",
            details={"key_source": "generated", "key_file": report.key_file,
                     "key_fingerprint": report.fingerprint, "reason": report.generated_reason},
        )
    if report.migrated:
        await write_audit_detached(
            "SECRETS_MIGRATE", "system", "secrets", user_id=None, status="success",
            details={"migrated": dict(report.migrated), "key_fingerprint": report.fingerprint},
        )
    if report.rotated:
        await write_audit_detached(
            "SECRETS_ROTATE", "system", "secrets", user_id=None, status="success",
            details={"rotated": dict(report.rotated), "key_fingerprint": report.fingerprint,
                     "decrypt_only_keys": report.decrypt_only_keys},
        )


async def init_secrets(engine: "AsyncEngine", cfg: Optional[KeyConfig] = None) -> StartupReport:
    """Schluessel laden/pruefen und Klartexte verschluesseln – nach init_db, vor jedem ORM-Zugriff
    auf users/server_configs/webhooks. Wirft SecretsStartupError (Startabbruch)."""
    cfg = cfg or KeyConfig.from_settings()
    try:
        async with engine.begin() as conn:
            report, box = await conn.run_sync(init_secrets_sync, cfg)
    except SecretsStartupError:
        raise
    except SQLAlchemyError as exc:
        raise SecretsStartupError(
            "MIGRATION_FAILED",
            "Migration der Geheimnisse fehlgeschlagen",
            [
                f"Datenbankfehler: {type(exc).__name__}",
                "Es wurde nichts veraendert (Transaktion zurueckgerollt). Beim naechsten Start wird erneut versucht.",
            ],
        ) from exc
    _activate(report, box)
    _log_summary(report)
    try:
        await _write_startup_audits(report)
    except Exception as exc:  # noqa: BLE001 - Audit darf den Start nicht abbrechen
        logger.warning("Audit der Geheimnis-Migration konnte nicht geschrieben werden: %s", exc)
    return report


_ISSUE_SEVERITY: Final[dict[str, str]] = {
    "plaintext_fallback": "error",
    "unreadable_values": "error",
    "plaintext_values": "warning",
    "key_file_permissions": "warning",
    "previous_keys_still_needed": "info",
    "previous_keys_unused": "info",
    "key_file_obsolete": "info",
    "key_only_in_volume": "info",
}


def _all_field_ids() -> list[str]:
    return [_column_id(t, c) for t, c in SECRET_COLUMNS] + [_setting_id(k) for k in _SETTING_KEY_ORDER]


def build_status(values: Sequence[StoredSecret]) -> dict[str, Any]:
    """Status-Struktur (F5 §3.1 SecretsStatusOut) aus gescannten Werten – nie Werte selbst."""
    report = _STATE.report
    box = _current_box()
    if report is None:
        # Ohne init_secrets (z. B. CLI): bestmoegliche Angaben aus Env/Datei, nichts erzeugen.
        cfg = KeyConfig.from_settings()
        file_state = read_key_file(cfg.file_path)
        env_ok = bool(cfg.env_primary) and _try_parse_key(cfg.env_primary) is not None
        report = StartupReport(
            mode="encrypted" if box is not None else "plaintext_fallback",
            fallback_reason=None,
            key_source=("env" if env_ok else ("file" if file_state.status == "ok" else None)),
            key_file=str(cfg.file_path),
            fingerprint=box.fingerprint if box else None,
            decrypt_only_keys=box.decrypt_only_count if box else 0,
            key_generated=False, generated_reason=None, migrated={}, rotated={}, unreadable=[],
            static_issues=[], at=_utcnow_naive(), key_file_explicit=cfg.file_explicit,
            previous_keys=len(cfg.env_previous),
        )
        if file_state.status == "ok":
            if not file_state.mode_ok:
                report.static_issues.append("key_file_permissions")
            if env_ok and file_state.key != _try_parse_key(cfg.env_primary):
                report.static_issues.append("key_file_obsolete")
        last_startup = None
    else:
        last_startup = {
            "at": _iso_utc(report.at),
            "key_generated": report.key_generated,
            "migrated": sum(report.migrated.values()),
            "rotated": sum(report.rotated.values()),
        }

    counts: dict[str, Counter[str]] = {fid: Counter() for fid in _all_field_ids()}
    unreadable_items: list[dict[str, Any]] = []
    for v in values:
        if box is not None:
            c = box.classify(v.raw)
        elif v.raw is None or v.raw == "":
            c = "empty"
        else:
            c = "unreadable" if is_encrypted(v.raw) else "plaintext"
        counts.setdefault(v.field, Counter())[c] += 1
        if c == "unreadable":
            unreadable_items.append({
                "kind": v.kind,
                "field": v.field,
                "id": None if v.kind == "setting" else v.row_id,
                "name": v.name,
                "owner": v.owner if v.kind == "webhook" else None,
            })

    columns = [
        {
            "id": fid,
            "encrypted": cnt["encrypted"],
            "encrypted_old": cnt["encrypted_old"],
            "plaintext": cnt["plaintext"],
            "unreadable": cnt["unreadable"],
            "empty": cnt["empty"],
        }
        for fid, cnt in counts.items()
    ]
    total = Counter()
    for cnt in counts.values():
        total.update(cnt)

    issues: list[str] = []
    if report.mode == "plaintext_fallback":
        issues.append("plaintext_fallback")
    if total["unreadable"]:
        issues.append("unreadable_values")
    if report.mode == "encrypted" and total["plaintext"]:
        issues.append("plaintext_values")
    for code in report.static_issues:
        if code not in issues:
            issues.append(code)
    if total["encrypted_old"]:
        issues.append("previous_keys_still_needed")
    elif report.previous_keys > 0:
        issues.append("previous_keys_unused")
    if report.key_source in ("file", "generated") and not report.key_file_explicit:
        issues.append("key_only_in_volume")

    severities = {_ISSUE_SEVERITY.get(code, "info") for code in issues}
    health = "error" if "error" in severities else ("warning" if "warning" in severities else "ok")
    try:
        key_file_exists = Path(report.key_file).exists()
    except OSError:
        key_file_exists = False

    return {
        "mode": report.mode,
        "fallback_reason": report.fallback_reason,
        "key_source": report.key_source,
        "key_file": report.key_file,
        "key_file_exists": key_file_exists,
        "key_fingerprint": report.fingerprint,
        "decrypt_only_keys": report.decrypt_only_keys,
        "health": health,
        "issues": issues,
        "columns": columns,
        "unreadable": unreadable_items,
        "last_startup": last_startup,
    }


def _iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


async def collect_status(db: "AsyncSession") -> dict[str, Any]:
    """Live-Scan fuer API (GET /settings/secrets/status) und CLI – nie Werte."""
    values = await db.run_sync(lambda s: scan_values(s.connection()))
    return build_status(values)


def current_mode() -> Literal["uninitialized", "encrypted", "plaintext_fallback"]:
    """Aktueller Modus (fuer /health: plaintext_fallback -> status "degraded")."""
    return _STATE.mode


def startup_report() -> Optional[StartupReport]:
    """Bericht des letzten init_secrets (None vor dem Start bzw. in Tests/CLI ohne Start)."""
    return _STATE.report


def runtime_unreadable_counts() -> dict[str, int]:
    """Anzahl unlesbarer Lesezugriffe je Feld seit Prozessstart (fuer F13-Metriken)."""
    return dict(_STATE.unreadable_counts)


def reset_for_tests() -> None:
    global _STATE
    _STATE = _SecretsState()


def configure_for_tests(
    primary: Optional[str] = None,
    previous: Sequence[str] = (),
    *,
    mode: str = "encrypted",
) -> None:
    """Setzt den Modulzustand direkt (ohne DB/Datei). primary=None erzeugt einen Zufallsschluessel."""
    reset_for_tests()
    if mode == "uninitialized":
        return
    if mode == "plaintext_fallback":
        _activate(StartupReport(
            mode="plaintext_fallback", fallback_reason="key_file_unwritable", key_source=None,
            key_file=str(DEFAULT_KEY_FILE), fingerprint=None, decrypt_only_keys=0, key_generated=False,
            generated_reason=None, migrated={}, rotated={}, unreadable=[], static_issues=[], at=_utcnow_naive(),
        ), None)
        return
    if mode != "encrypted":
        raise ValueError(f"Unbekannter Modus: {mode}")
    box = SecretBox(
        parse_key(primary or generate_key(), source_name="configure_for_tests(primary)"),
        [parse_key(p, source_name="configure_for_tests(previous)") for p in previous],
    )
    _activate(StartupReport(
        mode="encrypted", fallback_reason=None, key_source="env", key_file=str(DEFAULT_KEY_FILE),
        fingerprint=box.fingerprint, decrypt_only_keys=box.decrypt_only_count, key_generated=False,
        generated_reason=None, migrated={}, rotated={}, unreadable=[], static_issues=[], at=_utcnow_naive(),
        previous_keys=box.decrypt_only_count, decrypt_only_fingerprints=box.decrypt_only_fingerprints,
    ), box)
