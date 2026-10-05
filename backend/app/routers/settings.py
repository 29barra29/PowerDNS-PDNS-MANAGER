"""API-Routen fuer Systemeinstellungen und Server-Konfiguration.

Alle Endpunkte ausser dem oeffentlichen ``GET /settings/app-info`` verlangen einen Admin mit
Browser-Session (``get_admin_session_user``, F14 3.10): mit einem API-Token lassen sich weder
PowerDNS-API-Keys auslesen noch SMTP-, Captcha-, Branding- oder ACME-Einstellungen aendern.
Schreibende Handler nutzen ``DbWrite`` (Commit vor der Antwort, Bauplan B.7); Aenderungen an
Server-Konfigurationen leeren den Zonen-Index (``zone_index.invalidate``).

Geheimnisse (F5): PowerDNS-API-Keys, SMTP-Passwort und Captcha-Secret liegen verschluesselt in der DB.
Nicht entschluesselbare Werte kommen als Platzhalter ``UNREADABLE`` (``== ""``) an und werden in den
Antworten als ``*_unreadable``/``api_key_status`` gemeldet, nie ueberschrieben, solange kein neuer Wert
kommt. Settings nur ueber ``services/system_settings.py``. Wer bei SMTP ein Zielfeld (Host, Port,
Benutzer, Verschluesselung) aendert, muss das Passwort neu eingeben (``guard_secret_retarget`` [S3], in
PUT und Test); ``SecretReentryRequired`` bildet main.py auf 400 ab.
Den Status der Verschluesselung liefert ``routers/settings_secrets.py``.
"""
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, Body, HTTPException, Depends, UploadFile, File
from starlette.concurrency import run_in_threadpool
from app.core.timeutil import iso_utc
from app.services.audit import write_audit
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from sqlalchemy import select

from app.core.database import DbRead, DbWrite
from app.core.auth import get_admin_session_user
from app.core.secrets import is_unreadable
from app.core.secret_mask import (
    SECRET_MASK,
    SMTP_TARGET_FIELDS,
    guard_secret_retarget,
    pick_targets,
    secret_input_action,
)
from app.models.models import User, ServerConfig
from app.services import zone_index
from app.services.system_settings import get_settings, set_settings
from app.services.pdns_client import (
    pdns_manager,
    PowerDNSClient,
    STATUS_PROBE_TIMEOUT,
    ZONES_LIST_PROBE_TIMEOUT,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


# ========================
# App Info
# ========================
# Felder von PUT /settings/app-info (= system_settings-Keys), Reihenfolge wie im Formular.
APP_INFO_KEYS = (
    "app_name",
    "registration_enabled",
    "forgot_password_enabled",
    "app_base_url",
    "app_tagline",
    "app_creator",
    "app_logo_url",
)
# Hochgeladene Logos liegen als uploads/custom-logo.<ext> im festen Upload-Verzeichnis.
CUSTOM_LOGO_URL_PREFIX = "/uploads/custom-logo."
CUSTOM_LOGO_GLOB = "custom-logo.*"


class AppInfoUpdate(BaseModel):
    app_name: str = Field(..., min_length=1, max_length=100, description="Name der Anwendung")
    # None = nicht aendern, "" (auch nur Leerzeichen) = leeren (F8 3.4)
    app_base_url: Optional[str] = Field(None, max_length=500, description="Öffentliche Basis-URL für E-Mail-Links (nur Admin); leer = entfernen")
    registration_enabled: Optional[bool] = Field(None, description="Registrierung auf der Login-Seite erlauben")
    forgot_password_enabled: Optional[bool] = Field(None, description="Passwort vergessen-Link anzeigen und erlauben")
    app_tagline: Optional[str] = Field(None, max_length=200, description="Kurzer Footer-Text auf Login-Seiten")
    app_creator: Optional[str] = Field(None, max_length=200, description="Creator-/Branding-Hinweis unter dem Footer")
    app_logo_url: Optional[str] = Field(None, max_length=500, description="URL zum Logo für Login-/Setup-Seiten; leer = Logo entfernen")

    @field_validator("app_base_url")
    @classmethod
    def _validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        # Wir verhindern, dass jemand https://evil.example/?phishing= als app_base_url speichert
        # und so den Reset-Link in E-Mails kapert. Erlaubt: http(s)://host[:port][/path?...]
        if v is None:
            return v
        v = v.strip()
        if not v:
            return ""  # leeren (nicht None: None hiesse "nicht aendern")
        if any(c in v for c in ("\r", "\n", "\t", " ")):
            raise ValueError("Basis-URL darf keine Leer-/Steuerzeichen enthalten")
        try:
            parsed = urlparse(v)
        except Exception:
            raise ValueError("Ungültige Basis-URL")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Basis-URL muss http(s)://host[:port] enthalten")
        return v


def _uploads_dir() -> Path:
    """Festes Upload-Verzeichnis (static_new/uploads, Volume backend_uploads) – nie aus Nutzereingaben."""
    return Path(__file__).resolve().parent.parent / "static_new" / "uploads"


def _remove_custom_logo_files(uploads_dir: Path) -> int:
    """Loescht ``custom-logo.*`` im Upload-Verzeichnis; Fehler nur als Warnung. Rueckgabe: Anzahl geloeschter Dateien."""
    removed = 0
    try:
        candidates = sorted(uploads_dir.glob(CUSTOM_LOGO_GLOB))
    except OSError as exc:
        logger.warning("Logo-Dateien in %s nicht lesbar: %s", uploads_dir, exc)
        return 0
    for path in candidates:
        try:
            if path.is_dir() and not path.is_symlink():
                continue
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Logo-Datei %s konnte nicht geloescht werden: %s", path.name, exc)
    return removed


@router.get("/app-info", include_in_schema=False)
async def get_app_info(db: DbRead):
    """Get *public* app info: name, version, branding, auth feature flags.

    Admin-only data (z.B. INSTALL_PATH, app_base_url für E-Mail-Links) wird hier NICHT mehr ausgeliefert,
    sondern unter /settings/admin-info, der eine eingeloggte Admin-Sitzung verlangt.
    """
    from app.core.config import settings

    from app.services import captcha as captcha_service

    rows = await get_settings(db, (
        "app_name",
        "registration_enabled",
        "forgot_password_enabled",
        "app_tagline",
        "app_creator",
        "app_logo_url",
        captcha_service.KEY_PROVIDER,
        captcha_service.KEY_SITE_KEY,
    ))

    captcha_provider = (rows.get(captcha_service.KEY_PROVIDER) or captcha_service.PROVIDER_NONE).strip().lower()
    if captcha_provider not in captcha_service.PROVIDERS:
        captcha_provider = captcha_service.PROVIDER_NONE
    captcha_site_key = (rows.get(captcha_service.KEY_SITE_KEY) or "").strip()
    # Wenn kein Site-Key gepflegt ist, ist Captcha effektiv aus - sonst koennte
    # der Browser nur ein leeres Widget rendern und der Login waere blockiert.
    if not captcha_site_key:
        captcha_provider = captcha_service.PROVIDER_NONE

    return {
        "app_name": rows.get("app_name") or settings.APP_NAME,
        "app_version": settings.APP_VERSION,
        "registration_enabled": (rows.get("registration_enabled") or "false").lower() == "true",
        "forgot_password_enabled": (rows.get("forgot_password_enabled") or "false").lower() == "true",
        "app_tagline": (rows.get("app_tagline") or "").strip() or "PowerDNS Admin Panel",
        "app_creator": (rows.get("app_creator") or "").strip() or "Created by GemTec Games • Barra",
        "app_logo_url": (rows.get("app_logo_url") or "").strip() or None,
        "default_language": (settings.DEFAULT_LANGUAGE or "").strip() or "de",
        # Public Captcha-Info - das Secret bleibt im Backend.
        "captcha_provider": captcha_provider,
        "captcha_site_key": captcha_site_key,
    }


@router.get("/admin-info")
async def get_admin_info(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Liefert sensible/operative Felder (INSTALL_PATH, app_base_url) – nur für Admins."""
    from app.core.config import settings

    rows = await get_settings(db, ("app_base_url",))
    base_url = (rows.get("app_base_url") or "").strip()

    return {
        "install_path": (settings.INSTALL_PATH or "").strip() or None,
        "app_base_url": base_url or None,
    }


def _app_info_values(data: AppInfoUpdate) -> dict[str, str]:
    """Zu speichernde Werte (nur mitgeschickte Felder; None = nicht aendern)."""
    values: dict[str, str] = {"app_name": data.app_name}
    if data.registration_enabled is not None:
        values["registration_enabled"] = "true" if data.registration_enabled else "false"
    if data.forgot_password_enabled is not None:
        values["forgot_password_enabled"] = "true" if data.forgot_password_enabled else "false"
    for key in ("app_base_url", "app_tagline", "app_creator", "app_logo_url"):
        val = getattr(data, key)
        if val is not None:
            values[key] = val.strip()
    return values


@router.put("/app-info")
async def update_app_info(
    db: DbWrite,
    data: AppInfoUpdate,
    admin: User = Depends(get_admin_session_user),
):
    """App-Name, Branding und Auth-Schalter speichern.

    ``app_base_url: ""`` leert die Basis-URL, ``app_logo_url: ""`` entfernt das Logo (ein hochgeladenes
    ``custom-logo.*`` wird dabei geloescht). Audit ``APP_INFO_UPDATE`` nur mit Feldnamen, nie Werten.
    """
    values = _app_info_values(data)
    old = await get_settings(db, values.keys())
    changed = sorted(key for key, val in values.items() if (old.get(key) or "") != val)
    if not changed:
        return {"message": "Einstellungen aktualisiert"}
    await set_settings(db, {key: values[key] for key in changed})

    removed = 0
    old_logo = (old.get("app_logo_url") or "").strip()
    if "app_logo_url" in changed and values["app_logo_url"] == "" and old_logo.startswith(CUSTOM_LOGO_URL_PREFIX):
        removed = _remove_custom_logo_files(_uploads_dir())

    await write_audit(db, "APP_INFO_UPDATE", "settings", "app-info", user_id=admin.id,
                      details={"changed": changed, "logo_files_removed": removed})
    return {"message": "Einstellungen aktualisiert"}


def _detect_image_ext(blob: bytes) -> str | None:
    """Wir vertrauen nicht dem Client-Content-Type. Prüfung anhand der ersten Bytes (Magic Numbers)."""
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    head = blob[:512].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<svg"):
        return ".svg"
    return None


@router.post("/app-logo")
async def upload_app_logo(
    db: DbWrite,
    file: UploadFile = File(...),
    admin: User = Depends(get_admin_session_user),
):
    """Upload custom logo for login/setup pages (admin only)."""
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo ist zu groß (max. 2 MB)")

    ext = _detect_image_ext(content)
    if not ext:
        raise HTTPException(status_code=400, detail="Nur PNG, JPG, WEBP oder SVG erlaubt")

    uploads_dir = _uploads_dir()
    uploads_dir.mkdir(parents=True, exist_ok=True)
    _remove_custom_logo_files(uploads_dir)

    filename = f"custom-logo{ext}"
    out = uploads_dir / filename
    out.write_bytes(content)
    logo_url = f"/uploads/{filename}"

    await set_settings(db, {"app_logo_url": logo_url})
    await write_audit(db, "APP_LOGO_UPLOAD", "settings", "app-logo", user_id=admin.id,
                      details={"ext": ext, "bytes": len(content)})
    logger.info("Logo hochgeladen von Admin '%s' (%s, %d Bytes)", admin.username, ext, len(content))

    return {"message": "Logo hochgeladen", "app_logo_url": logo_url}

# ========================
# Schemas
# ========================
class ServerConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Eindeutiger Servername, z.B. server1")
    display_name: Optional[str] = Field(None, description="Anzeigename")
    url: str = Field(..., description="PowerDNS API URL, z.B. http://192.168.1.10:8081")
    api_key: str = Field(..., min_length=1, max_length=500, description="PowerDNS API Key")
    description: Optional[str] = None
    allow_writes: Optional[bool] = Field(True, description="Zonen/Änderungen auf diesem Server speichern. Bei gemeinsamer DB nur bei einem Server aktivieren.")


class ServerConfigUpdate(BaseModel):
    display_name: Optional[str] = None
    url: Optional[str] = None
    # None/leer = Bestand behalten (auch einen nicht lesbaren Key: der Server bleibt dann ungeladen)
    api_key: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = None
    is_active: Optional[bool] = None
    allow_writes: Optional[bool] = None


class TestConnectionRequest(BaseModel):
    url: str = Field(..., description="PowerDNS API URL")
    api_key: str = Field(..., max_length=500, description="PowerDNS API Key")


# ========================
# Server Configuration CRUD
# ========================
REVEAL_UNREADABLE_DETAIL = (
    "Der API-Key dieses Servers kann nicht entschlüsselt werden (Schlüssel fehlt oder passt nicht). "
    "Bitte den API-Key neu eintragen."
)
REVEAL_EMPTY_DETAIL = "Für diesen Server ist kein API-Key gespeichert."


def _api_key_status(cfg: ServerConfig) -> str:
    """``set`` | ``missing`` | ``unreadable`` (F5 3.2); nie der Wert selbst."""
    if is_unreadable(cfg.api_key):
        return "unreadable"
    return "set" if (cfg.api_key or "").strip() else "missing"


def _sync_live_client(cfg: ServerConfig) -> None:
    """Live-Client an die DB-Zeile angleichen.

    Aktiv und Key lesbar -> Client (neu) anlegen. Aktiv, aber Key unlesbar oder leer -> Server bleibt
    konfiguriert, aber ungeladen (``pdns_manager.unloaded``; Fan-out meldet "skipped (not loaded: …)",
    /health lokal ``servers_not_loaded``) [D4]. Inaktiv -> Client und Markierung entfernen.
    """
    if not cfg.is_active:
        pdns_manager.remove_server(cfg.name)
        return
    status = _api_key_status(cfg)
    if status == "set":
        pdns_manager.update_server(cfg.name, cfg.url, cfg.api_key)
        return
    reason = "api key unreadable" if status == "unreadable" else "api key empty"
    pdns_manager.mark_unloaded(cfg.name, reason)
    logger.error(
        "PowerDNS-Server '%s' nicht geladen: API-Key %s – bitte unter Einstellungen -> Server neu eintragen.",
        cfg.name, "nicht entschluesselbar" if status == "unreadable" else "leer",
    )


async def _enrich_server_config_row(cfg: ServerConfig) -> dict:
    """DB-Zeile + optional Live-Status mit kurzen Timeouts.

    Sicherheit: Der vollständige API-Key wird hier NIE ausgeliefert, nur eine kurze Maske.
    Zum Bearbeiten kann der Admin den Key über `GET /settings/servers/{id}/api-key` einmalig anfordern.
    ``api_key_status``: ``set`` | ``missing`` | ``unreadable`` (nicht entschluesselbar -> Maske "", has_api_key false).
    """
    is_online = False
    version = None
    zone_count = None
    if cfg.is_active and cfg.name in pdns_manager.get_all_clients():
        try:
            client = pdns_manager.get_client(cfg.name)
            info = await client.get_server_info(timeout=STATUS_PROBE_TIMEOUT)
            zones = await client.list_zones(timeout=ZONES_LIST_PROBE_TIMEOUT)
            is_online = True
            version = info.get("version", "")
            zone_count = len(zones) if zones else 0
        except Exception:
            pass

    return {
        "id": cfg.id,
        "name": cfg.name,
        "display_name": cfg.display_name,
        "url": cfg.url,
        "api_key": (cfg.api_key[:4] + "…") if cfg.api_key else "",  # Maskiert (auch length wird nicht offengelegt)
        "has_api_key": bool(cfg.api_key),
        "api_key_status": _api_key_status(cfg),
        "description": cfg.description,
        "is_active": cfg.is_active,
        "allow_writes": getattr(cfg, "allow_writes", True),
        "is_online": is_online,
        "version": version,
        "zone_count": zone_count,
        "created_at": iso_utc(cfg.created_at),
        "updated_at": iso_utc(cfg.updated_at),
    }


@router.get("/servers")
async def list_server_configs(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """List all server configurations from database."""
    result = await db.execute(select(ServerConfig).order_by(ServerConfig.sort_order, ServerConfig.name))
    configs = result.scalars().all()

    servers = await asyncio.gather(*[_enrich_server_config_row(cfg) for cfg in configs])
    return {"servers": list(servers)}


@router.post("/servers", status_code=201)
async def add_server_config(
    db: DbWrite,
    data: ServerConfigCreate,
    admin: User = Depends(get_admin_session_user),
):
    """Add a new PowerDNS server configuration."""
    # Check name unique
    result = await db.execute(select(ServerConfig).where(ServerConfig.name == data.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Server '{data.name}' existiert bereits")
    
    cfg = ServerConfig(
        name=data.name,
        display_name=data.display_name or data.name,
        url=data.url.rstrip("/"),
        api_key=data.api_key,
        description=data.description,
        is_active=True,
        allow_writes=getattr(data, "allow_writes", True),
    )
    db.add(cfg)
    await db.flush()

    # Live-Verbindung hinzufuegen (ein Key aus Leerzeichen wird nicht geladen)
    _sync_live_client(cfg)
    zone_index.invalidate()
    
    logger.info(f"Server config '{data.name}' added by admin '{admin.username}'")
    await write_audit(db, "SERVER_CREATE", "server_config", cfg.name, user_id=admin.id, server_name=cfg.name,
                      details={"url": cfg.url, "allow_writes": bool(cfg.allow_writes)})
    return {"message": f"Server '{data.name}' hinzugefuegt", "id": cfg.id}


@router.put("/servers/{server_id}")
async def update_server_config(
    db: DbWrite,
    server_id: int,
    data: ServerConfigUpdate,
    admin: User = Depends(get_admin_session_user),
):
    """Update an existing server configuration."""
    result = await db.execute(select(ServerConfig).where(ServerConfig.id == server_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Server-Konfiguration nicht gefunden")
    
    before = {"display_name": cfg.display_name, "url": cfg.url, "api_key": cfg.api_key,
              "description": cfg.description, "is_active": cfg.is_active, "allow_writes": cfg.allow_writes}
    if data.display_name is not None:
        cfg.display_name = data.display_name
    if data.url is not None:
        cfg.url = data.url.rstrip("/")
    if data.api_key is not None and data.api_key.strip():
        # Leerer/whitespace-Wert wird ignoriert -> Bestand bleibt erhalten (verhindert,
        # dass das Frontend versehentlich beim Speichern einer Edit-Form ohne Reveal den Key löscht).
        cfg.api_key = data.api_key
    if data.description is not None:
        cfg.description = data.description
    if data.is_active is not None:
        cfg.is_active = data.is_active
    if data.allow_writes is not None:
        cfg.allow_writes = data.allow_writes

    await db.flush()

    # Live-Verbindung aktualisieren (unlesbarer/leerer Key -> konfiguriert, aber nicht geladen)
    _sync_live_client(cfg)
    zone_index.invalidate()
    
    logger.info(f"Server config '{cfg.name}' updated by admin '{admin.username}'")
    changed = {k: ({"from": before[k], "to": getattr(cfg, k)} if k != "api_key" else "changed")
               for k in before if before[k] != getattr(cfg, k)}
    await write_audit(db, "SERVER_UPDATE", "server_config", cfg.name, user_id=admin.id, server_name=cfg.name,
                      details={"changed": changed})
    return {"message": f"Server '{cfg.name}' aktualisiert"}


@router.delete("/servers/{server_id}")
async def delete_server_config(
    db: DbWrite,
    server_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Delete a server configuration."""
    result = await db.execute(select(ServerConfig).where(ServerConfig.id == server_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Server-Konfiguration nicht gefunden")
    
    server_name = cfg.name
    
    # Live-Verbindung entfernen
    pdns_manager.remove_server(server_name)
    zone_index.invalidate()
    
    await db.delete(cfg)
    await db.flush()
    
    logger.info(f"Server config '{server_name}' deleted by admin '{admin.username}'")
    await write_audit(db, "SERVER_DELETE", "server_config", server_name, user_id=admin.id, server_name=server_name)
    return {"message": f"Server '{server_name}' geloescht"}


@router.get("/servers/{server_id}/api-key")
async def reveal_server_api_key(
    db: DbWrite,
    server_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Gibt den vollständigen API-Key eines Servers genau einmal an einen eingeloggten Admin zurück.

    Wird ins Audit-Log geschrieben, damit die Aufdeckung nachvollziehbar ist. Nicht entschluesselbarer
    Key -> 409 + Fehler-Audit; kein Key gespeichert -> 409 ohne Audit (F5 3.2).
    """
    result = await db.execute(select(ServerConfig).where(ServerConfig.id == server_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Server-Konfiguration nicht gefunden")

    if is_unreadable(cfg.api_key):
        # Fehler-Audit (eigene Session, ueberlebt den Rollback der Request-Session)
        await write_audit(db, "REVEAL_API_KEY", "server_config", cfg.name, user_id=admin.id, server_name=cfg.name,
                          status="error", error_message="API-Key nicht entschluesselbar")
        raise HTTPException(status_code=409, detail=REVEAL_UNREADABLE_DETAIL)
    if not cfg.api_key:
        raise HTTPException(status_code=409, detail=REVEAL_EMPTY_DETAIL)

    # Audit vor der Antwort committet (DbWrite): ohne Eintrag kein Klartext-Key
    await write_audit(db, "REVEAL_API_KEY", "server_config", cfg.name, user_id=admin.id, server_name=cfg.name)
    logger.info(f"API key revealed for server '{cfg.name}' by admin '{admin.username}'")
    return {"id": cfg.id, "name": cfg.name, "api_key": cfg.api_key}


# ========================
# Test Connection
# ========================
@router.post("/servers/test")
async def test_connection(
    data: TestConnectionRequest,
    admin: User = Depends(get_admin_session_user),
):
    """Test connection to a PowerDNS server. Returns server info if successful."""
    url = data.url.rstrip("/")
    headers = {
        "X-API-Key": data.api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{url}/api/v1/servers/localhost",
                headers=headers,
            )
            
            if response.status_code == 401:
                return {
                    "success": False,
                    "error": "API-Key ungueltig (401 Unauthorized)",
                }
            
            if response.status_code == 403:
                return {
                    "success": False,
                    "error": "Zugriff verweigert (403 Forbidden)",
                }
            
            if response.status_code >= 400:
                return {
                    "success": False,
                    "error": f"Server-Fehler: HTTP {response.status_code}",
                }
            
            info = response.json()
            
            # Auch Zonen zaehlen
            zones_resp = await client.get(
                f"{url}/api/v1/servers/localhost/zones",
                headers=headers,
            )
            zone_count = len(zones_resp.json()) if zones_resp.status_code == 200 else 0
            
            return {
                "success": True,
                "server_info": {
                    "version": info.get("version", "unbekannt"),
                    "type": info.get("type", ""),
                    "daemon_type": info.get("daemon_type", ""),
                    "zone_count": zone_count,
                },
            }
    except httpx.ConnectError:
        return {
            "success": False,
            "error": f"Verbindung zu {url} fehlgeschlagen. Ist der Server erreichbar?",
        }
    except httpx.TimeoutException:
        return {
            "success": False,
            "error": f"Zeitueberschreitung bei {url}. Server antwortet nicht.",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Unbekannter Fehler: {str(e)}",
        }


# ========================
# SMTP Settings
# ========================
SMTP_TARGETS = tuple(f for f in SMTP_TARGET_FIELDS if f in ("host", "port", "username", "encryption"))
SMTP_PASSWORD_UNREADABLE_ERROR = (
    "SMTP-Passwort kann nicht entschlüsselt werden – bitte unter Einstellungen → SMTP neu eintragen."
)
_SECRET_STATE = {"keep": "kept", "clear": "cleared", "set": "set"}


class SmtpSettings(BaseModel):
    host: str = Field(default="", description="SMTP Server Hostname")
    port: int = Field(default=587, description="SMTP Port")
    username: str = Field(default="", description="SMTP Benutzername")
    # None oder Maske = gespeichertes Passwort behalten, "" = loeschen, sonst neuer Wert (F8 3.6).
    # Wer host/port/username/encryption aendert, muss das Passwort neu eingeben [S3].
    password: Optional[str] = Field(default=None, max_length=500, description="SMTP Passwort (leer lassen = behalten)")
    from_email: str = Field(default="", description="Absender E-Mail")
    from_name: str = Field(default="PDNS Manager", description="Absender Name")
    encryption: str = Field(default="starttls", description="Verschlüsselung: none, starttls, ssl")
    enabled: bool = Field(default=False, description="SMTP aktiviert")


class SmtpTestRequest(BaseModel):
    """Optionale, noch nicht gespeicherte Formularwerte fuer den Verbindungstest.

    Fehlende Felder = gespeicherter Wert. Das gespeicherte Passwort wird nur verwendet, wenn kein
    Zielfeld (host/port/username/encryption) abweicht – sonst 400 ``secret_reentry_required`` [S3].
    """

    host: Optional[str] = Field(default=None, max_length=255)
    port: Optional[int] = None
    username: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, max_length=500)
    encryption: Optional[str] = Field(default=None, max_length=20)


def _smtp_password_state(stored: object) -> bool:
    """Ist ein Passwort gespeichert (auch ein nicht entschluesselbares)?"""
    return bool(stored) or is_unreadable(stored)


async def _audit_smtp_test(db, admin: User, settings: dict, result: dict, *, kind: str, unsaved: bool) -> None:
    ok = bool(result.get("success"))
    await write_audit(
        db, "SMTP_TEST", "settings", "smtp", user_id=admin.id,
        details={
            "kind": kind,
            "host": (settings.get("host") or "")[:255] or None,
            "port": settings.get("port"),
            "success": ok,
            "unsaved_values": unsaved,
        },
        status="success" if ok else "error",
        error_message=None if ok else "SMTP-Test fehlgeschlagen",
    )


@router.get("/smtp")
async def get_smtp_settings(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Aktuelle SMTP-Konfiguration; das Passwort nur als Maske (``password_unreadable`` = nicht entschluesselbar)."""
    from app.services.email_service import get_smtp_settings as _get
    settings = await _get(db)
    password = settings.get("password")
    settings["password_set"] = bool(password)
    settings["password"] = SECRET_MASK if password else ""
    settings["password_unreadable"] = is_unreadable(password)
    return settings


@router.put("/smtp")
async def update_smtp_settings(
    db: DbWrite,
    data: SmtpSettings,
    admin: User = Depends(get_admin_session_user),
):
    """SMTP-Konfiguration speichern.

    ``password``: None/Maske = behalten (wird weder gelesen noch neu geschrieben), "" = loeschen, sonst neu.
    Aendert sich ein Zielfeld, muss das Passwort neu eingegeben werden (``guard_secret_retarget``).
    """
    from app.services.email_service import save_smtp_settings, get_smtp_settings as _get

    old = await _get(db)
    save_data = data.model_dump()
    guard_secret_retarget(
        targets_before=pick_targets(old, SMTP_TARGETS),
        targets_after=pick_targets(save_data, SMTP_TARGETS),
        secret_in=data.password,
        secret_stored=bool(old.get("password")),
    )
    action = secret_input_action(data.password)
    if action == "keep":
        save_data["password"] = None  # nicht anfassen (auch einen unlesbaren Chiffretext nicht)
    elif action == "clear":
        save_data["password"] = ""
    state = _SECRET_STATE[action]
    if action == "clear" and not _smtp_password_state(old.get("password")):
        state = "kept"  # es gab nichts zu loeschen

    save_data["enabled"] = str(save_data["enabled"]).lower()
    await save_smtp_settings(db, save_data)
    details = {k: save_data.get(k) for k in ("host", "port", "encryption", "username", "from_email", "enabled")}
    details["password_changed"] = state
    await write_audit(db, "SMTP_UPDATE", "settings", "smtp", user_id=admin.id, details=details)

    return {"message": "SMTP-Einstellungen gespeichert"}


@router.post("/smtp/test")
async def test_smtp(
    db: DbWrite,
    admin: User = Depends(get_admin_session_user),
    data: Optional[SmtpTestRequest] = Body(default=None),
):
    """Verbindungstest. Ohne Body mit den gespeicherten Werten; mit Body mit den uebergebenen
    (noch nicht gespeicherten) Werten. Audit ``SMTP_TEST`` (Host/Port/Ergebnis, nie das Passwort)."""
    from app.services.email_service import get_smtp_settings as _get, test_smtp_connection

    stored = await _get(db)
    settings = dict(stored)
    unsaved = False
    if data is not None:
        overrides = {k: v for k, v in data.model_dump(exclude={"password"}).items() if v is not None}
        candidate = {**stored, **overrides}
        guard_secret_retarget(
            targets_before=pick_targets(stored, SMTP_TARGETS),
            targets_after=pick_targets(candidate, SMTP_TARGETS),
            secret_in=data.password,
            secret_stored=bool(stored.get("password")),
        )
        action = secret_input_action(data.password)
        settings = candidate
        if action == "set":
            settings["password"] = data.password
        elif action == "clear":
            settings["password"] = ""
        unsaved = bool(overrides) or action != "keep"

    result = await test_smtp_connection(settings)
    await _audit_smtp_test(db, admin, settings, result, kind="connection", unsaved=unsaved)
    return result


class TestEmailRequest(BaseModel):
    to_email: str = Field(..., description="E-Mail-Adresse für Test")


@router.post("/smtp/test-email")
async def send_test_email(
    db: DbWrite,
    data: TestEmailRequest,
    admin: User = Depends(get_admin_session_user),
):
    """Send a test email to verify SMTP works end-to-end (gespeicherte Einstellungen)."""
    from app.services.email_service import get_smtp_settings as _get, send_email
    from app.services.email_templates import pick_language, test_email
    from app.core.config import settings as app_settings

    smtp_settings = await _get(db)

    lang = pick_language(admin.preferred_language, app_settings.DEFAULT_LANGUAGE)
    subject, body_html, body_text = test_email(lang)

    try:
        await run_in_threadpool(send_email, smtp_settings, data.to_email, subject, body_html, body_text)
        msg_de = f"Test-E-Mail an {data.to_email} gesendet!"
        msg_en = f"Test email sent to {data.to_email}!"
        result = {"success": True, "message": msg_de if lang == "de" else msg_en}
    except Exception as e:
        result = {"success": False, "error": str(e)}
    await _audit_smtp_test(db, admin, smtp_settings, result, kind="email", unsaved=False)
    return result


# ========================
# Captcha Settings
# ========================
CAPTCHA_SECRET_UNREADABLE_ERROR = "Captcha-Secret kann nicht entschlüsselt werden – bitte neu eintragen."


class CaptchaSettings(BaseModel):
    provider: str = Field(default="none", description="none | turnstile | hcaptcha | recaptcha")
    site_key: str = Field(default="", max_length=500, description="Public Site-Key (im Browser sichtbar)")
    # secret_key=None oder Maske heisst "nicht aendern" (Pattern wie bei den PowerDNS-API-Keys),
    # secret_key="" loescht den Schluessel.
    secret_key: Optional[str] = Field(default=None, max_length=500, description="Privater Schluessel - leer lassen, um den bestehenden zu behalten")

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        from app.services.captcha import PROVIDERS
        v = (v or "none").strip().lower()
        if v not in PROVIDERS:
            raise ValueError(f"Unbekannter Provider. Erlaubt: {sorted(PROVIDERS)}")
        return v


@router.get("/captcha")
async def get_captcha_settings(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Aktuelle Captcha-Konfiguration. Das Secret wird maskiert (``secret_key_unreadable`` = nicht entschluesselbar)."""
    from app.services.captcha import get_captcha_settings as _get
    s = await _get(db)
    return {
        "provider": s["provider"],
        "site_key": s["site_key"],
        "secret_key": SECRET_MASK if s["secret_key"] else "",
        "secret_key_set": bool(s["secret_key"]),
        "secret_key_unreadable": bool(s["secret_unreadable"]),
    }


@router.put("/captcha")
async def update_captcha_settings(
    db: DbWrite,
    data: CaptchaSettings,
    admin: User = Depends(get_admin_session_user),
):
    """Captcha-Provider und Keys speichern. Wenn das Secret maskiert oder None ist,
    wird das gespeicherte Secret beibehalten; "" loescht es. Audit ``CAPTCHA_UPDATE`` (ohne Werte)."""
    from app.services.captcha import get_captcha_settings as _get, save_captcha_settings

    # Provider-Wechsel bei behaltenem Secret: das alte Secret ist evtl. ungueltig – bewusster
    # Trade-off; die Verify-Endpunkte der Provider sind fest (kein frei waehlbares Ziel).
    action = secret_input_action(data.secret_key)
    new_secret: Optional[str] = None if action == "keep" else ("" if action == "clear" else data.secret_key)
    old = await _get(db)
    state = _SECRET_STATE[action]
    if action == "clear" and not (old["secret_key"] or old["secret_unreadable"]):
        state = "kept"

    await save_captcha_settings(
        db,
        provider=data.provider,
        site_key=data.site_key,
        secret_key=new_secret,
    )
    await write_audit(db, "CAPTCHA_UPDATE", "settings", "captcha", user_id=admin.id, details={
        "provider": data.provider,
        "site_key_set": bool((data.site_key or "").strip()),
        "secret_key_changed": state,
    })
    return {"message": "Captcha-Einstellungen gespeichert"}


class CaptchaTestRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4096, description="Vom Browser geliefertes Captcha-Token")


@router.post("/captcha/test")
async def test_captcha(
    db: DbWrite,
    data: CaptchaTestRequest,
    admin: User = Depends(get_admin_session_user),
):
    """Verifiziert ein vom Browser-Widget geliefertes Test-Token gegen die Provider-API.
    So sieht der Admin sofort, ob Site-Key + Secret-Key zusammenpassen."""
    from app.services.captcha import (
        get_captcha_settings as _get,
        verify_captcha_token,
        PROVIDER_NONE,
    )

    s = await _get(db)
    if s["provider"] == PROVIDER_NONE:
        return {"success": False, "error": "Kein Captcha-Provider konfiguriert"}
    if s["secret_unreadable"]:
        return {"success": False, "error": CAPTCHA_SECRET_UNREADABLE_ERROR}
    if not s["secret_key"]:
        return {"success": False, "error": "Kein Secret-Key gespeichert"}

    ok, error = await verify_captcha_token(
        provider=s["provider"],
        secret_key=s["secret_key"],
        token=data.token,
    )
    if ok:
        return {"success": True, "message": "Captcha-Konfiguration funktioniert"}
    return {"success": False, "error": error or "Captcha-Pruefung fehlgeschlagen"}


# ========================
# Welcome Email Settings
# ========================
class WelcomeEmailSettings(BaseModel):
    enabled: bool = Field(default=False, description="Welcome-Mail nach Registrierung versenden")
    subject: str = Field(default="", max_length=200, description="E-Mail-Betreff (Platzhalter erlaubt)")
    body: str = Field(default="", max_length=20000, description="E-Mail-Inhalt als Text/HTML (Platzhalter erlaubt)")


@router.get("/welcome-email")
async def get_welcome_email_settings(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Welcome-Mail-Einstellungen + Default-Templates fuer leere Felder."""
    from app.services.email_service import get_welcome_email_settings as _get
    from app.services.email_templates import welcome_email_default, pick_language
    from app.core.config import settings as app_settings

    s = await _get(db)
    lang = pick_language(admin.preferred_language, app_settings.DEFAULT_LANGUAGE)
    default_subject, default_body = welcome_email_default(lang)
    return {
        "enabled": s["enabled"],
        "subject": s["subject"],
        "body": s["body"],
        "default_subject": default_subject,
        "default_body": default_body,
        "placeholders": ["username", "display_name", "email", "app_name", "login_url"],
    }


@router.put("/welcome-email")
async def update_welcome_email_settings(
    db: DbWrite,
    data: WelcomeEmailSettings,
    admin: User = Depends(get_admin_session_user),
):
    """Welcome-Mail-Einstellungen speichern. Wenn enabled aber Subject/Body leer ->
    werden die Defaults der Admin-Sprache als Initialwerte gespeichert."""
    from app.services.email_service import save_welcome_email_settings as _save
    from app.services.email_templates import welcome_email_default, pick_language
    from app.core.config import settings as app_settings

    subject = (data.subject or "").strip()
    body = (data.body or "").strip()
    if data.enabled and (not subject or not body):
        lang = pick_language(admin.preferred_language, app_settings.DEFAULT_LANGUAGE)
        d_subject, d_body = welcome_email_default(lang)
        if not subject:
            subject = d_subject
        if not body:
            body = d_body

    await _save(db, enabled=data.enabled, subject=subject, body=body)
    return {"message": "Welcome-Mail-Einstellungen gespeichert"}


@router.post("/welcome-email/test")
async def send_welcome_test_email(
    db: DbWrite,
    data: TestEmailRequest,
    admin: User = Depends(get_admin_session_user),
):
    """Sendet die Welcome-Mail testweise an die angegebene Adresse - mit dem Admin
    als Beispiel-Empfaenger fuer die Platzhalter."""
    from app.services.email_service import (
        get_smtp_settings as _smtp,
        get_welcome_email_settings as _welcome,
        send_email,
    )
    from app.services.email_templates import render_welcome_email, pick_language
    from app.core.config import settings as app_settings

    smtp_settings = await _smtp(db)
    welcome_settings = await _welcome(db)

    rows = await get_settings(db, ("app_name", "app_base_url"))
    app_name = (rows.get("app_name") or app_settings.APP_NAME or "PDNS Manager").strip()
    base_url = (rows.get("app_base_url") or "").strip() or "http://localhost:5380"

    lang = pick_language(admin.preferred_language, app_settings.DEFAULT_LANGUAGE)
    subject, body_html, body_text = render_welcome_email(
        lang=lang,
        subject_template=welcome_settings["subject"],
        body_template=welcome_settings["body"],
        username=admin.username,
        display_name=admin.display_name or admin.username,
        email=data.to_email,
        app_name=app_name,
        login_url=f"{base_url.rstrip('/')}/login",
    )

    try:
        await run_in_threadpool(send_email, smtp_settings, data.to_email, subject, body_html, body_text)
        return {"success": True, "message": f"Test-Welcome-Mail an {data.to_email} gesendet"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ========================
# ACME / Auto-TLS Tokens
# ========================
class AcmeTokenCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Sprechender Name (z.B. 'smtp-server')")
    allowed_zones: list[str] = Field(..., min_length=1, description="Zonen, die der Token bedienen darf (mit oder ohne Trailing-Dot)")


def _serialize_acme_token(t) -> dict:
    """ACME-Token DB-Row -> JSON. Niemals den Plaintext exposen - der existiert
    nach der Erstellung gar nicht mehr in der DB."""
    return {
        "id": t.id,
        "name": t.name,
        "token_prefix": t.token_prefix,
        "allowed_zones": t.allowed_zones or [],
        "created_at": iso_utc(t.created_at),
        "last_used_at": iso_utc(t.last_used_at),
        "last_used_ip": t.last_used_ip,
        "is_active": bool(t.is_active),
    }


@router.get("/acme/tokens")
async def list_acme_tokens(
    db: DbRead,
    admin: User = Depends(get_admin_session_user),
):
    """Alle ACME-Tokens auflisten - Plaintext-Wert ist NICHT enthalten (nur Prefix
    zur Wiedererkennung)."""
    from app.services import acme as acme_service
    rows = await acme_service.list_tokens(db)
    return {"tokens": [_serialize_acme_token(t) for t in rows]}


@router.post("/acme/tokens", status_code=201)
async def create_acme_token(
    db: DbWrite,
    data: AcmeTokenCreate,
    admin: User = Depends(get_admin_session_user),
):
    """Erzeugt einen neuen ACME-Token. Liefert den Plaintext GENAU EINMAL zurueck -
    das UI muss den User anzeigen lassen, weil er danach nirgendwo mehr lesbar ist.
    """
    from app.services import acme as acme_service
    try:
        row, plaintext = await acme_service.create_token(
            db,
            name=data.name,
            allowed_zones=data.allowed_zones,
            created_by_id=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await write_audit(db, "ACME_TOKEN_CREATE", "acme_token", row.name, user_id=admin.id,
                      details={"token_id": row.id, "allowed_zones": list(getattr(row, "allowed_zones", None) or data.allowed_zones or [])})
    # Commit macht DbWrite vor dem Senden der Antwort: ohne gespeicherten Token kein Klartext.
    return {
        "token": _serialize_acme_token(row),
        "plaintext_token": plaintext,  # NUR HIER, einmalig
        "warning": "Dieser Token wird kein zweites Mal angezeigt - jetzt sicher abspeichern.",
    }


@router.delete("/acme/tokens/{token_id}")
async def delete_acme_token(
    db: DbWrite,
    token_id: int,
    admin: User = Depends(get_admin_session_user),
):
    """Loescht einen Token (Hard-Delete - keine Wiederverwendung moeglich)."""
    from app.services import acme as acme_service
    ok = await acme_service.delete_token(db, token_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Token nicht gefunden")
    await write_audit(db, "ACME_TOKEN_DELETE", "acme_token", str(token_id), user_id=admin.id,
                      details={"token_id": token_id})
    return {"message": "Token geloescht"}

