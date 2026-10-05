"""Prometheus-Metriken des Panels (Praefix ``pdnsmgr_``).

Keine Imports aus ``app.services``/``app.routers`` (zyklenfrei: ``pdns_client``
importiert dieses Modul). Alle Helfer fangen Exceptions – Metriken duerfen nie einen
Request brechen. Die Registry ist prozesslokal (ein Worker-Prozess).
"""
from __future__ import annotations

import logging

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

NS = "pdnsmgr"
API_PREFIX = "/api/v1"
REGISTRY = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

_HTTP_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

APP_INFO = Gauge("info", "PDNS Manager Build-Info", ["version"], namespace=NS, registry=REGISTRY)
HTTP_REQUESTS = Counter("http_requests", "HTTP-Anfragen", ["method", "route", "status"], namespace=NS, registry=REGISTRY)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "Dauer der HTTP-Anfragen", ["method", "route"],
    buckets=_HTTP_BUCKETS, namespace=NS, registry=REGISTRY,
)
PDNS_API_REQUESTS = Counter(
    "pdns_api_requests", "Aufrufe der PowerDNS-API", ["server", "method", "status"], namespace=NS, registry=REGISTRY,
)
PDNS_API_DURATION = Histogram(
    "pdns_api_request_duration_seconds", "Dauer der PowerDNS-API-Aufrufe", ["server"],
    buckets=_HTTP_BUCKETS, namespace=NS, registry=REGISTRY,
)
PDNS_SERVER_UP = Gauge("pdns_server_up", "PowerDNS-API erreichbar (1/0), gecacht", ["server"], namespace=NS, registry=REGISTRY)
PDNS_ZONES = Gauge("pdns_zones", "Anzahl Zonen je PowerDNS-Server, gecacht", ["server"], namespace=NS, registry=REGISTRY)
PDNS_SERVERS = Gauge("pdns_servers_configured", "Konfigurierte aktive PowerDNS-Server", namespace=NS, registry=REGISTRY)
DATABASE_UP = Gauge("database_up", "Datenbank erreichbar (1/0), beim Scrape gemessen", namespace=NS, registry=REGISTRY)
WEBHOOK_DELIVERIES = Counter(
    "webhook_deliveries", "Webhook-Zustellversuche nach Ergebnis", ["status"], namespace=NS, registry=REGISTRY,
)
WEBHOOK_PENDING = Gauge(
    "webhook_deliveries_pending", "Offene Webhook-Zustellungen (queued/failed/in_progress)", namespace=NS, registry=REGISTRY,
)
DYNDNS_UPDATES = Counter("dyndns_updates", "DynDNS-Updates nach Ergebnis", ["result"], namespace=NS, registry=REGISTRY)
LOGIN_ATTEMPTS = Counter("login_attempts", "Anmeldeversuche", ["method", "result"], namespace=NS, registry=REGISTRY)
PROPAGATION_CHECKS = Counter("propagation_checks", "Propagations-Pruefungen", ["result"], namespace=NS, registry=REGISTRY)
METRICS_AUTH_FAILURES = Counter("metrics_auth_failures", "Abgelehnte /metrics-Abrufe", namespace=NS, registry=REGISTRY)

LOGIN_METHODS = ("password", "totp", "passkey", "oidc", "ldap")
LOGIN_RESULTS = ("success", "failure", "rate_limited", "denied", "2fa_required")
# Plan B.10/B.16: Label-Werte aus dem Worker-Status abgeleitet; "failed" wird nicht als Label genutzt.
WEBHOOK_STATUSES = ("success", "retry", "dead", "cancelled")
WEBHOOK_STATUS_MAP = {
    "succeeded": "success",
    "success": "success",
    "failed": "retry",  # Versuch fehlgeschlagen, erneut eingeplant
    "retry": "retry",
    "dead": "dead",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "discarded": "cancelled",
}
DYNDNS_RESULTS = ("good", "nochg", "badauth", "nohost", "notfqdn", "abuse", "badagent", "dnserr", "911", "other")
PROPAGATION_RESULTS = ("in_sync", "out_of_sync", "failed", "rate_limited", "cached")
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def init_static_series(version: str) -> None:
    """Build-Info setzen und alle festen Label-Kombinationen mit 0 vorbelegen (idempotent).

    Dadurch liefert ``increase()`` in Prometheus schon ab dem ersten Ereignis Werte.
    """
    try:
        APP_INFO.labels(str(version or "unknown")).set(1)
        for m in LOGIN_METHODS:
            for r in LOGIN_RESULTS:
                LOGIN_ATTEMPTS.labels(m, r)
        for s in WEBHOOK_STATUSES:
            WEBHOOK_DELIVERIES.labels(s)
        for r in DYNDNS_RESULTS:
            DYNDNS_UPDATES.labels(r)
        for r in PROPAGATION_RESULTS:
            PROPAGATION_CHECKS.labels(r)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Metrik-Initialisierung fehlgeschlagen: %s", exc)


def route_label(scope: dict, path: str) -> str:
    """Routen-Template als Label (nie der konkrete Pfad – Zonennamen bleiben privat)."""
    try:
        route = scope.get("route")
        tpl = getattr(route, "path", None)
        if tpl:
            if tpl == "/{path:path}":
                return "__spa__"
            # FastAPI 0.141: route.path enthaelt das include_router-Praefix nicht
            if path.startswith(API_PREFIX + "/") and not tpl.startswith("/api/"):
                tpl = API_PREFIX + tpl
            return tpl[:160]
        if path.startswith(("/assets/", "/uploads/")):
            return "__static__"
    except Exception as exc:  # noqa: BLE001
        logger.debug("route_label fehlgeschlagen: %s", exc)
    return "__unmatched__"


def _method(method: str) -> str:
    m = (method or "").upper()
    return m if m in _METHODS else "OTHER"


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    try:
        m = _method(method)
        HTTP_REQUESTS.labels(m, route, str(int(status))).inc()
        HTTP_DURATION.labels(m, route).observe(max(0.0, float(seconds)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("observe_http fehlgeschlagen: %s", exc)


def observe_pdns_request(server: str, method: str, status_label: str, seconds: float) -> None:
    """Zaehlt einen PowerDNS-API-Aufruf (``status_label``: 2xx/4xx/5xx/connect_error/timeout/transport_error/error)."""
    try:
        PDNS_API_REQUESTS.labels(str(server), _method(method), str(status_label)).inc()
        PDNS_API_DURATION.labels(str(server)).observe(max(0.0, float(seconds)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("observe_pdns_request fehlgeschlagen: %s", exc)


def record_login(method: str, result: str) -> None:
    """Login-Versuch zaehlen; unbekannte Methoden/Ergebnisse werden ignoriert."""
    try:
        if method not in LOGIN_METHODS or result not in LOGIN_RESULTS:
            logger.debug("record_login: unbekannte Kombination %r/%r ignoriert", method, result)
            return
        LOGIN_ATTEMPTS.labels(method, result).inc()
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_login fehlgeschlagen: %s", exc)


def record_webhook_delivery(status: str) -> None:
    """Zustellversuch zaehlen. Worker-Status: succeeded->success, failed->retry, dead, cancelled."""
    try:
        label = WEBHOOK_STATUS_MAP.get((status or "").strip().lower())
        if label is None:
            logger.debug("record_webhook_delivery: unbekannter Status %r ignoriert", status)
            return
        WEBHOOK_DELIVERIES.labels(label).inc()
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_webhook_delivery fehlgeschlagen: %s", exc)


def record_dyndns(result: str) -> None:
    """DynDNS-Antwort zaehlen (erstes Token, z. B. ``good 192.0.2.1`` -> ``good``)."""
    try:
        token = (str(result or "").split() or [""])[0].lower()
        if token not in DYNDNS_RESULTS:
            token = "other"
        DYNDNS_UPDATES.labels(token).inc()
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_dyndns fehlgeschlagen: %s", exc)


def record_propagation(result: str) -> None:
    try:
        if result not in PROPAGATION_RESULTS:
            logger.debug("record_propagation: unbekanntes Ergebnis %r ignoriert", result)
            return
        PROPAGATION_CHECKS.labels(result).inc()
    except Exception as exc:  # noqa: BLE001
        logger.debug("record_propagation fehlgeschlagen: %s", exc)


def render_latest() -> bytes:
    return generate_latest(REGISTRY)


init_static_series(settings.APP_VERSION)
