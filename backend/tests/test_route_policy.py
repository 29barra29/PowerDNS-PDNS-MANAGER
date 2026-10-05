"""Route-Policy-Test (Bauplan B.13 [S11], F14 9.2 Nr. 1): Walk ueber ALLE APIRoutes der App.

- jede Route (auch Root-Routen ohne ``/api/v1``) ist in genau einer ``tests/route_policy/<ws>.py`` klassifiziert,
- die Kategorie passt zu den Dependencies bzw. zum Endpunkt-Quelltext,
- Root-Routen haben nur GET/HEAD (der CSRF-Guard deckt nur ``/api/`` ab),
- keine verwaisten Eintraege (ausser ``PLANNED``), keine doppelten Eintraege, keine doppelt registrierten Routen.

Die Pruefroutinen liegen in ``tests/route_policy/__init__.py`` und werden hier zusaetzlich gegen eine Mini-App
mit absichtlichen Fehlern getestet (damit der Test nicht still gruen bleibt).
"""
import os

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import warnings  # noqa: E402

from fastapi import APIRouter, Depends, FastAPI  # noqa: E402

import route_policy as rp  # noqa: E402
from app.core.auth import (  # noqa: E402
    assert_zone_access,
    get_admin_session_user,
    get_admin_user,
    get_current_user,
    get_session_user,
)
from app.main import app  # noqa: E402


def _fmt(lines):
    return "\n".join(f"  - {x}" for x in lines)


def test_policy_files_are_consistent():
    ps = rp.load_policy()
    assert not ps.errors, "Route-Policy-Dateien fehlerhaft:\n" + _fmt(ps.errors)
    assert "base" in set(ps.origins.values())


def test_every_route_is_classified_and_enforced():
    ps = rp.load_policy()
    rep = rp.check_app(app, ps)
    for w in rep.warnings:
        warnings.warn(w)
    assert not rep.errors, "Route-Policy verletzt:\n" + _fmt(rep.errors)


def test_walk_covers_included_and_root_routes():
    keys = {r.key for r in rp.iter_app_routes(app)}
    # eingebundene Router (mit Prefix) ...
    assert ("GET", "/api/v1/auth/me") in keys
    assert ("POST", "/api/v1/auth/me/panel-tokens") in keys
    assert ("PUT", "/api/v1/auth/me/webhooks/{webhook_id}") in keys
    # ... und Root-Routen direkt an der App
    assert ("GET", "/health") in keys and ("GET", "/api") in keys
    # SPA-Catch-all ist ausgenommen
    assert not any(path == rp.SPA_CATCH_ALL for _, path in keys)


def test_root_routes_are_read_only_in_app():
    bad = [r.key for r in rp.iter_app_routes(app)
           if not r.path.startswith(rp.API_PREFIX + "/") and r.method not in rp.ROOT_ROUTE_METHODS]
    assert not bad, bad


# ---------------------------------------------------------------------------------------------
# Selbsttest der Pruefroutinen gegen eine Mini-App
# ---------------------------------------------------------------------------------------------
def _mini_app():
    r = APIRouter(prefix="/x")

    @r.get("/open")
    async def open_route():
        return {}

    @r.get("/me")
    async def me(u=Depends(get_current_user)):
        return {}

    @r.put("/me")
    async def me_put(u=Depends(get_session_user)):
        return {}

    @r.get("/adm")
    async def adm(u=Depends(get_admin_user)):
        return {}

    @r.get("/adm-session")
    async def adm_session(u=Depends(get_admin_session_user)):
        return {}

    @r.get("/zone/{z}")
    async def zone_ok(z: str, u=Depends(get_current_user)):
        await assert_zone_access(None, u, z)
        return {}

    @r.get("/zone-missing/{z}")
    async def zone_missing(z: str, u=Depends(get_current_user)):
        return {}

    a = FastAPI()
    a.include_router(r, prefix="/api/v1")

    @a.get("/rootok")
    async def rootok():
        return {}

    @a.post("/rootbad")
    async def rootbad():
        return {}

    @a.get("/{path:path}")
    async def spa(path: str):
        return {}

    return a


class _Mod:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _policy(**extra):
    A = "/api/v1/x"
    table = {
        ("GET", f"{A}/open"): "public",
        ("GET", f"{A}/me"): "user",
        ("PUT", f"{A}/me"): "session",
        ("GET", f"{A}/adm"): "admin",
        ("GET", f"{A}/adm-session"): "session",
        ("GET", f"{A}/zone/{{z}}"): "zone",
        ("GET", f"{A}/zone-missing/{{z}}"): "zone",
        ("GET", "/rootok"): "public",
        ("POST", "/rootbad"): "public",
    }
    table.update(extra)
    return table


def test_checker_detects_errors_in_mini_app():
    ps = rp.merge_policies([("mini", _Mod(ROUTE_POLICY=_policy()))])
    rep = rp.check_app(_mini_app(), ps)
    text = "\n".join(rep.errors)
    assert "Root-Route POST /rootbad" in text
    assert "GET /api/v1/x/zone-missing/{z}: Kategorie zone, aber kein assert_zone_access(" in text
    # korrekt klassifizierte Routen erzeugen keinen Fehler, der SPA-Catch-all wird nicht verlangt
    assert "/api/v1/x/zone/{z}:" not in text and "/{path:path}" not in text
    assert "GET /api/v1/x/me:" not in text and "GET /api/v1/x/adm:" not in text


def test_checker_detects_unclassified_wrong_and_stale():
    table = _policy()
    del table[("GET", "/api/v1/x/me")]
    table[("GET", "/api/v1/x/open")] = "user"            # falsch: public-Route als user
    table[("GET", "/api/v1/x/adm")] = "public"            # falsch: Admin-Dependency im Baum
    table[("GET", "/api/v1/x/gone")] = "user"             # verwaist
    table[("GET", "/api/v1/x/later")] = "user"            # verwaist, aber geplant
    ps = rp.merge_policies([("mini", _Mod(ROUTE_POLICY=table, PLANNED={("GET", "/api/v1/x/later")}))])
    text = "\n".join(rp.check_app(_mini_app(), ps).errors)
    assert "Route GET /api/v1/x/me fehlt in ROUTE_POLICY" in text
    assert "GET /api/v1/x/open: Kategorie user, aber get_current_user nicht direkt" in text
    assert "GET /api/v1/x/adm: Kategorie public, aber Auth-Dependency im Baum: get_admin_user, get_current_user" in text
    assert "ROUTE_POLICY-Eintrag GET /api/v1/x/gone" in text
    assert "/api/v1/x/later" not in text


def test_checker_detects_duplicates_and_bad_categories():
    a = _Mod(ROUTE_POLICY={("GET", "/api/v1/x/open"): "public"})
    b = _Mod(ROUTE_POLICY={("GET", "/api/v1/x/open"): "public", ("GET", "/api/v1/x/me"): "everyone",
                           ("get", "/lower"): "user"})
    ps = rp.merge_policies([("a", a), ("b", b)])
    text = "\n".join(ps.errors)
    assert "doppelt klassifiziert (route_policy/a.py und route_policy/b.py)" in text
    assert "unbekannte Kategorie 'everyone'" in text
    assert "ungueltiger Schluessel ('get', '/lower')" in text


def test_checker_transitional_and_token_scope():
    table = _policy()
    key = ("GET", "/api/v1/x/adm")
    ps = rp.merge_policies([("mini", _Mod(ROUTE_POLICY=table, TOKEN_SCOPE_REQUIRED={key}))])
    text = "\n".join(rp.check_app(_mini_app(), ps).errors)
    assert "GET /api/v1/x/adm: zonenbezogene Admin-Route ohne assert_token_scope(" in text
    # als TRANSITIONAL: kein Fehler mehr; ein bereits passender TRANSITIONAL-Eintrag erzeugt eine Warnung
    ps = rp.merge_policies([("mini", _Mod(ROUTE_POLICY=table, TOKEN_SCOPE_REQUIRED={key},
                                          TRANSITIONAL={key: "Test", ("GET", "/api/v1/x/me"): "Test"}))])
    rep = rp.check_app(_mini_app(), ps)
    assert not any("/api/v1/x/adm:" in e for e in rep.errors)
    assert any("GET /api/v1/x/me ist erledigt" in w for w in rep.warnings)
