"""API routes for system settings and configuration management (Admin only)."""
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import get_admin_user
from app.models.models import User, ServerConfig
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
class AppInfoUpdate(BaseModel):
    app_name: str = Field(..., min_length=1, max_length=100, description="Name der Anwendung")
    app_base_url: Optional[str] = Field(None, max_length=500, description="Öffentliche Basis-URL für E-Mail-Links (nur Admin)")
    registration_enabled: Optional[bool] = Field(None, description="Registrierung auf der Login-Seite erlauben")
    forgot_password_enabled: Optional[bool] = Field(None, description="Passwort vergessen-Link anzeigen und erlauben")
    app_tagline: Optional[str] = Field(None, max_length=200, description="Kurzer Footer-Text auf Login-Seiten")
    app_creator: Optional[str] = Field(None, max_length=200, description="Creator-/Branding-Hinweis unter dem Footer")
    app_logo_url: Optional[str] = Field(None, max_length=500, description="URL zum Logo für Login-/Setup-Seiten")

    @field_validator("app_base_url")
    @classmethod
    def _validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        # Wir verhindern, dass jemand https://evil.example/?phishing= als app_base_url speichert
        # und so den Reset-Link in E-Mails kapert. Erlaubt: http(s)://host[:port][/path?...]
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None
        if any(c in v for c in ("\r", "\n", "\t", " ")):
            raise ValueError("Basis-URL darf keine Leer-/Steuerzeichen enthalten")
        try:
            parsed = urlparse(v)
        except Exception:
            raise ValueError("Ungültige Basis-URL")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Basis-URL muss http(s)://host[:port] enthalten")
        return v


@router.get("/app-info", include_in_schema=False)
async def get_app_info(db: AsyncSession = Depends(get_db)):
    """Get *public* app info: name, version, branding, auth feature flags.

    Admin-only data (z.B. INSTALL_PATH, app_base_url für E-Mail-Links) wird hier NICHT mehr ausgeliefert,
    sondern unter /settings/admin-info, der eine eingeloggte Admin-Sitzung verlangt.
    """
    from app.models.models import SystemSetting
    from app.core.config import settings

    result = await db.execute(
        select(SystemSetting.key, SystemSetting.value).where(
            SystemSetting.key.in_((
                "app_name",
                "registration_enabled",
                "forgot_password_enabled",
                "app_tagline",
                "app_creator",
                "app_logo_url",
            ))
        )
    )
    rows = {r[0]: r[1] for r in result.all()}

    return {
        "app_name": rows.get("app_name") or settings.APP_NAME,
        "app_version": settings.APP_VERSION,
        "registration_enabled": (rows.get("registration_enabled") or "false").lower() == "true",
        "forgot_password_enabled": (rows.get("forgot_password_enabled") or "false").lower() == "true",
        "app_tagline": (rows.get("app_tagline") or "").strip() or "PowerDNS Admin Panel",
        "app_creator": (rows.get("app_creator") or "").strip() or "Created by GemTec Games • Barra",
        "app_logo_url": (rows.get("app_logo_url") or "").strip() or None,
        "default_language": (settings.DEFAULT_LANGUAGE or "").strip() or "de",
    }


@router.get("/admin-info")
async def get_admin_info(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Liefert sensible/operative Felder (INSTALL_PATH, app_base_url) – nur für Admins."""
    from app.models.models import SystemSetting
    from app.core.config import settings

    result = await db.execute(
        select(SystemSetting.key, SystemSetting.value).where(
            SystemSetting.key.in_(("app_base_url",))
        )
    )
    rows = {r[0]: r[1] for r in result.all()}
    base_url = (rows.get("app_base_url") or "").strip()

    return {
        "install_path": (settings.INSTALL_PATH or "").strip() or None,
        "app_base_url": base_url or None,
    }


@router.put("/app-info")
async def update_app_info(
    data: AppInfoUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Update custom app name and auth feature toggles."""
    from app.models.models import SystemSetting

    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "app_name"))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = data.app_name
    else:
        db.add(SystemSetting(key="app_name", value=data.app_name))

    if data.registration_enabled is not None:
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == "registration_enabled"))
        s = r.scalar_one_or_none()
        if s:
            s.value = "true" if data.registration_enabled else "false"
        else:
            db.add(SystemSetting(key="registration_enabled", value="true" if data.registration_enabled else "false"))

    if data.forgot_password_enabled is not None:
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == "forgot_password_enabled"))
        s = r.scalar_one_or_none()
        if s:
            s.value = "true" if data.forgot_password_enabled else "false"
        else:
            db.add(SystemSetting(key="forgot_password_enabled", value="true" if data.forgot_password_enabled else "false"))

    if data.app_base_url is not None:
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == "app_base_url"))
        s = r.scalar_one_or_none()
        val = (data.app_base_url or "").strip() or ""
        if s:
            s.value = val
        else:
            db.add(SystemSetting(key="app_base_url", value=val))

    if data.app_tagline is not None:
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == "app_tagline"))
        s = r.scalar_one_or_none()
        val = (data.app_tagline or "").strip()
        if s:
            s.value = val
        else:
            db.add(SystemSetting(key="app_tagline", value=val))

    if data.app_creator is not None:
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == "app_creator"))
        s = r.scalar_one_or_none()
        val = (data.app_creator or "").strip()
        if s:
            s.value = val
        else:
            db.add(SystemSetting(key="app_creator", value=val))

    if data.app_logo_url is not None:
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == "app_logo_url"))
        s = r.scalar_one_or_none()
        val = (data.app_logo_url or "").strip()
        if s:
            s.value = val
        else:
            db.add(SystemSetting(key="app_logo_url", value=val))

    await db.commit()
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
    file: UploadFile = File(...),
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload custom logo for login/setup pages (admin only)."""
    from app.models.models import SystemSetting

    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Logo ist zu groß (max. 2 MB)")

    ext = _detect_image_ext(content)
    if not ext:
        raise HTTPException(status_code=400, detail="Nur PNG, JPG, WEBP oder SVG erlaubt")

    static_dir = Path(__file__).resolve().parent.parent / "static_new"
    uploads_dir = static_dir / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    for old in uploads_dir.glob("custom-logo.*"):
        try:
            old.unlink()
        except Exception:
            pass

    filename = f"custom-logo{ext}"
    out = uploads_dir / filename
    out.write_bytes(content)
    logo_url = f"/uploads/{filename}"

    r = await db.execute(select(SystemSetting).where(SystemSetting.key == "app_logo_url"))
    s = r.scalar_one_or_none()
    if s:
        s.value = logo_url
    else:
        db.add(SystemSetting(key="app_logo_url", value=logo_url))
    await db.commit()
    logger.info(f"Logo uploaded by admin '{admin.username}' ({ext}, {len(content)} bytes)")

    return {"message": "Logo hochgeladen", "app_logo_url": logo_url}

# ========================
# Schemas
# ========================
class ServerConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Eindeutiger Servername, z.B. server1")
    display_name: Optional[str] = Field(None, description="Anzeigename")
    url: str = Field(..., description="PowerDNS API URL, z.B. http://192.168.1.10:8081")
    api_key: str = Field(..., description="PowerDNS API Key")
    description: Optional[str] = None
    allow_writes: Optional[bool] = Field(True, description="Zonen/Änderungen auf diesem Server speichern. Bei gemeinsamer DB nur bei einem Server aktivieren.")


class ServerConfigUpdate(BaseModel):
    display_name: Optional[str] = None
    url: Optional[str] = None
    api_key: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    allow_writes: Optional[bool] = None


class TestConnectionRequest(BaseModel):
    url: str = Field(..., description="PowerDNS API URL")
    api_key: str = Field(..., description="PowerDNS API Key")


# ========================
# Server Configuration CRUD
# ========================
async def _enrich_server_config_row(cfg: ServerConfig) -> dict:
    """DB-Zeile + optional Live-Status mit kurzen Timeouts.

    Sicherheit: Der vollständige API-Key wird hier NIE ausgeliefert, nur eine kurze Maske.
    Zum Bearbeiten kann der Admin den Key über `GET /settings/servers/{id}/api-key` einmalig anfordern.
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
        "description": cfg.description,
        "is_active": cfg.is_active,
        "allow_writes": getattr(cfg, "allow_writes", True),
        "is_online": is_online,
        "version": version,
        "zone_count": zone_count,
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


@router.get("/servers")
async def list_server_configs(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all server configurations from database."""
    result = await db.execute(select(ServerConfig).order_by(ServerConfig.sort_order, ServerConfig.name))
    configs = result.scalars().all()

    servers = await asyncio.gather(*[_enrich_server_config_row(cfg) for cfg in configs])
    return {"servers": list(servers)}


@router.post("/servers", status_code=201)
async def add_server_config(
    data: ServerConfigCreate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
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
    
    # Live-Verbindung hinzufuegen
    pdns_manager.add_server(cfg.name, cfg.url, cfg.api_key)
    
    logger.info(f"Server config '{data.name}' added by admin '{admin.username}'")
    return {"message": f"Server '{data.name}' hinzugefuegt", "id": cfg.id}


@router.put("/servers/{server_id}")
async def update_server_config(
    server_id: int,
    data: ServerConfigUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing server configuration."""
    result = await db.execute(select(ServerConfig).where(ServerConfig.id == server_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Server-Konfiguration nicht gefunden")
    
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
    
    # Live-Verbindung aktualisieren
    if cfg.is_active:
        pdns_manager.update_server(cfg.name, cfg.url, cfg.api_key)
    else:
        pdns_manager.remove_server(cfg.name)
    
    logger.info(f"Server config '{cfg.name}' updated by admin '{admin.username}'")
    return {"message": f"Server '{cfg.name}' aktualisiert"}


@router.delete("/servers/{server_id}")
async def delete_server_config(
    server_id: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a server configuration."""
    result = await db.execute(select(ServerConfig).where(ServerConfig.id == server_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Server-Konfiguration nicht gefunden")
    
    server_name = cfg.name
    
    # Live-Verbindung entfernen
    pdns_manager.remove_server(server_name)
    
    await db.delete(cfg)
    await db.flush()
    
    logger.info(f"Server config '{server_name}' deleted by admin '{admin.username}'")
    return {"message": f"Server '{server_name}' geloescht"}


@router.get("/servers/{server_id}/api-key")
async def reveal_server_api_key(
    server_id: int,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Gibt den vollständigen API-Key eines Servers genau einmal an einen eingeloggten Admin zurück.

    Wird ins Audit-Log geschrieben, damit die Aufdeckung nachvollziehbar ist.
    """
    from app.models.models import AuditLog
    result = await db.execute(select(ServerConfig).where(ServerConfig.id == server_id))
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(status_code=404, detail="Server-Konfiguration nicht gefunden")

    db.add(AuditLog(
        action="REVEAL_API_KEY",
        resource_type="server_config",
        resource_name=cfg.name,
        server_name=cfg.name,
        user_id=admin.id,
        status="success",
    ))
    await db.flush()
    logger.info(f"API key revealed for server '{cfg.name}' by admin '{admin.username}'")
    return {"id": cfg.id, "name": cfg.name, "api_key": cfg.api_key}


# ========================
# Test Connection
# ========================
@router.post("/servers/test")
async def test_connection(
    data: TestConnectionRequest,
    admin: User = Depends(get_admin_user),
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
class SmtpSettings(BaseModel):
    host: str = Field(default="", description="SMTP Server Hostname")
    port: int = Field(default=587, description="SMTP Port")
    username: str = Field(default="", description="SMTP Benutzername")
    password: str = Field(default="", description="SMTP Passwort")
    from_email: str = Field(default="", description="Absender E-Mail")
    from_name: str = Field(default="DNS Manager", description="Absender Name")
    encryption: str = Field(default="starttls", description="Verschlüsselung: none, starttls, ssl")
    enabled: bool = Field(default=False, description="SMTP aktiviert")


@router.get("/smtp")
async def get_smtp_settings(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current SMTP configuration."""
    from app.services.email_service import get_smtp_settings as _get
    settings = await _get(db)
    # Mask password for security
    if settings.get("password"):
        settings["password_set"] = True
        settings["password"] = "••••••••"
    else:
        settings["password_set"] = False
    return settings


@router.put("/smtp")
async def update_smtp_settings(
    data: SmtpSettings,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Update SMTP configuration."""
    from app.services.email_service import save_smtp_settings, get_smtp_settings as _get
    
    save_data = data.model_dump()
    
    # If password is masked (not changed), keep the old one
    if save_data["password"] == "••••••••":
        old = await _get(db)
        save_data["password"] = old.get("password", "")
    
    save_data["enabled"] = str(save_data["enabled"]).lower()
    await save_smtp_settings(db, save_data)
    
    return {"message": "SMTP-Einstellungen gespeichert"}


@router.post("/smtp/test")
async def test_smtp(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Test the current SMTP connection."""
    from app.services.email_service import get_smtp_settings as _get, test_smtp_connection
    
    settings = await _get(db)
    result = await test_smtp_connection(settings)
    return result


class TestEmailRequest(BaseModel):
    to_email: str = Field(..., description="E-Mail-Adresse für Test")


@router.post("/smtp/test-email")
async def send_test_email(
    data: TestEmailRequest,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a test email to verify SMTP works end-to-end."""
    from app.services.email_service import get_smtp_settings as _get, send_email
    from app.services.email_templates import pick_language, test_email
    from app.core.config import settings as app_settings

    smtp_settings = await _get(db)

    lang = pick_language(admin.preferred_language, app_settings.DEFAULT_LANGUAGE)
    subject, body_html, body_text = test_email(lang)

    try:
        send_email(smtp_settings, data.to_email, subject, body_html, body_text)
        msg_de = f"Test-E-Mail an {data.to_email} gesendet!"
        msg_en = f"Test email sent to {data.to_email}!"
        return {"success": True, "message": msg_de if lang == "de" else msg_en}
    except Exception as e:
        return {"success": False, "error": str(e)}

