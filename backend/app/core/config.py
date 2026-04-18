from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
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

    # App – Version **nur** aus VERSION-Datei (nie aus .env überschreiben; dort stand z. B. noch 2.0.0)
    APP_NAME: str = "DNS Manager"
    APP_VERSION: str = Field(default_factory=_read_version_file)
    LOG_LEVEL: str = "info"

    @model_validator(mode="after")
    def _app_version_always_from_file(self) -> "Settings":
        object.__setattr__(self, "APP_VERSION", _read_version_file())
        return self

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

    # Web-Sicherheit
    # Komma-getrennte Liste erlaubter Origins für CORS, z.B.
    # "https://dns.example.com,https://dns2.example.com".
    # Leer -> kein Cross-Origin (gleicher Origin reicht für die SPA, weil sie vom gleichen Backend gehostet wird).
    ALLOWED_ORIGINS: str = ""
    # OpenAPI-/ReDoc-/Swagger-UI nur einschalten, wenn explizit gewünscht (Default: aus).
    DOCS_ENABLED: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

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

    def get_allowed_origins(self) -> list[str]:
        """ALLOWED_ORIGINS aus Env in Liste – '*' wird ignoriert (mit Cookies inkompatibel)."""
        if not self.ALLOWED_ORIGINS:
            return []
        out: list[str] = []
        for entry in self.ALLOWED_ORIGINS.split(","):
            o = entry.strip().rstrip("/")
            if not o or o == "*":
                continue
            out.append(o)
        return out


# Initialisiere Settings und generiere JWT Secret wenn nötig
_settings = Settings()

# Generiere JWT Secret wenn nicht gesetzt
if not _settings.JWT_SECRET_KEY:
    _settings.JWT_SECRET_KEY = secrets.token_hex(32)
    import logging
    logging.warning("JWT_SECRET_KEY not set in environment, generated a random one. "
                   "For production, please set JWT_SECRET_KEY in your .env file!")

settings = _settings
