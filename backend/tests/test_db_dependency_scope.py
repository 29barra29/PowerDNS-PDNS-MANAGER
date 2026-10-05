"""Schreibende Routen committen VOR dem Senden der Antwort (Bauplan B.7/A.4 [D2]).

Jede Route mit Methode POST/PUT/PATCH/DELETE der App: jede ``get_db``-Abhaengigkeit im (rekursiv aufgeloesten)
Dependant-Baum hat ``scope="function"`` (``DbWrite``, bzw. ``get_current_user``). Mit dem Default-Scope
``request`` liefe der Commit erst nach der Antwort – ein Commit-Fehler fuehrte dann zu „200 ohne Audit/Outbox“.

Ausnahmen nur fuer Streaming-Routen (dort muss die Session bis zum Ende der Antwort leben) – derzeit keine.
"""
from fastapi import APIRouter, Depends, FastAPI

from app.core.database import DbRead, DbWrite, get_db
from route_policy import iter_app_routes

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# (METHODE, Pfad) -> Begruendung; nur Streaming-Antworten
STREAMING_EXCEPTIONS: dict[tuple[str, str], str] = {}


def _db_dependencies(dependant):
    stack = list(dependant.dependencies)
    while stack:
        d = stack.pop()
        if d.call is get_db:
            yield d
        stack.extend(d.dependencies)


def scope_problems(app) -> list[str]:
    problems = []
    for r in iter_app_routes(app):
        if r.method not in MUTATING or (r.method, r.path) in STREAMING_EXCEPTIONS:
            continue
        for d in _db_dependencies(r.dependant):
            if getattr(d, "scope", None) != "function":
                problems.append(f"{r.method} {r.path}: get_db mit scope={getattr(d, 'scope', None)!r} – DbWrite verwenden")
    return problems


def test_all_mutating_routes_commit_before_response():
    from app.main import app

    problems = scope_problems(app)
    assert not problems, "\n".join(problems)


def test_exceptions_list_only_contains_existing_routes():
    from app.main import app

    keys = {(r.method, r.path) for r in iter_app_routes(app)}
    assert set(STREAMING_EXCEPTIONS) <= keys


def test_checker_detects_request_scoped_session():
    """Selbsttest: eine POST-Route mit DbRead (Default-Scope) wird gemeldet, DbWrite nicht."""
    r = APIRouter()

    @r.post("/bad")
    async def bad(db: DbRead):
        return {}

    @r.post("/bad-nested")
    async def bad_nested(x=Depends(lambda db=Depends(get_db): db)):
        return {}

    @r.post("/good")
    async def good(db: DbWrite):
        return {}

    @r.get("/read")
    async def read(db: DbRead):
        return {}

    app = FastAPI()
    app.include_router(r, prefix="/api/v1")
    problems = scope_problems(app)
    assert len(problems) == 2
    assert any("/api/v1/bad:" in p for p in problems) and any("/api/v1/bad-nested" in p for p in problems)


def test_mutating_routes_exist_in_all_core_routers():
    """Der Walk sieht die Router aller Kernmodule (Discovery) – sonst waere der Test wirkungslos."""
    from app.main import app

    paths = {r.path for r in iter_app_routes(app) if r.method in MUTATING}
    for prefix in ("/api/v1/setup", "/api/v1/auth", "/api/v1/zones", "/api/v1/records", "/api/v1/dnssec",
                   "/api/v1/settings", "/api/v1/templates", "/api/v1/acme"):
        assert any(p.startswith(prefix) for p in paths), prefix
