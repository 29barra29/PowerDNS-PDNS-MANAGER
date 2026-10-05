"""Route-Policy: jede Route der App ist bewusst einer Zugriffskategorie zugeordnet (Bauplan Regel 6, B.13 [S11]).

Slot-Dateien: ``tests/route_policy/<ws>.py`` (je Workstream eine Datei, ``base.py`` = Stand 2.4.1 + Welle 0b).
Jede Datei definiert:

- ``ROUTE_POLICY: dict[tuple[str, str], str]`` – ``(METHODE, voller Pfad)`` -> Kategorie, z. B.
  ``("GET", "/api/v1/auth/me"): "user"``. Pfade so, wie FastAPI sie registriert (inkl. ``/api/v1`` und
  ``{param:path}``); Root-Routen ohne Prefix (``/health``, ``/metrics``, ``/nic/update``) ebenso.
- optional ``PLANNED: set[tuple[str, str]]`` – klassifizierte Routen, die erst ein spaeterer Workstream anlegt
  (Platzhalter; fehlen sie in der App, ist das kein Fehler).
- optional ``TOKEN_SCOPE_REQUIRED: set[tuple[str, str]]`` – Admin-Routen mit Zonenbezug, deren Endpunkt
  zusaetzlich ``assert_token_scope(`` aufrufen muss (F14 5.6, Defense in Depth).
- optional ``TRANSITIONAL: dict[tuple[str, str], str]`` – bekannte, begruendete Abweichung zwischen Kategorie und
  Code, die ein benannter Workstream derselben Welle behebt. Fuer diese Routen wird nur die Einordnung geprueft;
  sobald der Code passt, meldet der Test eine Warnung (Eintrag entfernen).

Kategorien (F14 9.2 Nr. 1, Anhang A3):

``public``   keine der vier Auth-Dependencies im (rekursiv aufgeloesten) Dependency-Baum
``own_auth`` wie ``public``, aber mit eigener Token-Pruefung im Endpunkt (ACME, DynDNS, Metrics-Scrape)
``user``     ``get_current_user`` direkt, zonenunabhaengig (Panel-Tokens erlaubt)
``session``  ``get_session_user`` oder ``get_admin_session_user`` direkt (nie per Token)
``admin``    ``get_admin_user`` direkt (Token nur mit ``allow_admin``)
``zone``     ``get_current_user`` direkt UND ``assert_zone_access(`` im Endpunkt-Quelltext

Root-Routen (Pfad ausserhalb ``/api/v1/``) duerfen nur GET/HEAD haben – der CSRF-Guard deckt nur ``/api/`` ab.
Ausgenommen vom Walk sind Mounts (``/assets``, ``/uploads``) und der SPA-Catch-all ``/{path:path}``.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Iterator

API_PREFIX = "/api/v1"
CATEGORIES = frozenset({"public", "own_auth", "user", "session", "admin", "zone"})
ROOT_ROUTE_METHODS = frozenset({"GET", "HEAD"})
SPA_CATCH_ALL = "/{path:path}"

RouteKey = tuple[str, str]


@dataclass
class PolicySet:
    """Zusammengefuehrte Slot-Dateien."""

    policy: dict[RouteKey, str] = field(default_factory=dict)
    origins: dict[RouteKey, str] = field(default_factory=dict)
    planned: set[RouteKey] = field(default_factory=set)
    token_scope_required: set[RouteKey] = field(default_factory=set)
    transitional: dict[RouteKey, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _policy_modules() -> list[tuple[str, Any]]:
    mods = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        mods.append((info.name, importlib.import_module(f"{__name__}.{info.name}")))
    return mods


def _valid_key(key: Any) -> bool:
    return (
        isinstance(key, tuple)
        and len(key) == 2
        and all(isinstance(x, str) for x in key)
        and key[0] == key[0].upper()
        and key[1].startswith("/")
    )


def merge_policies(modules: list[tuple[str, Any]]) -> PolicySet:
    """Fuehrt Slot-Module zusammen; doppelte Eintraege und ungueltige Kategorien landen in ``errors``."""
    ps = PolicySet()
    for name, mod in modules:
        table = getattr(mod, "ROUTE_POLICY", None)
        if not isinstance(table, dict):
            ps.errors.append(f"route_policy/{name}.py: ROUTE_POLICY (dict) fehlt")
            continue
        for key, cat in table.items():
            if not _valid_key(key):
                ps.errors.append(f"route_policy/{name}.py: ungueltiger Schluessel {key!r} (erwartet (METHODE, /pfad))")
                continue
            if cat not in CATEGORIES:
                ps.errors.append(f"route_policy/{name}.py: {key[0]} {key[1]} hat unbekannte Kategorie {cat!r}")
                continue
            if key in ps.policy:
                ps.errors.append(
                    f"Route {key[0]} {key[1]} ist doppelt klassifiziert "
                    f"(route_policy/{ps.origins[key]}.py und route_policy/{name}.py)"
                )
                continue
            ps.policy[key] = cat
            ps.origins[key] = name
        for attr, target in (("PLANNED", ps.planned), ("TOKEN_SCOPE_REQUIRED", ps.token_scope_required)):
            for key in getattr(mod, attr, set()) or set():
                if not _valid_key(key):
                    ps.errors.append(f"route_policy/{name}.py: {attr} enthaelt ungueltigen Schluessel {key!r}")
                    continue
                target.add(key)
        for key, reason in (getattr(mod, "TRANSITIONAL", {}) or {}).items():
            if not _valid_key(key) or not str(reason or "").strip():
                ps.errors.append(f"route_policy/{name}.py: TRANSITIONAL-Eintrag {key!r} ohne gueltigen Schluessel/Grund")
                continue
            ps.transitional[key] = str(reason)
    for key in ps.planned | ps.token_scope_required | set(ps.transitional):
        if key not in ps.policy:
            ps.errors.append(f"{key[0]} {key[1]}: in PLANNED/TOKEN_SCOPE_REQUIRED/TRANSITIONAL, aber ohne Kategorie")
    return ps


def load_policy() -> PolicySet:
    return merge_policies(_policy_modules())


# ---------------------------------------------------------------------------------------------
# Walk ueber die Routen der App
# ---------------------------------------------------------------------------------------------
@dataclass
class RouteInfo:
    method: str
    path: str
    endpoint: Any
    dependant: Any

    @property
    def key(self) -> RouteKey:
        return (self.method, self.path)


def _iter_route_contexts(routes) -> Iterator[Any]:
    """Effektive Routen inkl. eingebundener Router (FastAPI >= 0.14x: ``_IncludedRouter``)."""
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:  # aeltere FastAPI-Versionen: APIRoutes liegen flach mit vollem Pfad in app.routes
        yield from routes
        return
    yield from iter_route_contexts(routes)


def iter_app_routes(app) -> list[RouteInfo]:
    """Alle APIRoutes der App (je Methode ein Eintrag) – ohne Mounts und ohne SPA-Catch-all."""
    from fastapi.routing import APIRoute

    out: list[RouteInfo] = []
    for rc in _iter_route_contexts(app.routes):
        original = getattr(rc, "original_route", rc)
        if not isinstance(original, APIRoute):
            continue
        path = rc.path
        if path == SPA_CATCH_ALL:
            continue
        for method in sorted(rc.methods or ()):
            out.append(RouteInfo(method=method.upper(), path=path, endpoint=rc.endpoint, dependant=rc.dependant))
    return out


# ---------------------------------------------------------------------------------------------
# Pruefungen (liefern Fehlerlisten – testbar auch gegen Mini-Apps)
# ---------------------------------------------------------------------------------------------
def auth_dependencies() -> dict[str, Any]:
    from app.core import auth

    return {
        "get_current_user": auth.get_current_user,
        "get_session_user": auth.get_session_user,
        "get_admin_session_user": auth.get_admin_session_user,
        "get_admin_user": auth.get_admin_user,
    }


def _direct_calls(dependant) -> list[Any]:
    return [d.call for d in getattr(dependant, "dependencies", []) or []]


def _tree_calls(dependant) -> list[Any]:
    calls: list[Any] = []
    stack = list(getattr(dependant, "dependencies", []) or [])
    while stack:
        d = stack.pop()
        calls.append(d.call)
        stack.extend(getattr(d, "dependencies", []) or [])
    return calls


def _source(endpoint) -> str:
    try:
        return inspect.getsource(endpoint)
    except (OSError, TypeError):
        return ""


def route_category_problem(route: RouteInfo, category: str, *, needs_token_scope: bool = False) -> str | None:
    """``None``, wenn der Code zur Kategorie passt, sonst eine Beschreibung der Abweichung."""
    auth = auth_dependencies()
    direct = _direct_calls(route.dependant)
    tree = _tree_calls(route.dependant)

    def has_direct(*names: str) -> bool:
        return any(any(c is auth[n] for c in direct) for n in names)

    if category in ("public", "own_auth"):
        used = sorted({n for n, fn in auth.items() if any(c is fn for c in tree)})
        if used:
            return f"Kategorie {category}, aber Auth-Dependency im Baum: {', '.join(used)}"
    elif category == "session":
        if not has_direct("get_session_user", "get_admin_session_user"):
            return "Kategorie session, aber weder get_session_user noch get_admin_session_user direkt"
    elif category == "admin":
        if not has_direct("get_admin_user"):
            return "Kategorie admin, aber get_admin_user nicht direkt"
    elif category == "user":
        if not has_direct("get_current_user"):
            return "Kategorie user, aber get_current_user nicht direkt"
    elif category == "zone":
        if not has_direct("get_current_user"):
            return "Kategorie zone, aber get_current_user nicht direkt"
        if "assert_zone_access(" not in _source(route.endpoint):
            return "Kategorie zone, aber kein assert_zone_access( im Endpunkt"
    if needs_token_scope and "assert_token_scope(" not in _source(route.endpoint):
        return "zonenbezogene Admin-Route ohne assert_token_scope( im Endpunkt"
    return None


@dataclass
class PolicyReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def check_app(app, ps: PolicySet) -> PolicyReport:
    """Vollstaendigkeit, Altlasten, Kategorien und Root-Regel einer App gegen die Policy pruefen."""
    rep = PolicyReport(errors=list(ps.errors))
    routes = iter_app_routes(app)
    seen: dict[RouteKey, int] = {}
    for r in routes:
        seen[r.key] = seen.get(r.key, 0) + 1
    for key, n in sorted(seen.items()):
        if n > 1:
            rep.errors.append(f"Route {key[0]} {key[1]} ist {n}x registriert (Shadowing)")

    for r in routes:
        if not r.path.startswith(API_PREFIX + "/") and r.method not in ROOT_ROUTE_METHODS:
            rep.errors.append(f"Root-Route {r.method} {r.path}: ausserhalb von {API_PREFIX} sind nur GET/HEAD erlaubt")
        cat = ps.policy.get(r.key)
        if cat is None:
            rep.errors.append(f"Route {r.method} {r.path} fehlt in ROUTE_POLICY – Token-Verhalten festlegen")
            continue
        problem = route_category_problem(r, cat, needs_token_scope=r.key in ps.token_scope_required)
        if r.key in ps.transitional:
            if problem is None:
                rep.warnings.append(
                    f"TRANSITIONAL-Eintrag {r.method} {r.path} ist erledigt und kann entfernt werden "
                    f"(route_policy/{ps.origins.get(r.key, '?')}.py)"
                )
            continue
        if problem:
            rep.errors.append(f"Route {r.method} {r.path}: {problem} (route_policy/{ps.origins[r.key]}.py)")

    for key in sorted(set(ps.policy) - set(seen) - ps.planned):
        rep.errors.append(
            f"ROUTE_POLICY-Eintrag {key[0]} {key[1]} (route_policy/{ps.origins[key]}.py) hat keine Route "
            "– entfernen oder in PLANNED aufnehmen"
        )
    return rep
