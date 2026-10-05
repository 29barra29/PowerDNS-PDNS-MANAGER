"""Status der Verschluesselung gespeicherter Geheimnisse (F5 3.1) – eigener Router (Bauplan B.13, ROUTER_ORDER 101).

``GET /api/v1/settings/secrets/status`` liefert Modus, Schluesselquelle, Fingerprint, Zaehler je Feld,
die Liste nicht lesbarer Eintraege und Hinweise (Issue-Codes). Nie Werte, nie Chiffretexte.
Nur fuer Admins mit Browser-Session (``get_admin_session_user``): Panel-Tokens bekommen 403.
Kein Audit (reiner Lesezugriff ohne Geheimnis). Schreibende Aktionen (Recovery, Downgrade) gibt es
bewusst nur als CLI im Container (``python -m app.cli.secrets``).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core import secrets as secret_store
from app.core.auth import get_admin_session_user
from app.core.database import DbRead
from app.models.models import User

ROUTER_ORDER = 101

router = APIRouter(prefix="/settings", tags=["Settings"])

# Alle Feld-IDs (Spalten + Secret-Settings), aus den Konstanten des Kerns abgeleitet: kommt ein Feld
# hinzu (z. B. webhooks.url [S10], system_settings.metrics_token), waechst das Literal automatisch mit.
SECRET_FIELD_IDS: tuple[str, ...] = tuple(
    [f"{table}.{column}" for table, column in secret_store.SECRET_COLUMNS]
    + [f"system_settings.{key}" for key in sorted(secret_store.SECRET_SETTING_KEYS)]
)
SecretFieldId = Literal[SECRET_FIELD_IDS]  # type: ignore[valid-type]


class SecretColumnStatus(BaseModel):
    id: SecretFieldId
    encrypted: int          # mit dem aktiven Schluessel lesbar
    encrypted_old: int      # nur mit einem Zusatzschluessel lesbar (Umschluesselung beim naechsten Start)
    plaintext: int          # Klartext (ohne Praefix enc:v1:)
    unreadable: int         # Praefix, aber mit keinem Schluessel lesbar
    empty: int              # NULL oder ""


class UnreadableItem(BaseModel):
    kind: Literal["server", "webhook", "setting", "user_totp"]
    field: SecretFieldId
    id: Optional[int] = None        # Zeilen-ID (Server/Webhook/Benutzer); Settings: None
    name: Optional[str] = None      # Servername, Webhook-Name, Benutzername bzw. Setting-Key
    owner: Optional[str] = None     # nur Webhooks: Benutzername des Besitzers


class SecretsStartupInfo(BaseModel):
    at: str
    key_generated: bool
    migrated: int
    rotated: int


class SecretsStatusOut(BaseModel):
    mode: Literal["encrypted", "plaintext_fallback"]
    fallback_reason: Optional[Literal["key_file_unwritable", "key_file_unreadable", "key_file_invalid"]] = None
    key_source: Optional[Literal["env", "file", "generated"]] = None
    key_file: str
    key_file_exists: bool
    key_fingerprint: Optional[str] = None
    decrypt_only_keys: int
    health: Literal["ok", "warning", "error"]
    issues: list[str]
    columns: list[SecretColumnStatus]
    unreadable: list[UnreadableItem]
    last_startup: Optional[SecretsStartupInfo] = None


@router.get("/secrets/status", response_model=SecretsStatusOut)
async def get_secrets_status(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Status der Verschluesselung gespeicherter Geheimnisse (Live-Scan, nie Werte)."""
    return await secret_store.collect_status(db)
