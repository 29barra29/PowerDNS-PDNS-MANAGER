from pydantic_settings import BaseSettings
from typing import Optional
import os
import secrets


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    APP_NAME: str = "DNS Manager"
    APP_VERSION: str = "2.1.0"
    LOG_LEVEL: str = "info"

    # Database
    DATABASE_URL: str = "mysql+aiomysql://dns_admin:changeme-password@mariadb:3306/dns_manager"

    # JWT Auth - Automatisch generieren wenn nicht gesetzt
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 Stunden

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
