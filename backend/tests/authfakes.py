"""Test-Hilfen fuer Auth-/Token-Tests ohne Datenbank (F14 9.1).

- ``FakeSession``: beantwortet SQLAlchemy-``select``-Statements anhand der abgefragten Entitaet
  (PanelToken -> Token-Zeile, User -> Benutzer, UserZoneAccess -> Zonenrechte, sonst leer); ``add``,
  ``flush``, ``commit``, ``rollback``, ``delete``, ``begin_nested`` sind No-Ops und werden gezaehlt.
- ``FakePDNS``: PowerDNS-Client mit drei Zonen; Schreibmethoden zaehlen Aufrufe in ``calls``.
- ``make_token``/``make_user``/``bearer``: Bausteine fuer Panel-Token-Anfragen.
- ``build_app``: Test-App, die nur die angegebenen Router einbindet (Prefix ``/api/v1``) und ``get_db``
  auf die ``FakeSession`` umbiegt.

Bewusst ohne Abhaengigkeit von ``app.main``: die Router werden einzeln eingebunden (Bauplan: Faelle fuer
zones/records/dnssec gehoeren zu W0-INT-BE2b).
"""
from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Iterable, Optional

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

from fastapi import FastAPI  # noqa: E402

from app.core.database import get_db  # noqa: E402
from app.models.models import PanelToken, User, UserZoneAccess  # noqa: E402

PLAIN = "dnsmgr_usr_" + "A" * 43
PLAIN_HASH = hashlib.sha256(PLAIN.encode("utf-8")).hexdigest()


class FakeResult:
    def __init__(self, rows: Optional[list] = None, rowcount: int = 0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None

    def first(self):
        if not self._rows:
            return None
        r = self._rows[0]
        return r if isinstance(r, tuple) else (r,)

    def all(self):
        return [r if isinstance(r, tuple) else (r,) for r in self._rows]

    def scalars(self):
        rows = list(self._rows)
        return SimpleNamespace(all=lambda: rows, first=lambda: rows[0] if rows else None)


def _bound_strings(stmt) -> list[str]:
    try:
        params = stmt.compile().params
    except Exception:  # noqa: BLE001
        return []
    return [v for v in params.values() if isinstance(v, str)]


class FakeSession:
    """Minimale AsyncSession fuer Router-Tests ohne DB."""

    def __init__(
        self,
        token_row: Optional[PanelToken] = None,
        user_row: Optional[User] = None,
        zone_access: Optional[Iterable[tuple[str, str]]] = None,
        extra: Optional[dict[Any, list]] = None,
    ):
        self.token_row = token_row
        self.user_row = user_row
        self.zone_access = list(zone_access or [])
        self.extra = dict(extra or {})
        self.added: list = []
        self.deleted: list = []
        self.executed: list = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    # --- Statements ---------------------------------------------------------------------------
    async def execute(self, stmt, *args, **kwargs):
        self.executed.append(stmt)
        if not hasattr(stmt, "column_descriptions"):
            return FakeResult([], rowcount=0)  # DELETE/UPDATE
        desc = stmt.column_descriptions
        entity = desc[0].get("entity") if desc else None
        names = [d.get("name") for d in desc]
        if entity in self.extra:
            return FakeResult(self.extra[entity])
        if entity is PanelToken:
            if names == ["PanelToken"]:
                return FakeResult([self.token_row] if self.token_row is not None else [])
            return FakeResult([])
        if entity is User:
            if names == ["User"]:
                return FakeResult([self.user_row] if self.user_row is not None else [])
            return FakeResult([])
        if entity is UserZoneAccess:
            if names == ["zone_name"]:
                return FakeResult([(z,) for z, _ in self.zone_access])
            if names == ["permission"]:
                wanted = {s.lower() for s in _bound_strings(stmt)}
                return FakeResult([p for z, p in self.zone_access if z.lower() in wanted])
            return FakeResult([(z, p) for z, p in self.zone_access])
        return FakeResult([])

    async def scalar(self, stmt, *args, **kwargs):
        return (await self.execute(stmt)).scalar()

    # --- Unit of Work -------------------------------------------------------------------------
    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushes += 1
        for i, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                try:
                    obj.id = 1000 + i
                except Exception:  # noqa: BLE001
                    pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        return None

    async def close(self):
        return None

    @asynccontextmanager
    async def _nested(self):
        yield self

    def begin_nested(self):
        return self._nested()


class FakePDNS:
    """PowerDNS-Client mit drei Zonen; Schreibaufrufe werden gezaehlt."""

    url = "http://pdns-intern.example:8081"

    def __init__(self, name: str = "srv1"):
        self.name = name
        self.calls: dict[str, int] = {}
        self.search_args: list[tuple] = []

    def _count(self, name: str):
        self.calls[name] = self.calls.get(name, 0) + 1

    async def list_zones(self, timeout: float = 30.0):
        return [{"name": "allowed.example."}, {"name": "other.example."}, {"name": "Third.Example."}]

    async def get_zone(self, zone_id, **kw):
        return {"name": zone_id, "rrsets": []}

    async def search(self, q, max_results=100, object_type="all"):
        self.search_args.append((q, max_results, object_type))
        return [
            {"object_type": "record", "name": "www.allowed.example.", "zone_id": "allowed.example."},
            {"object_type": "record", "name": "www.other.example.", "zone_id": "other.example."},
            {"object_type": "zone", "name": "Third.Example.", "zone_id": "Third.Example."},
        ]

    async def get_server_info(self, timeout: float = 30.0):
        return {"version": "4.9", "daemon_type": "authoritative"}

    async def get_statistics(self):
        return [{"name": "x", "value": "1"}]

    def __getattr__(self, item):
        # Alle uebrigen (schreibenden) Methoden: Aufruf zaehlen
        async def _write(*a, **k):
            self._count(item)
            return {}

        return _write


def make_user(*, role: str = "admin", uid: int = 1, username: str = "admin", **kw) -> User:
    return User(id=uid, username=username, role=role, is_active=True, hashed_password="x",
                email=None, display_name=username, **kw)


def make_token(**kw) -> PanelToken:
    data = dict(
        id=7, user_id=1, name="ci", token_prefix="dnsmgr_usr_test…", token_hash=PLAIN_HASH,
        is_active=True, revoked_at=None, expires_at=None, scope_zones=["allowed.example."],
        permission="manage", allow_admin=False, last_used_at=None, last_used_ip=None,
    )
    data.update(kw)
    return PanelToken(**data)


def bearer(token: str = PLAIN) -> dict:
    return {"Authorization": f"Bearer {token}"}


def build_app(session: FakeSession, *routers) -> FastAPI:
    """App mit den gegebenen Router-Modulen (``mod.router``) unter ``/api/v1`` und Fake-DB."""
    app = FastAPI()
    for r in routers:
        app.include_router(getattr(r, "router", r), prefix="/api/v1")

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return app
