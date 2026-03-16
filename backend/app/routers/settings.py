"""API routes for system settings and configuration management (Admin only)."""
import logging
import httpx
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.auth import get_admin_user
from app.models.models import User, ServerConfig
from app.services.pdns_client import pdns_manager, PowerDNSClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


# ========================
# App Info
# ========================
class AppInfoUpdate(BaseModel):
    app_name: str = Field(..., description="Name der Anwendung")

@router.get("/app-info", include_in_schema=False)
async def get_app_info(db: AsyncSession = Depends(get_db)):
    """Get public app info like app name and version."""
    from app.models.models import SystemSetting
    from sqlalchemy import select
    from app.core.config import settings
    
    result = await db.execute(select(SystemSetting.value).where(SystemSetting.key == "app_name"))
    custom_name = result.scalar_one_or_none()
    
    return {
        "app_name": custom_name or settings.APP_NAME,
        "app_version": settings.APP_VERSION
    }

@router.put("/app-info")
async def update_app_info(
    data: AppInfoUpdate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db)
):
    """Update custom app name."""
    from app.models.models import SystemSetting
    from sqlalchemy import select
    
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "app_name"))
    setting = result.scalar_one_or_none()
    
    if setting:
        setting.value = data.app_name
    else:
        db.add(SystemSetting(key="app_name", value=data.app_name))
    
    await db.commit()
    return {"message": "Systemname aktualisiert"}

# ========================
# Schemas
# ========================
class ServerConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Eindeutiger Servername, z.B. ns1")
    display_name: Optional[str] = Field(None, description="Anzeigename")
    url: str = Field(..., description="PowerDNS API URL, z.B. http://192.168.1.10:8081")
    api_key: str = Field(..., description="PowerDNS API Key")
    description: Optional[str] = None


class ServerConfigUpdate(BaseModel):
    display_name: Optional[str] = None
    url: Optional[str] = None
    api_key: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class TestConnectionRequest(BaseModel):
    url: str = Field(..., description="PowerDNS API URL")
    api_key: str = Field(..., description="PowerDNS API Key")


# ========================
# Server Configuration CRUD
# ========================
@router.get("/servers")
async def list_server_configs(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all server configurations from database."""
    result = await db.execute(select(ServerConfig).order_by(ServerConfig.sort_order, ServerConfig.name))
    configs = result.scalars().all()
    
    servers = []
    for cfg in configs:
        # Check live status
        is_online = False
        version = None
        zone_count = None
        if cfg.is_active and cfg.name in pdns_manager.get_all_clients():
            try:
                client = pdns_manager.get_client(cfg.name)
                info = await client.get_server_info()
                is_online = True
                version = info.get("version", "")
                zones = await client.list_zones()
                zone_count = len(zones) if zones else 0
            except Exception:
                pass
        
        servers.append({
            "id": cfg.id,
            "name": cfg.name,
            "display_name": cfg.display_name,
            "url": cfg.url,
            "api_key": cfg.api_key[:8] + "..." if cfg.api_key else "",  # Maskiert
            "api_key_full": cfg.api_key,  # Für Bearbeitung
            "description": cfg.description,
            "is_active": cfg.is_active,
            "is_online": is_online,
            "version": version,
            "zone_count": zone_count,
            "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        })
    
    return {"servers": servers}


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
    if data.api_key is not None:
        cfg.api_key = data.api_key
    if data.description is not None:
        cfg.description = data.description
    if data.is_active is not None:
        cfg.is_active = data.is_active
    
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
    
    settings = await _get(db)
    
    try:
        send_email(
            settings,
            data.to_email,
            "DNS Manager – Test-E-Mail",
            "<h2>✅ Test erfolgreich!</h2><p>Diese E-Mail wurde vom DNS Manager gesendet.</p><p>Dein SMTP ist korrekt konfiguriert.</p>",
            "Test erfolgreich! Diese E-Mail wurde vom DNS Manager gesendet."
        )
        return {"success": True, "message": f"Test-E-Mail an {data.to_email} gesendet!"}
    except Exception as e:
        return {"success": False, "error": str(e)}

