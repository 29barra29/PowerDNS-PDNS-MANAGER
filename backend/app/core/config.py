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
    APP_NAME: str = "PDNS Manager"
    APP_VERSION: str = Field(default_factory=_read_version_file)
    LOG_LEVEL: str = "info"
    # text (Standard) oder json für strukturierte Zeilen
    LOG_FORMAT: str = "text"

    @model_validator(mode="after")
    def _app_version_always_from_file(self) -> "Settings":
        object.__setattr__(self, "APP_VERSION", _read_version_file())
        return self

    # Database
    DATABASE_URL: str = "mysql+aiomysql://dns_admin:changeme-password@mariadb:3306/dns_manager"

    # JWT Auth - Automatisch generieren wenn nicht gesetzt
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    # Laufzeit des Session-Tokens. Ohne explizite Env-Angabe folgt der Wert
    # AUTH_COOKIE_MAX_AGE (Sekunden / 60), damit Cookie und Token gleich lang gelten
    # (siehe _jwt_expiry_follows_cookie). compose.yaml reicht JWT_EXPIRE_MINUTES nicht durch.
    JWT_EXPIRE_MINUTES: int = 1440  # 24 Stunden – nur Fallback, s. o.

    # Auth-Cookie (sicherer als localStorage; HttpOnly, kein Zugriff per JavaScript)
    AUTH_COOKIE_NAME: str = "dns_manager_token"
    AUTH_COOKIE_MAX_AGE: int = 86400  # Sekunden, 24h; JWT_EXPIRE_MINUTES folgt diesem Wert
    AUTH_COOKIE_SECURE: bool = False  # True wenn nur HTTPS
    AUTH_COOKIE_SAMESITE: str = "lax"

    @model_validator(mode="after")
    def _jwt_expiry_follows_cookie(self) -> "Settings":
        # JWT_EXPIRE_MINUTES nur dann selbst ableiten, wenn es nicht explizit gesetzt wurde
        # (Env, .env oder Konstruktor). Sonst lebt der Cookie (compose-Default 30 Tage)
        # laenger als das darin gespeicherte Token (24 h) und Nutzer fliegen frueher raus.
        if "JWT_EXPIRE_MINUTES" not in self.model_fields_set:
            object.__setattr__(self, "JWT_EXPIRE_MINUTES", max(1, self.AUTH_COOKIE_MAX_AGE // 60))
        return self

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
    # Webhooks sind serverseitige HTTP-Requests. Private Ziele nur bewusst erlauben.
    WEBHOOK_ALLOW_PRIVATE_URLS: bool = False
    # Hinter einem Reverse-Proxy: X-Forwarded-For/X-Real-IP für die echte Client-IP
    # auswerten (Login-Rate-Limit, Audit-Log). NUR aktivieren, wenn das Backend
    # ausschließlich über den vertrauenswürdigen Proxy erreichbar ist – sonst sind
    # diese Header fälschbar.
    TRUST_PROXY_HEADERS: bool = False
    # Anzahl vertrauenswürdiger Proxys vor dem Backend (1 = nur der eigene Reverse-Proxy,
    # 2 = z. B. Cloudflare -> nginx). Bestimmt, welcher X-Forwarded-For-Eintrag von
    # rechts als echte Client-IP gilt.
    TRUSTED_PROXY_HOPS: int = 1
    # Groesse des DB-Verbindungspools. 0 = kein Pool (jede Session oeffnet/schliesst ihre
    # Verbindung selbst) – sinnvoll fuer Tests, bei denen jeder Testfall einen eigenen
    # Event-Loop hat; Default 10 fuer den Betrieb.
    DB_POOL_SIZE: int = 10
    # Content-Security-Policy-Header. Leer = Header wird nicht gesetzt.
    # Standard deckt die SPA (gleicher Origin), Google Fonts, die Captcha-Provider
    # (Turnstile/hCaptcha/reCAPTCHA) und den GitHub-Versionscheck ab. Bei reCAPTCHA-
    # Problemen ggf. "'unsafe-eval'" zu script-src ergänzen.
    CONTENT_SECURITY_POLICY: str = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data: blob: https:; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "script-src 'self' https://challenges.cloudflare.com https://js.hcaptcha.com "
        "https://*.hcaptcha.com https://www.google.com https://www.gstatic.com https://www.recaptcha.net; "
        "connect-src 'self' https://api.github.com https://challenges.cloudflare.com "
        "https://*.hcaptcha.com; "
        "frame-src https://challenges.cloudflare.com https://*.hcaptcha.com https://www.google.com https://www.recaptcha.net https://recaptcha.google.com"
    )

    # WebAuthn / Passkeys
    # RP-ID = die registrierbare Domain (OHNE Schema/Port), z. B. "dns.example.com".
    # Leer = automatisch aus der Browser-Origin jeder Anfrage ableiten (funktioniert für
    # localhost wie für die Produktiv-Domain). Nur setzen, wenn die App fest unter EINER
    # Domain läuft und du die Passkeys daran binden willst (empfohlen für Produktion).
    WEBAUTHN_RP_ID: Optional[str] = None
    # Anzeigename im Browser-Dialog. Leer = APP_NAME.
    WEBAUTHN_RP_NAME: Optional[str] = None
    # Optionale, zusätzlich erlaubte Origins (Komma-Liste, MIT Schema), z. B.
    # "https://dns.example.com,https://dns2.example.com". Die Origin der jeweiligen
    # Anfrage wird automatisch ergänzt; das hier ist nur für Sonderfälle nötig.
    WEBAUTHN_ORIGIN: str = ""

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

# JWT-Secret prüfen. In Produktion (HTTPS-Cookies) ist ein fester Schlüssel Pflicht:
# Ein pro Prozess zufällig generierter Key macht bei jedem Neustart ALLE Sessions
# ungültig und bricht den Betrieb mit mehreren Workern komplett. Lieber sofort mit
# klarer Meldung abbrechen als still ein unsicheres/instabiles Setup fahren.
if not _settings.JWT_SECRET_KEY:
    import logging
    import os
    from pathlib import Path

    # Kein Key in der .env: einen persistenten Key im Uploads-Volume ablegen, damit
    # Sessions Neustarts ueberleben. Bewusst KEIN Abbruch: ein RuntimeError wuerde
    # Bestandsinstallationen mit leerem Key in eine Container-Restart-Schleife schicken.
    _key_file = Path(
        os.getenv("JWT_SECRET_FILE")
        # NICHT unter static_new/uploads: das wird unauthentifiziert per /uploads ausgeliefert.
        or Path(__file__).resolve().parents[2] / "data" / ".jwt_secret"
    )
    try:
        if _key_file.exists():
            _settings.JWT_SECRET_KEY = _key_file.read_text(encoding="utf-8").strip()
        if not _settings.JWT_SECRET_KEY:
            _key_file.parent.mkdir(parents=True, exist_ok=True)
            _settings.JWT_SECRET_KEY = secrets.token_hex(32)
            _key_file.write_text(_settings.JWT_SECRET_KEY, encoding="utf-8")
            try:
                os.chmod(_key_file, 0o600)
            except OSError:
                pass
        logging.warning(
            "JWT_SECRET_KEY ist nicht in der .env gesetzt – es wird der Schluessel aus %s "
            "verwendet. Fuer Produktion JWT_SECRET_KEY in der .env setzen "
            "(z. B. openssl rand -hex 64).",
            _key_file,
        )
    except OSError as exc:
        _settings.JWT_SECRET_KEY = secrets.token_hex(32)
        logging.error(
            "JWT_SECRET_KEY nicht gesetzt und %s nicht schreibbar (%s): temporaerer Schluessel, "
            "alle Sessions enden bei jedem Neustart! Bitte JWT_SECRET_KEY in der .env setzen.",
            _key_file, exc,
        )

settings = _settings
