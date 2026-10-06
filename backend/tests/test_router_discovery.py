"""Router-Discovery und Struktur von ``main.py`` (Bauplan B.13 [F11, S11]).

- Jedes Modul in ``app/routers`` (ausser ``__init__``) hat ``router`` und genau eine Ordnung (Attribut
  ``ROUTER_ORDER`` ODER Eintrag in ``LEGACY_ROUTER_ORDER``); keine doppelten Ordnungen.
- Die App bindet die Router in dieser Ordnung ein, keine Route ist doppelt registriert.
- ``/metrics`` (und kuenftig ``/nic/update``) sowie alle API-Routen liegen vor dem SPA-Catch-all.
- Root-Routen (ausserhalb ``/api/v1``) haben nur GET/HEAD und stehen in der Route-Policy.
- Middleware-Reihenfolge und Exception-Handler laut B.13.
"""
from __future__ import annotations

import pkgutil
from types import ModuleType

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

import app.routers as routers_pkg
from app import main
from app.core import secrets as secret_store
from app.core.secret_mask import SecretReentryRequired
from app.services.pdns_client import PowerDNSAPIError

EXPECTED_LEGACY = {"setup": 10, "auth": 20, "servers": 40, "zones": 60, "records": 70, "dnssec": 80,
                   "search": 90, "settings": 100, "templates": 110, "acme": 120}
# Verbindliche Ordnungen neuer Module (B.13) – nur fuer bereits vorhandene Module geprueft
PLAN_ROUTER_ORDER = {"sso": 25, "panel_tokens": 27, "webhooks": 30, "history": 50, "propagation": 55, "lua": 75,
                     "settings_secrets": 101, "settings_sso": 102, "settings_dyndns": 103,
                     "settings_monitoring": 104, "settings_lua": 105, "dyndns": 130, "ptr": 135}


def _module_names() -> list[str]:
    return sorted(m.name for m in pkgutil.iter_modules(routers_pkg.__path__) if not m.name.startswith("_"))


def test_legacy_map_is_unchanged_plan_b13():
    assert main.LEGACY_ROUTER_ORDER == EXPECTED_LEGACY


def test_every_router_module_has_router_and_exactly_one_order():
    orders: dict[int, list[str]] = {}
    mods = main._iter_router_modules()
    assert [m.__name__.rsplit(".", 1)[-1] for m in mods] == _module_names()
    for mod in mods:
        name = mod.__name__.rsplit(".", 1)[-1]
        assert isinstance(getattr(mod, "router", None), APIRouter), f"{name}: router fehlt"
        own = getattr(mod, "ROUTER_ORDER", None)
        assert own is not None or name in main.LEGACY_ROUTER_ORDER, f"{name}: keine Ordnung"
        if own is not None:
            assert isinstance(own, int) and not isinstance(own, bool), f"{name}: ROUTER_ORDER keine Ganzzahl"
            assert name not in main.LEGACY_ROUTER_ORDER, f"{name}: ROUTER_ORDER und LEGACY-Eintrag"
            if name in PLAN_ROUTER_ORDER:
                assert own == PLAN_ROUTER_ORDER[name], f"{name}: ROUTER_ORDER {own} statt {PLAN_ROUTER_ORDER[name]}"
        orders.setdefault(main._router_order(mod), []).append(name)
    dup = {k: v for k, v in orders.items() if len(v) > 1}
    assert not dup, f"doppelte Ordnungen: {dup}"
    assert set(main.LEGACY_ROUTER_ORDER) <= set(_module_names()), "LEGACY-Eintrag ohne Modul"


def test_app_includes_routers_in_order():
    names = [m.__name__.rsplit(".", 1)[-1] for m in main.ROUTER_MODULES]
    assert names == sorted(names, key=lambda n: main._router_order(main.ROUTER_MODULES[names.index(n)]))
    assert names[:6] == ["setup", "auth", "sso", "panel_tokens", "webhooks", "servers"]
    assert set(names) == set(_module_names())
    # Reihenfolge in app.routes entspricht der Discovery (ein _IncludedRouter je Modul, dann Root-Routen)
    included = [getattr(r, "original_router", None) for r in main.app.routes]
    pos = [included.index(m.router) for m in main.ROUTER_MODULES]
    assert pos == sorted(pos)


def test_no_route_registered_twice():
    keys = []
    for ctx in iter_route_contexts(main.app.routes):
        for method in ctx.methods or ():
            keys.append((method, ctx.path))
    dup = sorted({k for k in keys if keys.count(k) > 1})
    assert not dup, dup


def test_root_routes_before_spa_catch_all_and_get_only():
    from route_policy import ROOT_ROUTE_METHODS, load_policy

    ctxs = list(iter_route_contexts(main.app.routes))
    paths = [c.path for c in ctxs]
    spa = paths.index("/{path:path}")
    assert spa == len(paths) - 1, "SPA-Catch-all muss die letzte Route sein"
    for p in ("/metrics", "/api/v1/metrics", "/health", "/api"):
        assert p in paths and paths.index(p) < spa, p
    if "/nic/update" in paths:
        assert paths.index("/nic/update") < spa
    policy = load_policy().policy
    for c in ctxs:
        if not isinstance(getattr(c, "original_route", c), APIRoute) or c.path == "/{path:path}":
            continue
        if c.path.startswith("/api/v1/"):
            continue
        assert set(c.methods or ()) <= ROOT_ROUTE_METHODS, f"Root-Route {c.path} mit {c.methods}"
        for m in c.methods or ():
            assert (m, c.path) in policy, f"Root-Route {m} {c.path} fehlt in ROUTE_POLICY"


def test_metrics_route_is_not_html(monkeypatch):
    from fastapi.testclient import TestClient

    from app.services import metrics_runtime

    async def disabled(force=False):
        return metrics_runtime.MetricsConfig(False, None, None, False, True)

    monkeypatch.setattr(metrics_runtime, "get_metrics_config", disabled)
    r = TestClient(main.app).get("/metrics")
    assert r.status_code == 404 and r.json() == {"error": "Not found"}
    assert "text/html" not in r.headers.get("content-type", "")


# --------------------------------------------------------------------------- Mechanik mit Testmodulen
def _mod(name: str, order=None, prefix: str = "", roots=()) -> ModuleType:
    mod = ModuleType(f"app.routers.{name}")
    r = APIRouter(prefix=prefix or f"/{name}")

    @r.get("/ping")
    async def ping():
        return {"mod": name}

    mod.router = r
    if order is not None:
        mod.ROUTER_ORDER = order
    if roots:
        mod.root_routers = list(roots)
    return mod


def test_include_router_modules_orders_and_mounts_root_routers():
    root = APIRouter()

    @root.get("/nic/update")
    async def nic():
        return {"ok": True}

    target = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    mods = [_mod("zzz", order=5), _mod("auth"), _mod("dyn", order=130, roots=[root]), _mod("setup")]
    ordered = main.include_router_modules(target, mods)
    assert [m.__name__.rsplit(".", 1)[-1] for m in ordered] == ["zzz", "setup", "auth", "dyn"]
    paths = [c.path for c in iter_route_contexts(target.routes)]
    assert paths == ["/api/v1/zzz/ping", "/api/v1/setup/ping", "/api/v1/auth/ping", "/api/v1/dyn/ping", "/nic/update"]


def test_module_without_order_fails():
    with pytest.raises(KeyError):
        main._router_order(_mod("unbekannt"))
    zero = _mod("null", order=0)
    assert main._router_order(zero) == 0  # 0 ist eine gueltige Ordnung (kein "or"-Fallback)


# --------------------------------------------------------------------------- Middleware / Handler
def test_middleware_order_outer_to_inner():
    names = [getattr(m.kwargs.get("dispatch"), "__name__", None) or m.cls.__name__ for m in main.app.user_middleware]
    assert names[:3] == ["_prometheus_http", "_security_headers", "_csrf_guard"]
    assert all(n == "CORSMiddleware" for n in names[3:])


def test_exception_handlers_registered():
    handlers = main.app.exception_handlers
    for exc in (PowerDNSAPIError, ValueError, secret_store.SecretsUnavailableError, SecretReentryRequired):
        assert exc in handlers, exc


def _probe_app(exc):
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.exception_handlers.update(main.app.exception_handlers)

    @app.get("/boom")
    async def boom():
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def test_secrets_unavailable_maps_to_503():
    r = _probe_app(secret_store.SecretsUnavailableError("kein Schluessel")).get("/boom")
    assert r.status_code == 503
    assert r.json() == {"detail": main.SECRETS_UNAVAILABLE_DETAIL}
    assert "kein Schluessel" not in r.text


def test_secret_reentry_maps_to_400_with_field_names_only():
    r = _probe_app(SecretReentryRequired(changed_fields=("host",))).get("/boom")
    assert r.status_code == 400
    body = r.json()
    assert body["detail"] == SecretReentryRequired.DEFAULT_MESSAGE
    assert body["code"] == "secret_reentry_required" and body["fields"] == ["host"]
    # normaler ValueError bleibt beim alten Format
    r = _probe_app(ValueError("x")).get("/boom")
    assert r.status_code == 400 and r.json() == {"error": "x"}


def test_routers_package_documents_convention():
    doc = routers_pkg.__doc__ or ""
    for word in ("ROUTER_ORDER", "LEGACY_ROUTER_ORDER", "root_routers", "GET/HEAD"):
        assert word in doc


def test_router_modules_never_register_spa_path():
    """Kein Router-Modul registriert den Pfad des SPA-Catch-alls (sonst waere die SPA tot)."""
    for mod in main.ROUTER_MODULES:
        for r in mod.router.routes:
            assert getattr(r, "path", "") != "/{path:path}", mod.__name__
