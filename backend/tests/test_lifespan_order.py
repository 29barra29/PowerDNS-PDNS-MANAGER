"""Startreihenfolge im lifespan (Bauplan A.6) und Hintergrund-Rahmen ``services/background.py`` (B.9).

Alle Schritte werden durch Aufzeichner ersetzt; geprueft wird nur die Reihenfolge und der Abbruch:
init_db (inkl. Spaltenpruefung) -> init_secrets -> create_initial_admin (+ commit) -> Basis-URL-Hinweis ->
PowerDNS-Server laden -> Hinweise (Metrik-Token, DynDNS) -> background.start_all -> yield -> stop_all.
"""
import asyncio
import logging

import pytest

from app import main
from app.core import secrets as secret_store
from app.core.config import settings
from app.core.database import SchemaStartupError
from app.services import audit as audit_service
from app.services import background, metrics_runtime, webhook_worker


class _Session:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.calls.append("commit")


@pytest.fixture
def steps(monkeypatch):
    calls: list[str] = []

    def rec(name, result=None):
        async def _a(*a, **k):
            calls.append(name)
            return result

        return _a

    monkeypatch.setattr(main, "init_db", rec("init_db"))
    monkeypatch.setattr(secret_store, "init_secrets", rec("init_secrets"))
    monkeypatch.setattr(main, "async_session", lambda: _Session(calls))
    monkeypatch.setattr(main, "create_initial_admin", rec("create_initial_admin"))
    monkeypatch.setattr(main, "_warn_missing_base_url", rec("base_url_hint"))
    monkeypatch.setattr(main, "_load_pdns_servers", rec("load_servers"))
    monkeypatch.setattr(metrics_runtime, "log_env_token_state", lambda: calls.append("metrics_token_hint"))
    monkeypatch.setattr(background, "start_all", lambda: calls.append("background_start"))
    monkeypatch.setattr(background, "stop_all", rec("background_stop"))
    monkeypatch.setattr(settings, "BACKGROUND_WORKERS_ENABLED", True)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    return calls


async def _run_lifespan(body=None):
    async with main.lifespan(main.app):
        if body:
            body()


async def test_lifespan_order(steps, caplog):
    with caplog.at_level(logging.WARNING):
        await _run_lifespan(lambda: steps.append("yield"))
    assert steps == [
        "init_db", "init_secrets", "create_initial_admin", "commit", "base_url_hint", "load_servers",
        "metrics_token_hint", "background_start", "yield", "background_stop",
    ]
    assert main.DYNDNS_PROXY_HINT in caplog.text


async def test_no_proxy_hint_with_trusted_proxy_headers(steps, monkeypatch, caplog):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    with caplog.at_level(logging.WARNING):
        await _run_lifespan()
    assert main.DYNDNS_PROXY_HINT not in caplog.text


async def test_background_not_started_when_disabled(steps, monkeypatch, caplog):
    monkeypatch.setattr(settings, "BACKGROUND_WORKERS_ENABLED", False)
    with caplog.at_level(logging.WARNING):
        await _run_lifespan()
    assert "background_start" not in steps and steps[-1] == "background_stop"
    assert "BACKGROUND_WORKERS_ENABLED=false" in caplog.text


async def test_schema_error_aborts_before_secrets(steps, monkeypatch, caplog):
    async def broken():
        steps.append("init_db")
        raise SchemaStartupError("SCHEMA_INCOMPLETE", "Datenbankschema unvollstaendig", ["audit_logs.zone_name fehlt"])

    monkeypatch.setattr(main, "init_db", broken)
    with caplog.at_level(logging.CRITICAL), pytest.raises(SchemaStartupError):
        await _run_lifespan()
    assert steps == ["init_db"]
    crit = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert any("Start abgebrochen" in m and "SCHEMA_INCOMPLETE" in m for m in crit)
    assert "audit_logs.zone_name fehlt" in crit


async def test_secrets_error_aborts_before_admin(steps, monkeypatch, caplog):
    async def broken(engine):
        steps.append("init_secrets")
        raise secret_store.SecretsStartupError("KEY_MISSING", "Schluessel fehlt", ["Zeile A"])

    monkeypatch.setattr(secret_store, "init_secrets", broken)
    with caplog.at_level(logging.CRITICAL), pytest.raises(secret_store.SecretsStartupError):
        await _run_lifespan()
    assert steps == ["init_db", "init_secrets"]
    crit = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert "Zeile A" in crit and any("KEY_MISSING" in m for m in crit)


async def test_migration_errors_logged_but_start_continues(steps, monkeypatch, caplog):
    monkeypatch.setattr(main, "MIGRATION_ERRORS", [("ALTER TABLE x", "Fehler")])
    with caplog.at_level(logging.ERROR):
        await _run_lifespan()
    assert "background_start" in steps
    assert any(r.levelno == logging.ERROR and "Migrationsschritte" in r.getMessage() for r in caplog.records)


async def test_shutdown_runs_even_if_app_body_fails(steps):
    with pytest.raises(RuntimeError):
        async with main.lifespan(main.app):
            raise RuntimeError("Absturz waehrend des Betriebs")
    assert steps[-1] == "background_stop"


async def test_load_servers_logs_skipped(monkeypatch, caplog):
    """Schritt 5: nicht geladene Server (unlesbarer Key) -> ERROR mit Namen [D4]."""
    from types import SimpleNamespace

    cfgs = [SimpleNamespace(name="ns1", is_active=True), SimpleNamespace(name="ns2", is_active=True)]

    class _Res:
        def scalars(self):
            return SimpleNamespace(all=lambda: cfgs)

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            return _Res()

        async def scalar(self, stmt):
            return 2

    monkeypatch.setattr(main, "async_session", lambda: _S())
    monkeypatch.setattr(main.pdns_manager, "load_from_db_configs", lambda c: ["ns2"])
    monkeypatch.setattr(main.pdns_manager, "list_servers", lambda: ["ns1"])
    with caplog.at_level(logging.ERROR):
        await main._load_pdns_servers()
    assert any(r.levelno == logging.ERROR and "ns2" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- services/background.py
@pytest.fixture
def bg(monkeypatch):
    background.reset_for_tests()
    monkeypatch.setattr(settings, "BACKGROUND_WORKERS_ENABLED", True)
    yield background
    background.reset_for_tests()


async def test_background_start_stop_and_state(bg, monkeypatch):
    calls = []

    async def reset():
        calls.append("reset")
        return 2

    async def stop():
        calls.append("stop_worker")

    monkeypatch.setattr(webhook_worker, "reset_in_progress_on_startup", reset)
    monkeypatch.setattr(webhook_worker, "start_worker", lambda: calls.append("start_worker"))
    monkeypatch.setattr(webhook_worker, "stop_worker", stop)
    bg.start_all()
    bg.start_all()  # idempotent
    await asyncio.sleep(0.05)
    assert calls == ["reset", "start_worker"]
    st = bg.state()
    assert st["enabled"] is True and set(st["tasks"]) == {"webhook_worker", "audit_purge"}
    assert st["tasks"]["audit_purge"]["running"] is True  # wartet auf den ersten Lauf
    assert st["tasks"]["webhook_worker"]["last_run_at"] is not None
    await bg.stop_all(timeout=1.0)
    assert "stop_worker" in calls
    assert all(not t["running"] for t in bg.state()["tasks"].values())


async def test_background_disabled_starts_nothing(bg, monkeypatch, caplog):
    monkeypatch.setattr(settings, "BACKGROUND_WORKERS_ENABLED", False)
    with caplog.at_level(logging.WARNING):
        bg.start_all()
    assert bg.state() == {"enabled": False, "tasks": {}}
    assert "BACKGROUND_WORKERS_ENABLED=false" in caplog.text
    await bg.stop_all(timeout=0.1)  # ohne Tasks unkritisch


async def test_audit_purge_loop_runs_and_survives_errors(bg, monkeypatch):
    runs = []

    async def purge(*, now=None):
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("DB weg")
        return 3

    monkeypatch.setattr(audit_service, "purge_expired_audit_logs", purge)
    task = asyncio.get_running_loop().create_task(bg._audit_purge_loop(first_delay=0, interval=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(runs) >= 2  # nach dem Fehler weiter
    st = bg._status["audit_purge"]
    assert st["last_error_at"] is not None and st["last_run_at"] is not None


async def test_stop_all_times_out_hanging_tasks(bg, monkeypatch):
    async def hang():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(5)  # ignoriert den Abbruch zunaechst

    async def stop():
        return None

    monkeypatch.setattr(webhook_worker, "stop_worker", stop)
    bg._spawn("haengt", hang)
    task = bg._tasks["haengt"]
    await asyncio.sleep(0)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await bg.stop_all(timeout=0.2)
    assert loop.time() - t0 < 2.0
    assert not task.done()  # haengt noch im Abbruch-Handler, stop_all ist trotzdem zurueck
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
