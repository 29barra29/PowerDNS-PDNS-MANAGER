"""CLI fuer gespeicherte Geheimnisse (F5 §5.15, Bauplan A.8): Status, Recovery und Downgrade-Vorbereitung.

Aufruf im Backend-Container (Image 3.x, gleiche Umgebung wie das Backend)::

    docker compose run --rm --name pdnsmgr-secrets-cli backend python -m app.cli.secrets <kommando>

Kommandos:

``status [--json]``
    Wie ``GET /api/v1/settings/secrets/status``: Modus, Schluesselquelle, Fingerprint, Zaehler je Feld,
    nicht lesbare Eintraege (nur Namen). Exit 0, 1 bei DB-Fehler.
``generate-key``
    Druckt einen neuen Fernet-Schluessel (eine Zeile, fuer ``SECRET_ENCRYPTION_KEY``). Exit 0.
``key-info``
    Schluesselquelle, Pfad und Fingerprint ohne DB-Zugriff (fuer ``update.sh``), Zeilen ``name=wert``.
    Exit 0; 1 wenn ``SECRET_ENCRYPTION_KEY`` ungueltig ist.
``reset-unreadable [--yes]``
    Setzt nicht entschluesselbare Werte zurueck (Schluessel verloren): API-Keys -> leer, Webhook-Secret ->
    neues Zufalls-Secret + Webhook deaktiviert, Webhook-URL -> leer + deaktiviert, Secret-Settings -> leer,
    2FA -> deaktiviert. Ohne Schluessel wird ein neuer erzeugt (Datei, nie bei explizitem
    ``SECRET_ENCRYPTION_KEY_FILE``). Ohne ``--yes`` nur Trockenlauf. Exit 0; 3 wenn nichts zu tun ist; 1 bei Fehlern.
``decrypt-all [--yes] [--force]``
    Alle lesbaren Geheimnisse zurueck in Klartext (Spezialfall; fuer einen Downgrade ``prepare-downgrade``
    verwenden). Exit 0; 1 ohne Schluessel; 2 bei nicht lesbaren Werten ohne ``--force``.
``prepare-downgrade [--yes] [--force]``
    Vorbereitung fuer einen Downgrade auf 2.4.x: ``decrypt-all`` plus Haertung der Panel-Tokens (Tokens mit
    Zonen-/Lese-Einschraenkung, Ablaufdatum oder Admin-Besitzer ohne ``allow_admin`` werden deaktiviert, weil
    2.4.x diese Einschraenkungen nicht kennt), Loeschen der Migrations-Marker (beim erneuten Upgrade laufen
    die Migrationen wieder: unter 2.4.x geloeschte Tokens werden als widerrufen markiert) und Audit
    ``DOWNGRADE_PREPARED``. Exit-Codes wie ``decrypt-all``.

Grundregeln: nie Werte, Chiffretexte oder Schluessel ausgeben (nur Feld-IDs, Namen, Anzahlen, Fingerprint);
schreibende Kommandos nur bei gestopptem Backend und in EINER Transaktion; Audit-Eintraege ohne Nutzer.
Die Kernlogik ist synchron (``Connection``) und damit ohne MariaDB testbar.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from sqlalchemy import bindparam, inspect, text

from app.core import secrets as secret_store
from app.core.secrets import SecretBox, StoredSecret, is_encrypted

logger = logging.getLogger("app.cli.secrets")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNREADABLE = 2
EXIT_NOTHING_TO_DO = 3

STOP_HINT = "Backend vorher stoppen: docker compose stop backend"
F14_MARKER = "migration_f14_panel_token_scope_v1"
AUDIT_BACKFILL_MARKER = "audit_zone_backfill_v1"
# Beim erneuten Upgrade sollen diese Datenmigrationen wieder laufen (beide sind idempotent):
# F14 markiert unter 2.4.x geloeschte Tokens als widerrufen, der Audit-Backfill ergaenzt zone_name.
DOWNGRADE_MARKERS = (F14_MARKER, AUDIT_BACKFILL_MARKER)

FIELD_LABELS = {
    "server_configs.api_key": "PowerDNS-API-Key",
    "webhooks.secret": "Webhook-Secret",
    "webhooks.url": "Webhook-URL",
    "users.totp_secret": "2FA-Geheimnis",
    "users.totp_pending_secret": "2FA-Geheimnis (Einrichtung offen)",
}


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class _CliAbort(RuntimeError):
    """Eigener Abbruchgrund; der Text stammt aus der CLI selbst und darf ausgegeben werden."""


def _safe_exc(exc: BaseException) -> str:
    """Fehlerbeschreibung ohne Werte (L7): nur Typnamen, bei DB-Fehlern zusaetzlich Treiberklasse und Fehlernummer.

    ``str(exc)`` einer SQLAlchemy-Ausnahme enthaelt das SQL samt gebundenen Parametern – beim Entschluesseln
    also Klartext-Geheimnisse – und Treibertexte wie ``Duplicate entry '<wert>'`` nennen Werte direkt. Deshalb
    wird nie der Text einer fremden Ausnahme ausgegeben, nur der eigener ``_CliAbort``-Abbrueche.
    """
    if isinstance(exc, _CliAbort):
        return str(exc)
    text = type(exc).__name__
    orig = getattr(exc, "orig", None)
    if orig is not None:
        text += f" / {type(orig).__name__}"
        args = getattr(orig, "args", None) or ()
        if args and isinstance(args[0], int):
            text += f" {args[0]}"
    return text


def _label(item: StoredSecret) -> str:
    if item.kind == "setting":
        return f"Einstellung {item.name}"
    base = FIELD_LABELS.get(item.field, item.field)
    who = item.name or f"ID {item.row_id}"
    if item.kind == "webhook" and item.owner:
        who = f"{who} (Besitzer {item.owner})"
    return f"{base}: {who}"


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------------------------
# Schluessel
# ---------------------------------------------------------------------------------------------


@dataclass
class CliKeys:
    """Verfuegbare Schluessel fuer die CLI (wie die Startmatrix, aber ohne Abbruch und ohne Erzeugen)."""

    box: Optional[SecretBox]            # alle verfuegbaren Schluessel (Primaer zuerst) oder None
    source: str                         # "env" | "file" | "none"
    file_path: Path
    file_explicit: bool
    file_status: str                    # read_key_file: missing | ok | unreadable | invalid
    problems: list[str] = field(default_factory=list)
    key_list: list[bytes] = field(default_factory=list)   # dieselben Schluessel wie in box (nie ausgeben)


def resolve_keys(cfg: Optional[secret_store.KeyConfig] = None) -> CliKeys:
    """Laedt Env-/Dateischluessel. Wirft nie; Probleme stehen in ``problems`` (ohne Werte)."""
    cfg = cfg or secret_store.KeyConfig.from_settings()
    problems: list[str] = []
    env_key: Optional[bytes] = None
    if cfg.env_primary:
        try:
            env_key = secret_store.parse_key(cfg.env_primary, source_name="SECRET_ENCRYPTION_KEY")
        except secret_store.SecretsStartupError as exc:
            problems.extend(exc.lines)
    previous: list[bytes] = []
    for idx, raw in enumerate(cfg.env_previous, start=1):
        try:
            previous.append(secret_store.parse_key(raw, source_name=f"SECRET_ENCRYPTION_KEY_PREVIOUS (Eintrag {idx})"))
        except secret_store.SecretsStartupError as exc:
            problems.extend(exc.lines)
    file_state = secret_store.read_key_file(cfg.file_path)
    if file_state.status in ("unreadable", "invalid"):
        problems.append(f"Schluesseldatei {cfg.file_path}: {file_state.status} ({file_state.error})")

    keys: list[bytes] = []
    source = "none"
    if env_key is not None:
        keys.append(env_key)
        source = "env"
    elif cfg.env_primary:
        source = "invalid"
    if file_state.status == "ok" and file_state.key is not None:
        if not keys and source != "invalid":
            source = "file"
        keys.append(file_state.key)
    keys.extend(previous)
    deduped: list[bytes] = []
    for k in keys:
        if k not in deduped:
            deduped.append(k)
    box = SecretBox(deduped[0], deduped[1:]) if deduped else None
    return CliKeys(box=box, source=source, file_path=cfg.file_path, file_explicit=cfg.file_explicit,
                   file_status=file_state.status, problems=problems, key_list=deduped)


def classify(box: Optional[SecretBox], raw: Optional[str]) -> str:
    """empty | plaintext | encrypted | encrypted_old | unreadable (ohne Schluessel: Praefix = unreadable)."""
    if raw is None or raw == "":
        return "empty"
    if not is_encrypted(raw):
        return "plaintext"
    if box is None:
        return "unreadable"
    return box.classify(raw)


# ---------------------------------------------------------------------------------------------
# Kernlogik (synchron, Connection) – decrypt-all
# ---------------------------------------------------------------------------------------------


@dataclass
class DecryptPlan:
    readable: list[StoredSecret]
    unreadable: list[StoredSecret]

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(v.field for v in self.readable))


def plan_decrypt(values: Sequence[StoredSecret], box: Optional[SecretBox]) -> DecryptPlan:
    readable: list[StoredSecret] = []
    unreadable: list[StoredSecret] = []
    for v in values:
        c = classify(box, v.raw)
        if c in ("encrypted", "encrypted_old"):
            readable.append(v)
        elif c == "unreadable":
            unreadable.append(v)
    return DecryptPlan(readable, unreadable)


def apply_decrypt(conn, plan: DecryptPlan, box: SecretBox) -> dict[str, int]:
    """Schreibt die lesbaren Werte als Klartext zurueck (Raw-SQL, ohne TypeDecorator)."""
    done: Counter[str] = Counter()
    for v in plan.readable:
        secret_store.update_stored_value(conn, v, box.decrypt(v.raw or ""))
        done[v.field] += 1
    return dict(done)


# ---------------------------------------------------------------------------------------------
# Kernlogik – Token-Haertung und Marker (prepare-downgrade)
# ---------------------------------------------------------------------------------------------

TOKEN_COLUMNS_30 = ("scope_zones", "permission", "expires_at", "allow_admin", "revoked_at")


def _table_columns(conn, table: str) -> set[str]:
    try:
        return {c["name"] for c in inspect(conn).get_columns(table)}
    except Exception:  # noqa: BLE001 - Tabelle fehlt -> leere Menge
        return set()


def _scope_list(raw: Any) -> Optional[list]:
    """``scope_zones`` (JSON) -> Liste oder None. JSON-``null`` gilt als "alle Zonen"."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return ["?"]  # unbekanntes Format: vorsichtshalber als eingeschraenkt behandeln
    if raw is None:
        return None
    return list(raw) if isinstance(raw, (list, tuple)) else ["?"]


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


@dataclass(frozen=True)
class TokenAction:
    token_id: int
    name: str
    owner: Optional[str]
    reasons: tuple[str, ...]


def plan_token_hardening(conn, *, now: Optional[datetime] = None) -> list[TokenAction]:
    """Aktive Panel-Tokens, die unter 2.4.x mehr duerften als in 3.0 (F14 §4.5) -> deaktivieren.

    Gruende: ``revoked`` (Widerruf ohne is_active=0), ``zone_scope``, ``read_only``, ``expired``/``expires``
    (2.4.x kennt kein Ablaufdatum), ``admin_without_allow_admin``.
    """
    cols = _table_columns(conn, "panel_tokens")
    if not cols or not set(TOKEN_COLUMNS_30) <= cols:
        return []  # Schema ohne 3.0-Spalten: es gibt nichts einzuschraenken
    now = now or _utcnow_naive()
    rows = conn.execute(text(
        "SELECT pt.id, pt.name, pt.revoked_at, pt.scope_zones, pt.permission, pt.expires_at, pt.allow_admin, "
        "u.role, u.username FROM panel_tokens pt LEFT JOIN users u ON u.id = pt.user_id "
        "WHERE pt.is_active = 1 ORDER BY pt.id"
    )).all()
    actions: list[TokenAction] = []
    for r in rows:
        reasons: list[str] = []
        if r.revoked_at is not None:
            reasons.append("revoked")
        if _scope_list(r.scope_zones) is not None:
            reasons.append("zone_scope")
        if (r.permission or "manage") != "manage":
            reasons.append("read_only")
        exp = _as_datetime(r.expires_at)
        if r.expires_at is not None:
            reasons.append("expired" if exp is not None and exp <= now else "expires")
        if (r.role or "") == "admin" and not r.allow_admin:
            reasons.append("admin_without_allow_admin")
        if reasons:
            actions.append(TokenAction(int(r.id), str(r.name), r.username, tuple(reasons)))
    return actions


def apply_token_hardening(conn, actions: Sequence[TokenAction]) -> int:
    if not actions:
        return 0
    stmt = text("UPDATE panel_tokens SET is_active = 0 WHERE id IN :ids").bindparams(
        bindparam("ids", expanding=True))
    conn.execute(stmt, {"ids": [a.token_id for a in actions]})
    return len(actions)


def present_markers(conn, keys: Sequence[str] = DOWNGRADE_MARKERS) -> list[str]:
    stmt = text("SELECT `key` FROM system_settings WHERE `key` IN :keys ORDER BY `key`").bindparams(
        bindparam("keys", expanding=True))
    return [str(r[0]) for r in conn.execute(stmt, {"keys": list(keys)}).all()]


def delete_markers(conn, keys: Sequence[str]) -> None:
    if not keys:
        return
    stmt = text("DELETE FROM system_settings WHERE `key` IN :keys").bindparams(bindparam("keys", expanding=True))
    conn.execute(stmt, {"keys": list(keys)})


def count_external_accounts(conn) -> int:
    if "auth_source" not in _table_columns(conn, "users"):
        return 0
    return int(conn.execute(text(
        "SELECT COUNT(*) FROM users WHERE auth_source IS NOT NULL AND auth_source <> 'local'"
    )).scalar() or 0)


@dataclass
class DowngradePlan:
    decrypt: DecryptPlan
    tokens: list[TokenAction]
    markers: list[str]
    external_accounts: int

    def token_reasons(self) -> dict[str, int]:
        c: Counter[str] = Counter()
        for a in self.tokens:
            c.update(a.reasons)
        return dict(sorted(c.items()))


def plan_prepare_downgrade(conn, box: Optional[SecretBox], *, now: Optional[datetime] = None) -> DowngradePlan:
    values = secret_store.scan_values(conn)
    return DowngradePlan(
        decrypt=plan_decrypt(values, box),
        tokens=plan_token_hardening(conn, now=now),
        markers=present_markers(conn),
        external_accounts=count_external_accounts(conn),
    )


def apply_prepare_downgrade(conn, plan: DowngradePlan, box: Optional[SecretBox]) -> dict[str, Any]:
    decrypted = apply_decrypt(conn, plan.decrypt, box) if (box is not None and plan.decrypt.readable) else {}
    deactivated = apply_token_hardening(conn, plan.tokens)
    delete_markers(conn, plan.markers)
    return {
        "decrypted": decrypted,
        "skipped_unreadable": len(plan.decrypt.unreadable),
        "tokens_deactivated": deactivated,
        "token_reasons": plan.token_reasons(),
        "markers_removed": list(plan.markers),
        "external_accounts": plan.external_accounts,
    }


# ---------------------------------------------------------------------------------------------
# Kernlogik – reset-unreadable
# ---------------------------------------------------------------------------------------------


@dataclass
class ResetPlan:
    servers: list[StoredSecret]
    webhook_secrets: list[StoredSecret]
    webhook_urls: list[StoredSecret]
    settings: list[StoredSecret]
    totp: list[StoredSecret]
    totp_pending: list[StoredSecret]

    @property
    def items(self) -> list[StoredSecret]:
        return [*self.servers, *self.webhook_secrets, *self.webhook_urls, *self.settings, *self.totp,
                *self.totp_pending]

    @property
    def empty(self) -> bool:
        return not self.items


def plan_reset(values: Sequence[StoredSecret], box: Optional[SecretBox]) -> ResetPlan:
    plan = ResetPlan([], [], [], [], [], [])
    totp_ids: set[int] = set()
    for v in values:
        if classify(box, v.raw) != "unreadable":
            continue
        if v.field == "server_configs.api_key":
            plan.servers.append(v)
        elif v.field == "webhooks.secret":
            plan.webhook_secrets.append(v)
        elif v.field == "webhooks.url":
            plan.webhook_urls.append(v)
        elif v.field == "users.totp_secret":
            plan.totp.append(v)
            totp_ids.add(v.row_id)
        elif v.field == "users.totp_pending_secret":
            plan.totp_pending.append(v)
        elif v.kind == "setting":
            plan.settings.append(v)
    # Ein unlesbares aktives Geheimnis setzt auch das offene zurueck – nicht doppelt auffuehren.
    plan.totp_pending = [v for v in plan.totp_pending if v.row_id not in totp_ids]
    return plan


def apply_reset(conn, plan: ResetPlan, box: SecretBox, *, new_webhook_secret: Callable[[], str]) -> dict[str, Any]:
    """Setzt unlesbare Werte zurueck und verschluesselt danach Klartexte/Altschluessel mit ``box``."""
    for v in plan.servers:
        conn.execute(text("UPDATE server_configs SET api_key = '' WHERE id = :id"), {"id": v.row_id})
    for v in plan.webhook_secrets:
        conn.execute(text("UPDATE webhooks SET secret = :s, is_active = 0 WHERE id = :id"),
                     {"s": box.encrypt(new_webhook_secret()), "id": v.row_id})
    for v in plan.webhook_urls:
        conn.execute(text("UPDATE webhooks SET url = '', is_active = 0 WHERE id = :id"), {"id": v.row_id})
    for v in plan.settings:
        conn.execute(text("UPDATE system_settings SET `value` = '' WHERE id = :id"), {"id": v.row_id})
    for v in plan.totp:
        conn.execute(text(
            "UPDATE users SET totp_enabled = 0, totp_secret = NULL, totp_pending_secret = NULL WHERE id = :id"
        ), {"id": v.row_id})
    for v in plan.totp_pending:
        conn.execute(text("UPDATE users SET totp_pending_secret = NULL WHERE id = :id"), {"id": v.row_id})

    # Danach: dieselbe Migration wie beim Start (Klartext verschluesseln, Altschluessel umschluesseln)
    migrated: Counter[str] = Counter()
    rotated: Counter[str] = Counter()
    for v in secret_store.scan_values(conn):
        c = box.classify(v.raw)
        if c == "plaintext":
            secret_store.update_stored_value(conn, v, box.encrypt(v.raw or ""))
            migrated[v.field] += 1
        elif c == "encrypted_old":
            secret_store.update_stored_value(conn, v, box.rotate(v.raw or ""))
            rotated[v.field] += 1

    webhooks: dict[int, dict] = {}
    for v in [*plan.webhook_secrets, *plan.webhook_urls]:
        entry = webhooks.setdefault(v.row_id, {"id": v.row_id, "name": v.name, "owner": v.owner, "fields": []})
        entry["fields"].append(v.field.split(".", 1)[1])
    return {
        "servers": [v.name for v in plan.servers],
        "webhooks": list(webhooks.values()),
        "settings": [v.name for v in plan.settings],
        "users_2fa_reset": [v.name for v in plan.totp],
        "users_2fa_pending_cleared": [v.name for v in plan.totp_pending],
        "migrated": dict(migrated),
        "rotated": dict(rotated),
    }


# ---------------------------------------------------------------------------------------------
# Infrastruktur (fuer Tests austauschbar)
# ---------------------------------------------------------------------------------------------


def _get_engine():
    from app.core.database import engine

    return engine


async def _write_audit(action: str, details: dict[str, Any]) -> Optional[int]:
    from app.services.audit import write_audit_detached

    return await write_audit_detached(action, "system", "secrets", user_id=None, status="success",
                                      details=details)


async def _run_sync(fn: Callable, *args, write: bool = False, **kwargs):
    """Fuehrt ``fn(conn, ...)`` in einer Transaktion aus (``write=False``: wird zurueckgerollt)."""
    engine = _get_engine()
    if write:
        async with engine.begin() as conn:
            return await conn.run_sync(fn, *args, **kwargs)
    async with engine.connect() as conn:
        try:
            return await conn.run_sync(fn, *args, **kwargs)
        finally:
            await conn.rollback()


# ---------------------------------------------------------------------------------------------
# Kommandos
# ---------------------------------------------------------------------------------------------


def _print_problems(keys: CliKeys) -> None:
    for line in keys.problems:
        _err(f"WARNUNG: {line}")


async def cmd_status(args: argparse.Namespace) -> int:
    try:
        values = await _run_sync(secret_store.scan_values)
    except Exception as exc:  # noqa: BLE001 - DB nicht erreichbar o. ae.
        _err(f"FEHLER: Datenbank nicht lesbar ({_safe_exc(exc)}). DATABASE_URL und DB-Container pruefen.")
        return EXIT_ERROR
    data = secret_store.build_status(values)
    if args.json:
        _out(json.dumps(data, indent=2, ensure_ascii=False))
        return EXIT_OK
    _out(f"Modus:            {data['mode']}" + (f" ({data['fallback_reason']})" if data["fallback_reason"] else ""))
    _out(f"Schluesselquelle: {data['key_source'] or '-'}")
    _out(f"Schluesseldatei:  {data['key_file']} ({'vorhanden' if data['key_file_exists'] else 'fehlt'})")
    _out(f"Fingerprint:      {data['key_fingerprint'] or '-'}")
    _out(f"Zusatzschluessel: {data['decrypt_only_keys']}")
    _out(f"Zustand:          {data['health']}")
    _out("")
    _out(f"{'Feld':40} {'verschl.':>8} {'alt':>5} {'Klartext':>9} {'unlesbar':>9} {'leer':>6}")
    for col in data["columns"]:
        _out(f"{col['id']:40} {col['encrypted']:>8} {col['encrypted_old']:>5} {col['plaintext']:>9} "
             f"{col['unreadable']:>9} {col['empty']:>6}")
    if data["unreadable"]:
        _out("")
        _out("Nicht lesbar:")
        for item in data["unreadable"]:
            owner = f", Besitzer {item['owner']}" if item.get("owner") else ""
            _out(f"  - {item['field']}: {item.get('name') or '-'} (ID {item.get('id') or '-'}{owner})")
    if data["issues"]:
        _out("")
        _out("Hinweise: " + ", ".join(data["issues"]))
    return EXIT_OK


def cmd_generate_key(args: argparse.Namespace) -> int:
    _out(secret_store.generate_key())
    return EXIT_OK


def cmd_key_info(args: argparse.Namespace) -> int:
    """Maschinenlesbar fuer update.sh: Quelle, Datei, Fingerprint (nie der Schluessel)."""
    cfg = secret_store.KeyConfig.from_settings()
    keys = resolve_keys(cfg)
    fingerprint = ""
    if keys.source in ("env", "file") and keys.box is not None:
        fingerprint = keys.box.fingerprint
    try:
        exists = cfg.file_path.exists()
    except OSError:
        exists = False
    _out(f"source={keys.source}")
    _out(f"key_file={cfg.file_path}")
    _out(f"key_file_explicit={1 if cfg.file_explicit else 0}")
    _out(f"key_file_exists={1 if exists else 0}")
    _out(f"key_file_status={keys.file_status}")
    _out(f"fingerprint={fingerprint}")
    return EXIT_ERROR if keys.source == "invalid" else EXIT_OK


def _print_reset_plan(plan: ResetPlan) -> None:
    _out(f"Nicht lesbare Werte: {len(plan.items)}")
    for v in plan.servers:
        _out(f"  - Server {v.name}: API-Key wird geleert (Server bleibt ungeladen, bis der Key neu eingetragen ist)")
    for v in plan.webhook_secrets:
        _out(f"  - Webhook {v.name} (Besitzer {v.owner or '-'}): neues Secret, Webhook wird deaktiviert")
    for v in plan.webhook_urls:
        _out(f"  - Webhook {v.name} (Besitzer {v.owner or '-'}): Ziel-URL wird geleert, Webhook wird deaktiviert")
    for v in plan.settings:
        _out(f"  - Einstellung {v.name}: wird geleert")
    for v in plan.totp:
        _out(f"  - Benutzer {v.name}: 2FA (TOTP) wird deaktiviert")
    for v in plan.totp_pending:
        _out(f"  - Benutzer {v.name}: offene 2FA-Einrichtung wird verworfen")


async def cmd_reset_unreadable(args: argparse.Namespace) -> int:
    cfg = secret_store.KeyConfig.from_settings()
    keys = resolve_keys(cfg)
    _print_problems(keys)
    if keys.source == "invalid":
        _err("FEHLER: SECRET_ENCRYPTION_KEY ist ungueltig – erst korrigieren oder entfernen.")
        return EXIT_ERROR
    try:
        values = await _run_sync(secret_store.scan_values)
    except Exception as exc:  # noqa: BLE001
        _err(f"FEHLER: Datenbank nicht lesbar ({_safe_exc(exc)}).")
        return EXIT_ERROR
    plan = plan_reset(values, keys.box)
    if plan.empty:
        _out("Keine nicht lesbaren Werte gefunden – nichts zu tun.")
        return EXIT_NOTHING_TO_DO

    generate = keys.source == "none"
    if generate:
        if cfg.file_explicit:
            _err(f"FEHLER: SECRET_ENCRYPTION_KEY_FILE={cfg.file_path} ist gesetzt, die Datei fehlt oder ist ungueltig. "
                 "Bei explizitem Pfad wird kein Schluessel erzeugt – Datei bereitstellen oder "
                 "SECRET_ENCRYPTION_KEY setzen.")
            return EXIT_ERROR
        if keys.file_status == "unreadable":
            _err(f"FEHLER: {cfg.file_path} ist nicht lesbar. Rechte korrigieren: docker compose run --rm --no-deps "
                 "--name pdnsmgr-chown -u root backend chown -R 1001:1001 /app/data")
            return EXIT_ERROR

    _print_reset_plan(plan)
    if generate:
        _out(f"Es gibt keinen verwendbaren Schluessel: ein neuer wird erzeugt ({cfg.file_path}).")
    else:
        _out(f"Schluessel: Quelle {keys.source}, Fingerprint {keys.box.fingerprint if keys.box else '-'}")
    if not args.yes:
        _out("")
        _out("Trockenlauf – nichts geaendert. Ausfuehren mit --yes (" + STOP_HINT + ").")
        return EXIT_OK

    _err(f"Hinweis: {STOP_HINT}")
    key_generated = False
    box = keys.box
    if generate:
        try:
            if keys.file_status == "invalid":
                target = cfg.file_path.with_name(
                    f"{cfg.file_path.name}.invalid-{_utcnow_naive().strftime('%Y%m%d%H%M%S')}")
                cfg.file_path.rename(target)
                _out(f"Ungueltige Schluesseldatei umbenannt: {target}")
            new_key = secret_store.write_new_key_file(cfg.file_path)
        except OSError as exc:
            _err(f"FEHLER: Schluesseldatei {cfg.file_path} konnte nicht geschrieben werden: {exc}")
            return EXIT_ERROR
        box = SecretBox(new_key, keys.key_list)  # bisherige Schluessel (z. B. PREVIOUS) nur zum Entschluesseln
        key_generated = True

    from app.services.webhook_service import generate_webhook_secret

    assert box is not None
    try:
        result = await _run_sync(apply_reset, plan, box, new_webhook_secret=generate_webhook_secret, write=True)
    except Exception as exc:  # noqa: BLE001
        _err(f"FEHLER: Zuruecksetzen fehlgeschlagen ({_safe_exc(exc)}) – Transaktion zurueckgerollt, nichts geaendert.")
        return EXIT_ERROR

    details = {k: result[k] for k in ("servers", "webhooks", "settings", "users_2fa_reset")}
    details.update({"key_generated": key_generated, "key_fingerprint": box.fingerprint})
    await _write_audit("SECRETS_RESET_UNREADABLE", details)
    _out("")
    _out(f"Erledigt: {len(plan.items)} Werte zurueckgesetzt"
         + (f", neuer Schluessel (Fingerprint {box.fingerprint})." if key_generated else "."))
    if key_generated:
        _out("WICHTIG: Den neuen Schluessel getrennt vom DB-Backup sichern (./update.sh legt eine Kopie an).")
    _out("Naechste Schritte: docker compose up -d; danach API-Keys/SMTP/Captcha im Panel neu eintragen, "
         "Webhook-Besitzer rotieren das Secret und aktivieren den Webhook, betroffene Benutzer richten 2FA neu ein.")
    return EXIT_OK


def _print_decrypt_plan(plan: DecryptPlan) -> None:
    counts = plan.counts
    _out(f"Zu entschluesseln: {sum(counts.values())} Werte")
    for fid in sorted(counts):
        _out(f"  - {fid}: {counts[fid]}")
    if plan.unreadable:
        _out(f"Nicht lesbar (bleiben verschluesselt): {len(plan.unreadable)}")
        for v in plan.unreadable:
            _out(f"  - {_label(v)}")


def _check_decrypt_preconditions(keys: CliKeys, plan: DecryptPlan, force: bool) -> Optional[int]:
    if keys.source == "invalid":
        _err("FEHLER: SECRET_ENCRYPTION_KEY ist ungueltig – erst korrigieren.")
        return EXIT_ERROR
    if keys.box is None and (plan.readable or plan.unreadable):
        _err("FEHLER: Kein Schluessel verfuegbar (weder SECRET_ENCRYPTION_KEY noch eine gueltige Schluesseldatei). "
             "Ohne Schluessel lassen sich die Werte nicht entschluesseln.")
        return EXIT_ERROR
    if plan.unreadable and not force:
        _err(f"FEHLER: {len(plan.unreadable)} Werte sind nicht lesbar. Mit --force werden nur die lesbaren "
             "entschluesselt (die anderen funktionieren danach auch unter 2.4.x nicht).")
        return EXIT_UNREADABLE
    return None


async def cmd_decrypt_all(args: argparse.Namespace) -> int:
    _err("Hinweis: Fuer einen Downgrade auf 2.4.x 'prepare-downgrade' verwenden (entschluesselt UND haertet "
         "die Panel-Tokens, sonst haetten eingeschraenkte Tokens unter 2.4.x wieder Vollzugriff).")
    keys = resolve_keys()
    _print_problems(keys)
    try:
        values = await _run_sync(secret_store.scan_values)
    except Exception as exc:  # noqa: BLE001
        _err(f"FEHLER: Datenbank nicht lesbar ({_safe_exc(exc)}).")
        return EXIT_ERROR
    plan = plan_decrypt(values, keys.box)
    rc = _check_decrypt_preconditions(keys, plan, args.force)
    if rc is not None:
        if rc == EXIT_UNREADABLE:
            _print_decrypt_plan(plan)
        return rc
    _print_decrypt_plan(plan)
    if not args.yes:
        _out("")
        _out("Trockenlauf – nichts geaendert. Ausfuehren mit --yes (" + STOP_HINT + ").")
        return EXIT_OK
    _err(f"Hinweis: {STOP_HINT}")
    if not plan.readable:
        _out("Keine verschluesselten Werte vorhanden – nichts zu tun.")
        return EXIT_OK
    try:
        decrypted = await _run_sync(apply_decrypt, plan, keys.box, write=True)
    except Exception as exc:  # noqa: BLE001
        _err(f"FEHLER: Entschluesseln fehlgeschlagen ({_safe_exc(exc)}) – Transaktion zurueckgerollt.")
        return EXIT_ERROR
    await _write_audit("SECRETS_DECRYPT_ALL", {"decrypted": decrypted, "skipped_unreadable": len(plan.unreadable)})
    _out(f"Erledigt: {sum(decrypted.values())} Werte liegen jetzt im Klartext in der Datenbank.")
    _out("WICHTIG: Backend jetzt NICHT mehr mit 3.x starten, sonst wird erneut verschluesselt.")
    return EXIT_OK


def _print_downgrade_plan(plan: DowngradePlan) -> None:
    _print_decrypt_plan(plan.decrypt)
    _out(f"Panel-Tokens, die deaktiviert werden (2.4.x kennt ihre Einschraenkungen nicht): {len(plan.tokens)}")
    for a in plan.tokens:
        _out(f"  - Token {a.name} (ID {a.token_id}, Besitzer {a.owner or '-'}): {', '.join(a.reasons)}")
    _out("Migrations-Marker, die geloescht werden: " + (", ".join(plan.markers) or "keine"))
    if plan.external_accounts:
        _out(f"Hinweis: {plan.external_accounts} externe Konten (SSO) koennen sich unter 2.4.x nicht anmelden.")


async def cmd_prepare_downgrade(args: argparse.Namespace) -> int:
    keys = resolve_keys()
    _print_problems(keys)
    try:
        plan = await _run_sync(plan_prepare_downgrade, keys.box)
    except Exception as exc:  # noqa: BLE001
        _err(f"FEHLER: Datenbank nicht lesbar ({_safe_exc(exc)}).")
        return EXIT_ERROR
    rc = _check_decrypt_preconditions(keys, plan.decrypt, args.force)
    if rc is not None:
        if rc == EXIT_UNREADABLE:
            _print_decrypt_plan(plan.decrypt)
        return rc
    _print_downgrade_plan(plan)
    if not args.yes:
        _out("")
        _out("Trockenlauf – nichts geaendert. Ausfuehren mit --yes (" + STOP_HINT + ").")
        return EXIT_OK
    _err(f"Hinweis: {STOP_HINT}")
    try:
        # Neu planen innerhalb der schreibenden Transaktion (Stand zwischen Trockenlauf und Ausfuehrung)
        def _apply(conn):
            fresh = plan_prepare_downgrade(conn, keys.box)
            if fresh.decrypt.unreadable and not args.force:
                raise _CliAbort("nicht lesbare Werte")
            return apply_prepare_downgrade(conn, fresh, keys.box)

        result = await _run_sync(_apply, write=True)
    except Exception as exc:  # noqa: BLE001
        # Nie str(exc) einer DB-Ausnahme: sie enthielte die entschluesselten Werte als SQL-Parameter (L7).
        _err(f"FEHLER: Vorbereitung fehlgeschlagen ({_safe_exc(exc)}) – Transaktion zurueckgerollt, "
             "nichts geaendert.")
        return EXIT_ERROR
    await _write_audit("DOWNGRADE_PREPARED", result)
    _out("")
    _out(f"Erledigt: {sum(result['decrypted'].values())} Werte entschluesselt, "
         f"{result['tokens_deactivated']} Panel-Tokens deaktiviert, Marker geloescht: "
         f"{', '.join(result['markers_removed']) or 'keine'}.")
    _out("WICHTIG: Backend jetzt NICHT mehr mit 3.x starten, sonst wird erneut verschluesselt.")
    _out("Naechste Schritte: git checkout v2.4.1 (bzw. die gewuenschte 2.4.x-Version), "
         "docker compose build backend, docker compose up -d.")
    _out("Die Schluesseldatei bleibt erhalten; ein spaeteres Upgrade auf 3.x verschluesselt wieder.")
    return EXIT_OK


# ---------------------------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.cli.secrets",
        description="Gespeicherte Geheimnisse verwalten (Status, Recovery, Downgrade-Vorbereitung).",
    )
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("status", help="Status der Verschluesselung (wie Einstellungen -> Sicherheit)")
    s.add_argument("--json", action="store_true", help="Ausgabe als JSON (Struktur wie die API)")
    sub.add_parser("generate-key", help="Neuen Schluessel ausgeben (fuer SECRET_ENCRYPTION_KEY)")
    sub.add_parser("key-info", help="Schluesselquelle, Pfad und Fingerprint ohne DB (fuer update.sh)")
    r = sub.add_parser("reset-unreadable", help="Nicht lesbare Werte zuruecksetzen (Schluessel verloren)")
    r.add_argument("--yes", action="store_true", help="wirklich ausfuehren (sonst Trockenlauf)")
    for name, helptext in (
        ("decrypt-all", "Alle Geheimnisse in Klartext zurueckschreiben (Spezialfall)"),
        ("prepare-downgrade", "Downgrade auf 2.4.x vorbereiten (entschluesseln, Tokens haerten, Marker loeschen)"),
    ):
        d = sub.add_parser(name, help=helptext)
        d.add_argument("--yes", action="store_true", help="wirklich ausfuehren (sonst Trockenlauf)")
        d.add_argument("--force", action="store_true", help="nicht lesbare Werte ueberspringen")
    return p


_COMMANDS: dict[str, Callable] = {
    "status": cmd_status,
    "generate-key": cmd_generate_key,
    "key-info": cmd_key_info,
    "reset-unreadable": cmd_reset_unreadable,
    "decrypt-all": cmd_decrypt_all,
    "prepare-downgrade": cmd_prepare_downgrade,
}


async def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    handler = _COMMANDS[args.command]
    try:
        result = handler(args)
        if asyncio.iscoroutine(result):
            result = await result
        return int(result)
    except Exception as exc:  # noqa: BLE001 - kein Traceback: er koennte SQL-Parameter (Klartext) enthalten (L7)
        _err(f"FEHLER: unerwarteter Fehler ({_safe_exc(exc)}) – Details werden bewusst nicht ausgegeben.")
        return EXIT_ERROR
    finally:
        if args.command not in ("generate-key", "key-info"):
            try:
                await _get_engine().dispose()
            except Exception:  # noqa: BLE001
                pass


def run(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(main(argv))


if __name__ == "__main__":
    sys.exit(run())
