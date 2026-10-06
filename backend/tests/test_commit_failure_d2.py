"""Commit-Fehler schreibender Routen fuehren zu 5xx statt 200 (Bauplan F.3 / B.7 [D2]).

``test_db_dependency_scope.py`` prueft statisch, dass jede schreibende Route ``DbWrite`` (``scope="function"``)
nutzt. Dieser Test prueft das Verhalten zur Laufzeit: ``session.commit`` wird per Monkeypatch zum Fehlschlag
gezwungen (wie ein Verbindungsabbruch beim COMMIT). Mit ``DbWrite`` laeuft der Commit vor dem Senden der Antwort,
der Client bekommt also 500 – nie ein 200, dessen Audit-Eintrag/Outbox-Zeile in Wahrheit verloren ist.

Ohne Datenbank: ``get_db`` holt seine Session aus ``app.core.database.async_session``; der Test ersetzt diese
Fabrik durch eine ``FakeSession`` (``tests/authfakes.py``), deren ``commit`` eine ``OperationalError`` wirft.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from authfakes import FakeSession, make_user
from app.core import database
from app.core.database import DbRead, DbWrite


class CommitFailsSession(FakeSession):
    """FakeSession, deren COMMIT scheitert (z. B. Verbindung waehrend des Commits verloren)."""

    async def commit(self):
        self.commits += 1
        raise OperationalError("COMMIT", {}, Exception("Lost connection to server during query"))


def _patch_session_factory(monkeypatch, session: FakeSession) -> None:
    @asynccontextmanager
    async def _factory():
        yield session

    monkeypatch.setattr(database, "async_session", _factory)


def test_real_write_route_returns_500_when_commit_fails(monkeypatch):
    """``PUT /api/v1/auth/me`` (DbWrite) mit gueltiger Browser-Sitzung: COMMIT scheitert -> 500, kein 200."""
    from app.core.auth import create_access_token
    from app.core.config import settings
    from app.main import app

    user = make_user(role="user", uid=7, username="alice")
    session = CommitFailsSession(user_row=user)
    _patch_session_factory(monkeypatch, session)

    token = create_access_token({"sub": str(user.id)}, user=user)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(settings.AUTH_COOKIE_NAME, token)
    resp = client.put("/api/v1/auth/me", json={"display_name": "Alice Neu"})

    assert resp.status_code >= 500, (resp.status_code, resp.text)
    assert resp.status_code != 200
    assert session.commits == 1, "der Commit muss vor dem Senden der Antwort versucht worden sein"
    assert session.rollbacks >= 1, "nach dem gescheiterten Commit wird zurueckgerollt"


def test_real_write_route_returns_200_when_commit_succeeds(monkeypatch):
    """Gegenprobe zum Test oben: gleicher Aufbau mit funktionierendem COMMIT -> 200."""
    from app.core.auth import create_access_token
    from app.core.config import settings
    from app.main import app

    user = make_user(role="user", uid=7, username="alice")
    session = FakeSession(user_row=user)
    _patch_session_factory(monkeypatch, session)

    token = create_access_token({"sub": str(user.id)}, user=user)
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set(settings.AUTH_COOKIE_NAME, token)
    resp = client.put("/api/v1/auth/me", json={"display_name": "Alice Neu"})

    assert resp.status_code == 200, (resp.status_code, resp.text)
    assert session.commits == 1


@pytest.mark.parametrize("use_dbwrite", [True, False])
def test_dbwrite_scope_decides_between_500_and_lost_commit(monkeypatch, use_dbwrite):
    """Selbsttest: Mit ``DbWrite`` meldet der Client 500. Mit ``DbRead`` (Default-Scope ``request``) laeuft der
    Commit erst nach dem Senden – der Client saehe 200, obwohl nichts gespeichert wurde. Genau deshalb ist
    ``DbWrite`` fuer schreibende Handler Pflicht."""
    session = CommitFailsSession()
    _patch_session_factory(monkeypatch, session)

    mini = FastAPI()
    if use_dbwrite:
        @mini.post("/write")
        async def write(db: DbWrite):
            db.add(object())
            return {"ok": True}
    else:
        @mini.post("/write")
        async def write(db: DbRead):
            db.add(object())
            return {"ok": True}

    client = TestClient(mini, raise_server_exceptions=False)
    resp = client.post("/write")

    assert session.commits == 1
    if use_dbwrite:
        assert resp.status_code == 500
    else:
        assert resp.status_code == 200
