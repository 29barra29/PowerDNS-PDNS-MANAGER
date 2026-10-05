"""Test-Hilfen fuer WS-F2F3 (Benutzerverwaltung, Zugangs-Widerruf) ohne Datenbank.

``UserDB`` ist eine kleine In-Memory-``AsyncSession`` fuer die Handler aus ``routers/auth.py``: Sie beantwortet die
dort benutzten SELECTs anhand der gebundenen Parameter (``User`` per id/username/email, ``User.id`` fuer die
E-Mail-Pruefung, Admin-Zaehlung, Passkeys, Panel-Tokens, Zaehlungen fuer ``access_summary``) und liefert fuer
UPDATE/DELETE konfigurierbare ``rowcount``-Werte je Tabelle. Alle Statements landen in ``executed``.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Optional

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

from sqlalchemy.sql.dml import Delete, Update  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.models.models import (  # noqa: E402
    DynDnsToken, PanelToken, SystemSetting, User, UserZoneAccess, WebAuthnCredential, Webhook, WebhookDelivery,
)


class Result:
    def __init__(self, rows: Optional[list] = None, rowcount: int = 0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        assert len(self._rows) == 1, self._rows
        return self._rows[0]

    def scalar(self):
        if not self._rows:
            return None
        r = self._rows[0]
        return r[0] if isinstance(r, tuple) else r

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


def bound_params(stmt) -> dict:
    """Gebundene Parameter; Listen aus ``in_()`` (expanding) werden zu ``<name>__<i>`` aufgefaltet."""
    try:
        raw = dict(stmt.compile().params)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for k, v in raw.items():
        if isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                out[f"{k}__{i}"] = item
        else:
            out[k] = v
    return out


def make_user(uid: int, username: str, *, role: str = "user", is_active: bool = True, email=None, **kw) -> User:
    from app.core.auth import hash_password

    pw = kw.pop("password", None)
    u = User(id=uid, username=username, role=role, is_active=is_active, email=email, display_name=username,
             hashed_password=hash_password(pw) if pw else "x", totp_enabled=kw.pop("totp_enabled", False), **kw)
    if getattr(u, "must_change_password", None) is None:
        u.must_change_password = False
    if getattr(u, "auth_source", None) is None:
        u.auth_source = "local"
    return u


class UserDB:
    """In-Memory-Session fuer die Benutzerverwaltungs-Handler."""

    def __init__(
        self,
        users: list[User] = (),
        *,
        tokens: list[PanelToken] = (),
        creds: list[WebAuthnCredential] = (),
        settings: Optional[dict[str, str]] = None,
        rowcounts: Optional[dict[str, int]] = None,
        counts: Optional[dict[str, int]] = None,
        flush_error: Optional[BaseException] = None,
    ):
        self.users = list(users)
        self.tokens = list(tokens)
        self.creds = list(creds)
        self.settings = dict(settings or {})
        self.rowcounts = dict(rowcounts or {})
        self.counts = dict(counts or {})
        self.flush_error = flush_error
        self.executed: list = []
        self.added: list = []
        self.deleted: list = []
        self.flushes = 0

    # --- Antworten --------------------------------------------------------------------------------
    def _user_by_params(self, params: dict) -> list:
        ints = {v for v in params.values() if isinstance(v, int) and not isinstance(v, bool)}
        strs = {v for v in params.values() if isinstance(v, str)}
        for u in self.users:
            if u.id in ints or u.username in strs or (u.email and u.email in strs):
                return [u]
        return []

    async def execute(self, stmt, *args, **kwargs):
        self.executed.append(stmt)
        if isinstance(stmt, (Update, Delete)):
            name = stmt.table.name
            if isinstance(stmt, Delete) and name == "webauthn_credentials":
                n = len(self.creds)
                self.creds = []
                return Result([], rowcount=n)
            return Result([], rowcount=self.rowcounts.get(name, 0))
        desc = getattr(stmt, "column_descriptions", None) or []
        entity = desc[0].get("entity") if desc else None
        names = [d.get("name") for d in desc]
        params = bound_params(stmt)
        sql = str(stmt)
        if "count(" in sql.lower() and "GROUP BY" not in sql:
            for table, n in self.counts.items():
                if f"FROM {table}" in sql:
                    return Result([n])
            if "FROM users" in sql:  # count_active_admins
                exclude = {v for v in params.values() if isinstance(v, int) and not isinstance(v, bool)}
                n = sum(1 for u in self.users if u.role == "admin" and u.is_active and u.id not in exclude)
                return Result([n])
            return Result([0])
        if entity is User and names == ["User"]:
            if not params:  # Liste aller Benutzer (list_users)
                return Result(list(self.users))
            return Result(self._user_by_params(params))
        if entity is User and names == ["id"]:
            strs = {v for v in params.values() if isinstance(v, str)}
            exclude = {v for v in params.values() if isinstance(v, int) and not isinstance(v, bool)}
            return Result([(u.id,) for u in self.users if u.email and u.email in strs and u.id not in exclude])
        if entity is UserZoneAccess:
            return Result([])
        if entity is WebAuthnCredential:
            if names == ["WebAuthnCredential"]:
                return Result(list(self.creds))
            # GROUP BY user_id -> (user_id, n)
            agg: dict[int, int] = {}
            for c in self.creds:
                agg[c.user_id] = agg.get(c.user_id, 0) + 1
            return Result(list(agg.items()))
        if entity is PanelToken:
            if names == ["PanelToken"]:
                return Result([t for t in self.tokens if t.revoked_at is None])
            agg = {}
            for t in self.tokens:
                if t.revoked_at is None and t.is_active:
                    agg[t.user_id] = agg.get(t.user_id, 0) + 1
            return Result(list(agg.items()))
        if entity is SystemSetting:
            keys = {v for v in params.values() if isinstance(v, str)}
            if names == ["SystemSetting"]:
                rows = [SystemSetting(key=k, value=v) for k, v in self.settings.items() if k in keys or not keys]
                return Result(rows)
            if names == ["value"]:
                return Result([v for k, v in self.settings.items() if k in keys][:1])
            return Result([(k, v) for k, v in self.settings.items() if k in keys])
        return Result([])

    async def scalar(self, stmt, *args, **kwargs):
        return (await self.execute(stmt)).scalar()

    # --- Unit of Work -----------------------------------------------------------------------------
    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushes += 1
        if self.flush_error is not None:
            err, self.flush_error = self.flush_error, None
            raise err
        for i, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                obj.id = 500 + i

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def refresh(self, obj):
        return None

    @asynccontextmanager
    async def _nested(self):
        yield self

    def begin_nested(self):
        return self._nested()

    # --- Auswertung -------------------------------------------------------------------------------
    def statements(self, kind: type) -> list:
        return [s for s in self.executed if isinstance(s, kind)]

    def updates_on(self, table: str) -> list:
        return [s for s in self.statements(Update) if s.table.name == table]


class AuditSink:
    """Ersatz fuer ``write_audit``: sammelt Aufrufe (action, resource_name, kwargs)."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, db, action, resource_type, resource_name=None, **kw):
        self.calls.append({"action": action, "resource_type": resource_type, "resource_name": resource_name, **kw})
        return SimpleNamespace(id=len(self.calls))

    def actions(self) -> list[str]:
        return [c["action"] for c in self.calls]

    def one(self, action: str) -> dict:
        found = [c for c in self.calls if c["action"] == action]
        assert len(found) == 1, (action, self.actions())
        return found[0]


def http_request(method: str = "POST", path: str = "/api/v1/x", *, ip: str = "192.0.2.10") -> Request:
    return Request({
        "type": "http", "method": method, "path": path, "root_path": "", "headers": [],
        "query_string": b"", "client": (ip, 40000), "server": ("testserver", 80), "scheme": "http",
    })


__all__ = [
    "AuditSink", "DynDnsToken", "PanelToken", "Result", "UserDB", "Webhook", "WebhookDelivery",
    "bound_params", "http_request", "make_user",
]
