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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
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
    
    # Load server configs from database
    from sqlalchemy import select
    from app.models.models import ServerConfig
    
    async with async_session() as session:
        result = await session.execute(select(ServerConfig).where(ServerConfig.is_active == True))
        db_configs = result.scalars().all()
        
        if db_configs:
            # DB has configs -> use them (overrides env)
            pdns_manager.load_from_db_configs(db_configs)
        else:
            # No DB configs -> check if env has servers, and save them to DB
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
                await session.commit()
                logger.info(f"Saved {len(env_servers)} server configs to database")
    
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

# Serve static assets (JS, CSS, images). Assets are optional at import time so a
# broken build does not crash API-only diagnostics; the SPA route explains it.
if (STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")
if STATIC_DIR.exists():
    app.mount("/uploads", StaticFiles(directory=str(STATIC_DIR / "uploads"), check_dir=False), name="uploads")


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

