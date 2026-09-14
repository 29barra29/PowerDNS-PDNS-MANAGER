"""PDNS Manager Backend - Main Application.

A custom backend for managing PowerDNS servers.
Replaces PowerDNS-Admin with a cleaner, more stable solution.
"""
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import Depends, FastAPI, Request
from urllib.parse import urlparse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import init_db
from app.services.pdns_client import PowerDNSAPIError, pdns_manager
from app.routers import servers, zones, records, dnssec, search, auth, settings as settings_router, setup, templates, acme
from app.core.auth import create_initial_admin, get_current_user
from app.core.database import engine, async_session
from sqlalchemy import text
from app.models.models import User

# Configure logging (optional JSON-Zeilen für Log-Aggregatoren, LOG_FORMAT=json)
_LOG_LEVEL = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
if (settings.LOG_FORMAT or "").lower() == "json":

    class _JsonLogFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            return json.dumps(
                {
                    "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                },
                ensure_ascii=False,
            )

    _root = logging.getLogger()
    _root.setLevel(_LOG_LEVEL)
    if not _root.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(_JsonLogFormatter())
        _root.addHandler(_h)
else:
    logging.basicConfig(
        level=_LOG_LEVEL,
        format="%(asctime)s [%(name)s] %(levelname)s - %(message)s",
    )
logger = logging.getLogger(__name__)
_METRICS_START = time.time()
_REQUEST_COUNT = 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle events."""
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    
    # Initialize database tables
    await init_db()
    logger.info("Database initialized")
    
    # Create initial admin user if no users exist
    async with async_session() as session:
        await create_initial_admin(session)
        await session.commit()

    # Ab 2.4.1 werden Reset-Links nur aus der konfigurierten Basis-URL gebaut – ohne sie
    # gehen keine Passwort-vergessen-Mails raus. Beim Start deutlich darauf hinweisen.
    try:
        from sqlalchemy import select as _select
        from app.models.models import SystemSetting as _SystemSetting
        async with async_session() as session:
            _base = await session.scalar(_select(_SystemSetting.value).where(_SystemSetting.key == "app_base_url"))
        if not (_base or "").strip() and not (settings.WEBAUTHN_ORIGIN or "").strip():
            logger.warning(
                "Keine App-Basis-URL konfiguriert: Passwort-Reset-Mails werden NICHT versendet. "
                "Bitte unter Einstellungen -> Profil -> Oeffentliche Basis-URL eintragen "
                "(oder WEBAUTHN_ORIGIN in der .env setzen)."
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("app_base_url-Check beim Start uebersprungen: %s", exc)
    
    # Load server configs from database
    from sqlalchemy import select, func
    from sqlalchemy.exc import IntegrityError
    from app.models.models import ServerConfig
    
    async with async_session() as session:
        result = await session.execute(select(ServerConfig).where(ServerConfig.is_active == True))
        db_configs = result.scalars().all()
        total_rows = await session.scalar(select(func.count(ServerConfig.id))) or 0
        
        if db_configs:
            # DB has configs -> use them (overrides env)
            pdns_manager.load_from_db_configs(db_configs)
        elif total_rows:
            # Es gibt Server in der DB, aber alle sind deaktiviert: KEIN erneuter Env-Import,
            # sonst kollidiert der Name mit der deaktivierten Zeile (UNIQUE) und der Start
            # bricht in einer Restart-Schleife ab.
            logger.info("Alle PowerDNS-Server in der DB sind deaktiviert – kein Import aus PDNS_SERVERS.")
        else:
            # Leere Tabelle -> Env-Server einmalig in die DB uebernehmen
            env_servers = settings.get_pdns_servers()
            if env_servers:
                logger.info("Importing server configs from environment to database...")
                for s in env_servers:
                    new_cfg = ServerConfig(
                        name=s["name"],
                        display_name=s["name"].upper(),
                        url=s["url"],
                        api_key=s["api_key"],
                        description=f"Imported from PDNS_SERVERS env",
                        is_active=True,
                        allow_writes=True,
                    )
                    session.add(new_cfg)
                try:
                    await session.commit()
                    logger.info(f"Saved {len(env_servers)} server configs to database")
                except IntegrityError as exc:
                    await session.rollback()
                    logger.warning("PDNS_SERVERS-Import uebersprungen (Name existiert bereits): %s", exc)
    
    # Log configured servers
    server_names = pdns_manager.list_servers()
    if server_names:
        logger.info(f"Configured PowerDNS servers: {server_names}")
    else:
        logger.info("No PowerDNS servers configured. Use the admin panel to add servers.")
    
    yield
    
    # Shutdown
    logger.info("Shutting down PDNS Manager")


# Create FastAPI app – /docs, /redoc und /openapi.json sind standardmäßig AUS,
# damit die API-Struktur nicht ungewollt im Internet einsehbar ist. Aktivieren via DOCS_ENABLED=true.
_docs_url = "/docs" if settings.DOCS_ENABLED else None
_redoc_url = "/redoc" if settings.DOCS_ENABLED else None
_openapi_url = "/openapi.json" if settings.DOCS_ENABLED else None

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="PDNS Manager backend for PowerDNS.",
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
    lifespan=lifespan,
)

# CORS – nur konfigurierte Origins erlaubt; "*" mit Cookies wäre unsicher (Browser blockt es ohnehin).
# Wenn keine Origin gesetzt ist, deaktivieren wir CORS komplett – die SPA wird vom gleichen Origin geliefert.
_cors_origins = settings.get_allowed_origins()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )


_CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _is_same_site_request(request: Request) -> bool:
    """Prueft bei zustandsaendernden Cookie-Requests, dass sie von der eigenen Seite kommen.

    Cookie-Auth allein schuetzt nicht vor Cross-Site-Formularen (CSRF), besonders wenn
    AUTH_COOKIE_SAMESITE=none gesetzt ist. Browser schicken bei Cross-Site-POSTs immer
    ``Origin`` und (moderne) ``Sec-Fetch-Site``; beides wird hier geprueft. Requests ganz
    ohne diese Header stammen nicht aus einem Browser (curl, Skripte) und sind kein CSRF-Vektor.
    """
    sfs = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sfs in ("same-origin", "none"):
        return True
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return not sfs  # sfs=cross-site/same-site ohne Origin -> ablehnen; gar nichts -> kein Browser
    origin_host = (urlparse(origin).netloc or "").lower()
    if origin_host and origin_host == (request.headers.get("host") or "").strip().lower():
        return True
    if settings.TRUST_PROXY_HEADERS:
        fwd_host = ", ".join(request.headers.getlist("x-forwarded-host")).split(",")[-1].strip().lower()
        if fwd_host and origin_host == fwd_host:
            return True
    allowed = {o.strip().rstrip("/").lower() for o in settings.get_allowed_origins()}
    return origin.lower() in allowed


@app.middleware("http")
async def _csrf_guard(request: Request, call_next):
    if request.method not in _CSRF_SAFE_METHODS and request.url.path.startswith("/api/"):
        auth = (request.headers.get("authorization") or "").lower()
        if not auth.startswith("bearer ") and not _is_same_site_request(request):
            return JSONResponse(
                status_code=403,
                content={"detail": "Cross-Site-Anfrage abgelehnt (CSRF-Schutz)"},
            )
    return await call_next(request)


# Security-Header für *alle* Antworten. Hilft gegen Clickjacking, MIME-Sniffing,
# unkontrolliertes Browser-Feature-Loading und versehentliche Referer-Lecks.
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    global _REQUEST_COUNT
    if request.url.path.startswith("/api/"):
        _REQUEST_COUNT += 1
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "interest-cohort=(), camera=(), microphone=(), geolocation=()",
    )
    # Content-Security-Policy (gegen XSS/Injection). Konfigurierbar; leer = aus.
    # Swagger-UI/ReDoc (nur bei DOCS_ENABLED) laden ihre Assets von einem CDN und nutzen
    # Inline-Skripte – dort wuerde die strikte CSP die Seite leer lassen.
    if settings.CONTENT_SECURITY_POLICY and not request.url.path.startswith(("/docs", "/redoc", "/openapi.json")):
        response.headers.setdefault("Content-Security-Policy", settings.CONTENT_SECURITY_POLICY)
    # Cross-Origin-Hardening (für die SPA + API gleichermaßen sicher).
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    # Wenn das Backend hinter HTTPS läuft, sorgt HSTS dafür, dass Browser das nicht vergessen.
    if settings.AUTH_COOKIE_SECURE:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# ========================
# Exception handlers
# ========================
@app.exception_handler(PowerDNSAPIError)
async def pdns_error_handler(request: Request, exc: PowerDNSAPIError):
    """Handle PowerDNS API errors."""
    logger.warning("PowerDNS error for client: server=%s status=%s detail=%s", exc.server, exc.status_code, exc.detail)
    public_detail = exc.detail
    if exc.status_code >= 500:
        public_detail = "PowerDNS-Server ist derzeit nicht erreichbar. Bitte Server-Konfiguration und Erreichbarkeit prüfen."
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": "PowerDNS API Error",
            "server": exc.server,
            "detail": public_detail,
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle value errors (e.g., unknown server name)."""
    return JSONResponse(
        status_code=400,
        content={"error": str(exc)},
    )


# ========================
# Include routers
# ========================
API_PREFIX = "/api/v1"

# Setup router (muss vor auth router sein für öffentlichen Zugriff)
app.include_router(setup.router, prefix=API_PREFIX)
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(servers.router, prefix=API_PREFIX)
app.include_router(zones.router, prefix=API_PREFIX)
app.include_router(records.router, prefix=API_PREFIX)
app.include_router(dnssec.router, prefix=API_PREFIX)
app.include_router(search.router, prefix=API_PREFIX)
app.include_router(settings_router.router, prefix=API_PREFIX)
app.include_router(templates.router, prefix=API_PREFIX)
app.include_router(acme.router, prefix=API_PREFIX)


# ========================
# Static files & Frontend (React SPA)
# ========================
STATIC_DIR = Path(__file__).parent / "static_new"


class _NoDotfiles(StaticFiles):
    """StaticFiles, das Dotfiles/-Ordner (.jwt_secret, .env, .git ...) nie ausliefert."""

    async def check_config(self) -> None:
        # Das Uploads-Volume kann beim allerersten Start noch fehlen: dann 404 statt 500.
        try:
            await super().check_config()
        except RuntimeError as exc:
            logger.warning("Uploads-Verzeichnis fehlt noch (%s) – /uploads liefert vorerst 404", exc)
            self.config_checked = True

    async def get_response(self, path: str, scope):
        if any(seg.startswith(".") for seg in path.replace("\\", "/").split("/") if seg):
            return PlainTextResponse("Not Found", status_code=404)
        return await super().get_response(path, scope)


# Serve static assets (JS, CSS, images). Assets are optional at import time so a
# broken build does not crash API-only diagnostics; the SPA route explains it.
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")
if STATIC_DIR.exists():
    app.mount("/uploads", _NoDotfiles(directory=str(STATIC_DIR / "uploads"), check_dir=False), name="uploads")


@app.get("/vite.svg", include_in_schema=False)
async def vite_icon():
    icon = STATIC_DIR / "vite.svg"
    if icon.exists():
        return FileResponse(str(icon), media_type="image/svg+xml")
    return JSONResponse(status_code=404, content={"error": "Not found"})


@app.get(f"{API_PREFIX}/metrics", tags=["Health"])
async def app_metrics(
    current_user: User = Depends(get_current_user),
):
    """Einfache Laufzeit-Metriken (für Admins) – ungefähre Request-Anzahl + Uptime."""
    if current_user.role != "admin":
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Nur Admins")
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "uptime_seconds": int(time.time() - _METRICS_START),
        "api_request_count": _REQUEST_COUNT,
    }


@app.get("/api", tags=["Health"])
async def api_info():
    """API info endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs" if settings.DOCS_ENABLED else None,
        "api_prefix": API_PREFIX,
        "servers_configured": len(pdns_manager.list_servers()),
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check: DB + PowerDNS-APIs."""
    db_ok = True
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    server_status = {}
    for name, client in pdns_manager.get_all_clients().items():
        try:
            await client.get_server_info()
            server_status[name] = "healthy"
        except Exception:
            server_status[name] = "unreachable"

    pdns_ok = all(s == "healthy" for s in server_status.values()) if server_status else True
    if not db_ok:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "database": "disconnected",
                "servers": server_status,
            },
        )
    if not pdns_ok:
        return {
            "status": "degraded",
            "database": "connected",
            "servers": server_status,
        }
    return {
        "status": "healthy",
        "database": "connected",
        "servers": server_status,
    }


# SPA catch-all: serve index.html for all non-API routes (React Router handles them)
@app.get("/{path:path}", response_class=HTMLResponse, tags=["Frontend"])
async def spa_catch_all(path: str):
    """Serve the React SPA for all frontend routes."""
    # Don't intercept API, docs, or static paths
    if path.startswith(("api/", "docs", "redoc", "openapi", "assets/", "uploads/")):
        return JSONResponse(status_code=404, content={"error": "Not found"})
    
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Frontend not found. Run 'npm run build' first.</h1>", status_code=404)

