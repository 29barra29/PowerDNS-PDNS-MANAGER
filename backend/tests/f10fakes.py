"""Test-Hilfen fuer WS-F10-APP-BE (SSO-Router, Login-Flows, Step-up) ohne MariaDB.

- ``SqliteSession``: ``AsyncSession``-Ersatz ueber einer synchronen SQLite-Session (aiosqlite fehlt im Testimage).
  Echte SQL-Abfragen auf den App-Modellen (User, SystemSetting, AuditLog, Passkeys, Tokens, Webhooks, Zonenrechte),
  SAVEPOINTs fuer ``write_audit``/JIT, thread-sicher fuer den ``TestClient`` (``StaticPool``).
- ``F10Env``: Datenbank + Test-App aus einzelnen Routern (``/api/v1``), ``get_db`` wie in der App (Commit nach dem
  Handler, Rollback bei Fehler), globale Fehlerbehandlung fuer ``SecretReentryRequired``; Helfer zum Anlegen von
  Benutzern und SSO-Einstellungen, Session-Header, gesammelte Fehler-Audits (``write_audit_detached``).
"""
from __future__ import annotations

import os
from typing import Any, Optional

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.auth import create_access_token, hash_password  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.secret_mask import SecretReentryRequired  # noqa: E402
from app.models.models import (  # noqa: E402
    AuditLog, DynDnsToken, PanelToken, SystemSetting, User, UserZoneAccess, WebAuthnCredential, Webhook,
    WebhookDelivery,
)

TABLES = (User, SystemSetting, AuditLog, WebAuthnCredential, UserZoneAccess, PanelToken, Webhook, WebhookDelivery,
          DynDnsToken)

# Bcrypt kostet ~0,2 s je Hash: Testpasswoerter einmal hashen
_HASH_CACHE: dict[str, str] = {}


def pw_hash(password: str) -> str:
    if password not in _HASH_CACHE:
        _HASH_CACHE[password] = hash_password(password)
    return _HASH_CACHE[password]


class _Nested:
    def __init__(self, session: Session):
        self._s, self._tx = session, None

    async def __aenter__(self):
        self._tx = self._s.begin_nested()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        (self._tx.commit if exc_type is None else self._tx.rollback)()
        return False


class SqliteSession:
    """Minimale AsyncSession ueber SQLite (nur die von Routern/Diensten benutzten Methoden)."""

    def __init__(self, session: Session):
        self.sync_session = session
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, *a, **k):
        return self.sync_session.execute(*a, **k)

    async def scalar(self, *a, **k):
        return self.sync_session.scalar(*a, **k)

    async def get(self, *a, **k):
        return self.sync_session.get(*a, **k)

    def add(self, obj):
        self.sync_session.add(obj)

    def add_all(self, objs):
        self.sync_session.add_all(objs)

    async def delete(self, obj):
        self.sync_session.delete(obj)

    async def flush(self, *a, **k):
        self.sync_session.flush(*a, **k)

    async def commit(self):
        self.commits += 1
        self.sync_session.commit()

    async def rollback(self):
        self.rollbacks += 1
        self.sync_session.rollback()

    async def refresh(self, obj, *a, **k):
        self.sync_session.refresh(obj, *a, **k)

    async def close(self):
        return None

    def begin_nested(self):
        return _Nested(self.sync_session)


def make_engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _connect(dbapi_conn, _rec):
        dbapi_conn.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    for model in TABLES:
        model.__table__.create(eng)
    return eng


class F10Env:
    """SQLite-DB + Test-App mit den angegebenen Routern."""

    def __init__(self, *routers):
        self.engine = make_engine()
        self.session = Session(self.engine, expire_on_commit=False)
        self.db = SqliteSession(self.session)
        self.app = FastAPI()
        for r in routers:
            self.app.include_router(getattr(r, "router", r), prefix="/api/v1")

        async def _db():
            try:
                yield self.db
                await self.db.commit()
            except Exception:
                await self.db.rollback()
                raise

        self.app.dependency_overrides[get_db] = _db

        from app.main import secret_reentry_handler

        self.app.add_exception_handler(SecretReentryRequired, secret_reentry_handler)

    def close(self):
        self.session.close()
        self.engine.dispose()

    def client(self, *, ip: str = "198.51.100.10", base_url: str = "https://testserver") -> TestClient:
        return TestClient(self.app, base_url=base_url, raise_server_exceptions=False, follow_redirects=False,
                          client=(ip, 50000))

    # --- Daten -------------------------------------------------------------------------------------
    def add_user(self, username: str, *, password: Optional[str] = "Passwort-123", role: str = "user",
                 auth_source: str = "local", issuer: Optional[str] = None, ext_id: Optional[str] = None,
                 is_active: bool = True, email: Optional[str] = None, **kw: Any) -> User:
        u = User(username=username, email=email, display_name=username, role=role, is_active=is_active,
                 hashed_password=pw_hash(password) if password else pw_hash("x-unbekannt-x"),
                 auth_source=auth_source, external_issuer=issuer, external_id=ext_id, **kw)
        self.session.add(u)
        self.session.commit()
        return u

    def set_settings(self, **values: Any) -> None:
        for key, val in values.items():
            if isinstance(val, bool):
                val = "true" if val else "false"
            row = self.session.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
            if row is None:
                self.session.add(SystemSetting(key=key, value=val))
            else:
                row.value = val
        self.session.commit()

    def audits(self, action: Optional[str] = None) -> list[AuditLog]:
        q = select(AuditLog).order_by(AuditLog.id)
        if action:
            q = q.where(AuditLog.action == action)
        return list(self.session.execute(q).scalars().all())

    def reload(self, obj):
        self.session.refresh(obj)
        return obj

    def user_by_name(self, username: str) -> Optional[User]:
        return self.session.execute(select(User).where(User.username == username)).scalar_one_or_none()


def session_headers(user: User, **claims: Any) -> dict[str, str]:
    """Bearer-JWT einer Browser-Session (``typ=access``, an den Passwort-Hash gebunden)."""
    token = create_access_token(data={"sub": str(user.id), "role": user.role, **claims}, user=user)
    return {"Authorization": f"Bearer {token}"}
