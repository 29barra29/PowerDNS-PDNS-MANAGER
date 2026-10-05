"""PDNS Manager Backend - Main Application.

A custom backend for managing PowerDNS servers.
Replaces PowerDNS-Admin with a cleaner, more stable solution.

Aufbau (Bauplan A.6, B.13):
- Logging; sensible Query-Parameter werden im uvicorn-Access-Log maskiert (F9 5.2).
- ``lifespan`` (Reihenfolge verbindlich): Schema/Migration -> Geheimnisse -> Initial-Admin -> Hinweis
  Basis-URL -> PowerDNS-Server laden -> Hinweise (Metrik-Token, DynDNS hinter Proxy) -> Hintergrund-Aufgaben;
  beim Herunterfahren werden die Hintergrund-Aufgaben beendet.
- Middleware von aussen nach innen: ``_prometheus_http``, ``_security_headers``, ``_csrf_guard``, CORS.
- API-Router per Discovery aus ``app.routers`` (``LEGACY_ROUTER_ORDER`` fuer die 2.4.1-Module, Attribut
  ``ROUTER_ORDER`` fuer neue Module, optional ``root_routers`` ohne Prefix – siehe ``app/routers/__init__.py``).
- Danach: Static-Mounts, ``/vite.svg``, ``/api/v1/metrics`` (JSON, Admin), ``/metrics`` (Prometheus),
  ``/api``, ``/health`` und zuletzt der SPA-Catch-all.
"""
import asyncio
import importlib
import ipaddress
import json
import logging
import pkgutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterable
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core import metrics as prom
from app.core import secrets as secret_store
from app.core.auth import create_initial_admin, get_admin_user
from app.core.config import settings
from app.core.database import MIGRATION_ERRORS, SchemaStartupError, async_session, engine, init_db
from app.core.log_redaction import install_access_log_redaction
from app.core.secret_mask import SecretReentryRequired
from app.models.models import User
from app.services import background, metrics_runtime
from app.services.pdns_client import PowerDNSAPIError, pdns_manager

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
# Tokens/Passwoerter in Query-Strings (z. B. DynDNS-Clients) nie im Access-Log (F9 5.2). uvicorn hat sein
# Logging beim Import der App bereits konfiguriert – der Filter bleibt erhalten.
install_access_log_redaction()

logger = logging.getLogger(__name__)
_METRICS_START = time.time()
_REQUEST_COUNT = 0

API_PREFIX = "/api/v1"

SECRETS_UNAVAILABLE_DETAIL = (
    "Verschlüsselung nicht verfügbar – Geheimnis wurde nicht gespeichert. Bitte Server-Log prüfen."
)
DYNDNS_PROXY_HINT = (
    "DynDNS-Rate-Limits arbeiten auf der direkten Peer-IP – hinter einem Reverse-Proxy TRUST_PROXY_HEADERS setzen"
)

# ========================
# Router-Discovery (Bauplan B.13)
# ========================
# Ordnung der 2.4.1-Module (unveraendert, keine ROUTER_ORDER-Attribute in diesen Dateien). Neue Module
# setzen ROUTER_ORDER selbst; ein Modul ohne beides ist ein Fehler (KeyError beim Start, Test schlaegt fehl).
LEGACY_ROUTER_ORDER = {"setup": 10, "auth": 20, "servers": 40, "zones": 60, "records": 70, "dnssec": 80,
                       "search": 90, "settings": 100, "templates": 110, "acme": 120}


def _iter_router_modules(package: str = "app.routers") -> list[ModuleType]:
    """Alle Router-Module des Pakets (ohne ``_``-Praefix und ohne Unterpakete), alphabetisch."""
    pkg = importlib.import_module(package)
    names = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_") and not m.ispkg)
    return [importlib.import_module(f"{package}.{name}") for name in names]


def _router_order(mod: ModuleType) -> int:
    """``ROUTER_ORDER`` des Moduls, sonst Eintrag in ``LEGACY_ROUTER_ORDER`` (fehlt beides: KeyError)."""
    own = getattr(mod, "ROUTER_ORDER", None)
    if own is not None:
        return own
    return LEGACY_ROUTER_ORDER[mod.__name__.rsplit(".", 1)[-1]]


def include_router_modules(target: FastAPI, modules: Iterable[ModuleType]) -> list[ModuleType]:
    """Router nach Ordnung einbinden (``router`` unter ``API_PREFIX``, ``root_routers`` ohne Prefix).

    Rueckgabe: die Module in Einbinde-Reihenfolge.
    """
    ordered = sorted(modules, key=_router_order)
    for mod in ordered:
        target.include_router(mod.router, prefix=API_PREFIX)
        for root_router in getattr(mod, "root_routers", None) or []:
            target.include_router(root_router)
    return ordered


# ========================
# Start und Herunterfahren (Bauplan A.6)
# ========================
async def _warn_missing_base_url() -> None:
    """Ab 2.4.1 werden Reset-Links nur aus der konfigurierten Basis-URL gebaut – ohne sie gehen keine
    Passwort-vergessen-Mails raus. Beim Start deutlich darauf hinweisen."""
    try:
        from app.services.system_settings import get_setting

        async with async_session() as session:
            base = await get_setting(session, "app_base_url")
        if not (base or "").strip() and not (settings.WEBAUTHN_ORIGIN or "").strip():
            logger.warning(
                "Keine App-Basis-URL konfiguriert: Passwort-Reset-Mails werden NICHT versendet. "
                "Bitte unter Einstellungen -> Profil -> Oeffentliche Basis-URL eintragen "
                "(oder WEBAUTHN_ORIGIN in der .env setzen)."
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("app_base_url-Check beim Start uebersprungen: %s", exc)


async def _load_pdns_servers() -> None:
    """PowerDNS-Server aus der DB laden; leere Tabelle -> einmaliger Import aus ``PDNS_SERVERS``.

    Server mit unlesbarem/leerem API-Key werden nicht geladen, stehen in ``pdns_manager.unloaded`` und
    erscheinen im Fan-out als ``skipped (not loaded: …)`` [D4].
    """
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    from app.models.models import ServerConfig

    async with async_session() as session:
        result = await session.execute(select(ServerConfig).where(ServerConfig.is_active == True))  # noqa: E712
        db_configs = result.scalars().all()
        total_rows = await session.scalar(select(func.count(ServerConfig.id))) or 0

        if db_configs:
            # DB has configs -> use them (overrides env)
            skipped = pdns_manager.load_from_db_configs(db_configs)
            if skipped:
                logger.error(
                    "%d PowerDNS-Server nicht geladen (API-Key nicht lesbar oder leer): %s – "
                    "bitte unter Einstellungen -> DNS-Server neu eintragen.",
                    len(skipped), ", ".join(skipped),
                )
        elif total_rows:
            # Es gibt Server in der DB, aber alle sind deaktiviert: KEIN erneuter Env-Import,
            # sonst kollidiert der Name mit der deaktivierten Zeile (UNIQUE) und der Start
            # bricht in einer Restart-Schleife ab.
            logger.info("Alle PowerDNS-Server in der DB sind deaktiviert – kein Import aus PDNS_SERVERS.")
        else:
            # Leere Tabelle -> Env-Server einmalig in die DB uebernehmen (api_key wird verschluesselt)
            env_servers = settings.get_pdns_servers()
            if env_servers:
                logger.info("Importing server configs from environment to database...")
                for s in env_servers:
                    session.add(ServerConfig(
                        name=s["name"],
                        display_name=s["name"].upper(),
                        url=s["url"],
                        api_key=s["api_key"],
                        description="Imported from PDNS_SERVERS env",
                        is_active=True,
                        allow_writes=True,
                    ))
                try:
                    await session.commit()
                    logger.info(f"Saved {len(env_servers)} server configs to database")
                except IntegrityError as exc:
                    await session.rollback()
                    logger.warning("PDNS_SERVERS-Import uebersprungen (Name existiert bereits): %s", exc)

    server_names = pdns_manager.list_servers()
    if server_names:
        logger.info(f"Configured PowerDNS servers: {server_names}")
    else:
        logger.info("No PowerDNS servers configured. Use the admin panel to add servers.")


def _log_startup_hints() -> None:
    """Einmalige Betriebs-Hinweise: zu kurzer METRICS_TOKEN (F13), DynDNS ohne Proxy-Header [S6]."""
    metrics_runtime.log_env_token_state()
    if not settings.TRUST_PROXY_HEADERS:
        logger.warning(DYNDNS_PROXY_HINT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start in fester Reihenfolge (A.6); Fehler in Schritt 1/2 brechen den Start mit klarer Meldung ab."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")

    # 1. Schema -> Spaltenpruefung -> Datenmigrationen -> Backfill
    try:
        await init_db()
    except SchemaStartupError as exc:
        for line in exc.log_lines():
            logger.critical(line)
        raise
    if MIGRATION_ERRORS:
        logger.error(
            "Start trotz %d fehlgeschlagener Migrationsschritte fortgesetzt (Details oben; lokal auch ueber /health).",
            len(MIGRATION_ERRORS),
        )
    logger.info("Database initialized")

    # 2. Geheimnisse: Schluessel laden/pruefen, Klartexte verschluesseln – VOR jedem ORM-Zugriff auf
    #    users/server_configs/webhooks (create_initial_admin laedt User inkl. totp_secret).
    try:
        await secret_store.init_secrets(engine)
    except secret_store.SecretsStartupError as exc:
        for line in exc.log_lines():
            logger.critical(line)
        raise

    # 3. Initial-Admin (nur wenn noch kein Benutzer existiert)
    async with async_session() as session:
        await create_initial_admin(session)
        await session.commit()

    # 4. Hinweis Basis-URL
    await _warn_missing_base_url()

    # 5. PowerDNS-Server
    await _load_pdns_servers()

    # 6. Betriebs-Hinweise
    _log_startup_hints()

    # 7. Hintergrund-Aufgaben (Webhook-Worker, Audit-Bereinigung)
    if settings.BACKGROUND_WORKERS_ENABLED:
        background.start_all()
    else:
        logger.warning(
            "Hintergrund-Aufgaben deaktiviert (BACKGROUND_WORKERS_ENABLED=false) – Webhook-Ereignisse werden "
            "nur gesammelt, die Audit-Bereinigung laeuft nicht."
        )

    try:
        yield
    finally:
        # 8. Herunterfahren
        await background.stop_all()
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

# ========================
# Middleware – Registrierung von innen nach aussen (die zuletzt registrierte ist die aeusserste):
# CORS (innen) -> _csrf_guard -> _security_headers -> _prometheus_http (aussen, misst auch CSRF-403)
# ========================

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


# Prometheus-HTTP-Metriken (F13 5.4): aeusserste Middleware, misst alle Antworten inkl. CSRF-403 und 500.
# Label ist die Routen-Vorlage (nie der konkrete Pfad mit Zonennamen).
@app.middleware("http")
async def _prometheus_http(request: Request, call_next):
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        prom.observe_http(request.method, prom.route_label(request.scope, request.url.path),
                          status_code, time.perf_counter() - start)


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


@app.exception_handler(secret_store.SecretsUnavailableError)
async def secrets_unavailable_handler(request: Request, exc: Exception):
    """Schreibzugriff auf ein Geheimnis ohne verfuegbaren Schluessel (F5 3.3) -> 503, nie Klartext speichern."""
    logger.error("Schreibzugriff auf Geheimnis ohne verfuegbaren Schluessel: %s", exc)
    return JSONResponse(status_code=503, content={"detail": SECRETS_UNAVAILABLE_DETAIL})


@app.exception_handler(SecretReentryRequired)
async def secret_reentry_handler(request: Request, exc: SecretReentryRequired):
    """Zielfeld geaendert, gespeichertes Geheimnis soll weiterverwendet werden -> 400 (B.2 [S3]).

    Spezifischer als der ValueError-Handler (Starlette waehlt den Handler entlang der MRO). Nur Feldnamen,
    nie Werte.
    """
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "code": "secret_reentry_required", "fields": list(exc.changed_fields)},
    )


# ========================
# API-Router (Discovery, vor allen Root-Routen und dem SPA-Catch-all)
# ========================
ROUTER_MODULES = include_router_modules(app, _iter_router_modules())


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
    current_user: User = Depends(get_admin_user),
):
    """Einfache Laufzeit-Metriken (für Admins) – ungefähre Request-Anzahl + Uptime."""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "uptime_seconds": int(time.time() - _METRICS_START),
        "api_request_count": _REQUEST_COUNT,
    }


METRICS_TOKEN_INVALID_DETAIL = "Ungültiger oder fehlender Metrik-Token"
METRICS_CONFIG_UNAVAILABLE_DETAIL = "Metrik-Konfiguration derzeit nicht lesbar."


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request):
    """Prometheus-Scrape (F13 3.8): nur mit Bearer-Scrape-Token; deaktiviert -> 404 wie eine unbekannte Seite."""
    from app.core.client_ip import get_client_ip

    try:
        cfg = await metrics_runtime.get_metrics_config()
    except metrics_runtime.ConfigUnavailable:
        return JSONResponse(status_code=503, content={"detail": METRICS_CONFIG_UNAVAILABLE_DETAIL})
    if not cfg.effective_enabled:
        return JSONResponse(status_code=404, content={"error": "Not found"})
    if not metrics_runtime.check_bearer(request.headers.get("authorization"), cfg):
        prom.METRICS_AUTH_FAILURES.inc()
        metrics_runtime.log_auth_failure(get_client_ip(request) or "unknown")
        return JSONResponse(
            status_code=401,
            content={"detail": METRICS_TOKEN_INVALID_DETAIL},
            headers={"WWW-Authenticate": 'Bearer realm="pdns-manager-metrics"'},
        )
    try:
        await asyncio.wait_for(metrics_runtime.refresh_runtime_gauges(cfg), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Metrik-Gauges: Aktualisierung nach 5 s abgebrochen – liefere letzte Werte")
    return Response(
        content=prom.render_latest(),
        media_type=prom.CONTENT_TYPE_LATEST,
        headers={"Cache-Control": "no-store"},
    )


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


# Hinweise auf einen vorgeschalteten Proxy: dann ist der direkte Peer nicht der eigentliche Absender.
_FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")
_MAX_HEALTH_MIGRATION_ERRORS = 50


def _is_loopback_peer(request: Request) -> bool:
    """Direkter Peer ist 127.0.0.0/8 bzw. ::1 und kein Proxy-Header gesetzt (Forwarded-Header werden
    bewusst nicht ausgewertet [S14]): Container-Healthcheck und ``update.sh`` per ``compose exec``."""
    host = request.client.host if request.client else None
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if not ip.is_loopback:
        return False
    return not any(request.headers.get(h) for h in _FORWARDING_HEADERS)


def _health_details() -> dict:
    """Zusatzfelder nur fuer lokale Abfragen: Geheimnis-Modus, Migrationsfehler, Schema, Hintergrund-Aufgaben."""
    secrets_info: dict = {"mode": secret_store.current_mode()}
    report = secret_store.startup_report()
    if report is not None:
        secrets_info.update({
            "fallback_reason": report.fallback_reason,
            "key_source": report.key_source,
            "key_fingerprint": report.fingerprint,
            "unreadable_values": len(report.unreadable),
            "issues": list(report.static_issues),
        })
    secrets_info["runtime_unreadable"] = secret_store.runtime_unreadable_counts()
    errors = [
        {"statement": str(stmt)[:300], "error": str(err)[:500]}
        for stmt, err in MIGRATION_ERRORS[:_MAX_HEALTH_MIGRATION_ERRORS]
    ]
    return {
        "secrets": secrets_info,
        "migration_errors": errors,
        "schema": {"ok": not MIGRATION_ERRORS, "migration_error_count": len(MIGRATION_ERRORS)},
        "background": background.state(),
        "servers_not_loaded": dict(getattr(pdns_manager, "unloaded", {}) or {}),
    }


@app.get("/health", tags=["Health"])
async def health_check(request: Request):
    """Health check: DB + PowerDNS-APIs.

    Oeffentlich nur ``status``, ``database``, ``servers`` (wie 2.4.1). ``degraded`` auch im Klartext-
    Fallback der Geheimnisse (Grund nur lokal). Zusatzfelder nur fuer Loopback-Peers [S14].
    """
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
        status_code, status = 503, "unhealthy"
    elif not pdns_ok or secret_store.current_mode() == "plaintext_fallback":
        status_code, status = 200, "degraded"
    else:
        status_code, status = 200, "healthy"
    body = {
        "status": status,
        "database": "connected" if db_ok else "disconnected",
        "servers": server_status,
    }
    if _is_loopback_peer(request):
        try:
            body.update(_health_details())
        except Exception as exc:  # noqa: BLE001 - Health darf an Zusatzinfos nie scheitern
            logger.warning("Health-Zusatzinfos nicht verfuegbar: %s", exc)
    return JSONResponse(status_code=status_code, content=body)


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
