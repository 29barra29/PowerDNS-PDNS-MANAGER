from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional
import os
import secrets


def _read_version_file() -> str:
    """Liest die zentrale VERSION-Datei (eine Stelle für die ganze App)."""
    fallback = "2.2.1"
    base = Path(__file__).resolve().parent.parent.parent  # backend/app/core -> backend oder /app
    for p in [base / "VERSION", base.parent / "VERSION"]:
        if p.exists():
            try:
                return p.read_text(encoding="utf-8").strip() or fallback
            except Exception:
                pass
    return fallback


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App – Version aus VERSION-Datei (einmal eintragen, überall aktuell)
    APP_NAME: str = "DNS Manager"
    APP_VERSION: str = _read_version_file()
    LOG_LEVEL: str = "info"

    # Database
    DATABASE_URL: str = "mysql+aiomysql://dns_admin:changeme-password@mariadb:3306/dns_manager"

    # JWT Auth - Automatisch generieren wenn nicht gesetzt
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 Stunden

    # Auth-Cookie (sicherer als localStorage; HttpOnly, kein Zugriff per JavaScript)
    AUTH_COOKIE_NAME: str = "dns_manager_token"
    AUTH_COOKIE_MAX_AGE: int = 86400  # Sekunden, 24h (sollte zu JWT_EXPIRE_MINUTES passen)
    AUTH_COOKIE_SECURE: bool = False  # True wenn nur HTTPS
    AUTH_COOKIE_SAMESITE: str = "lax"

    # First-Run Settings
    ENABLE_REGISTRATION: bool = False
    INITIAL_ADMIN_PASSWORD: Optional[str] = None

    # Email Settings
    MAIL_ENABLED: bool = False
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None
    SMTP_USE_TLS: bool = True

    # PowerDNS Servers
    # Format: name|url|api_key,name|url|api_key
    PDNS_SERVERS: str = ""

    # Optional: Pfad zum Projekt auf dem Host (für Anzeige unter Einstellungen → Updates).
    INSTALL_PATH: Optional[str] = None
    # Standard-Sprache der Oberfläche (de/en), z.B. aus install.sh gesetzt.
    DEFAULT_LANGUAGE: Optional[str] = None
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
    def get_pdns_servers(self) -> list[dict]:
        """Parse PDNS_SERVERS env var into list of server configs."""
        servers = []
        if not self.PDNS_SERVERS:
            return servers
        
        for entry in self.PDNS_SERVERS.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("|")
            if len(parts) == 3:
                servers.append({
                    "name": parts[0].strip(),
                    "url": parts[1].strip().rstrip("/"),
                    "api_key": parts[2].strip(),
                })
        return servers


# Initialisiere Settings und generiere JWT Secret wenn nötig
_settings = Settings()

# Generiere JWT Secret wenn nicht gesetzt
if not _settings.JWT_SECRET_KEY:
    _settings.JWT_SECRET_KEY = secrets.token_hex(32)
    import logging
    logging.warning("JWT_SECRET_KEY not set in environment, generated a random one. "
                   "For production, please set JWT_SECRET_KEY in your .env file!")

settings = _settings
