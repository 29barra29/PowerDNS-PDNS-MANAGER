"""Regressionstests WS-W1-NACHARBEIT (niedrige Review-Funde aus Welle 1).

- L7: ``python -m app.cli.secrets`` gibt bei DB-Fehlern keine Werte aus. ``str()`` einer SQLAlchemy-Ausnahme enthaelt
  das SQL mit den gebundenen Parametern – bei ``prepare-downgrade`` also entschluesselte Geheimnisse.
- L8: Der Webhook-Sender loggt bei unerwarteten Fehlern nie einen Traceback und nie die volle Ziel-URL.

L10 (Rollback: Noop ist kein Konflikt) steht in ``test_rollback.py`` und ``test_record_history.py``.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError, StatementError

from app.cli import secrets as cli
from app.core.secrets import SecretBox, parse_key
from app.services import webhook_sender as sender

KEY = "dGVzdC1rZXktZm9yLXBkbnMtbWFuYWdlci0zMmJ5dGU="
BOX = SecretBox(parse_key(KEY, source_name="t"))
SECRET = "pdns-klartext-GEHEIM-4711"


# ---------------------------------------------------------------------------------------------
# L7 – CLI
# ---------------------------------------------------------------------------------------------


class _Engine:
    disposed = 0

    async def dispose(self):
        _Engine.disposed += 1


def _db_error() -> IntegrityError:
    # So sieht ein Fehler beim Zurueckschreiben aus: SQL + Parameter (Klartext) + Treibertext mit Wert
    return IntegrityError("UPDATE server_configs SET api_key=%s WHERE server_configs.id = %s", (SECRET, 1),
                          Exception(1062, f"Duplicate entry '{SECRET}' for key 'x'"))


@pytest.fixture
def cli_env(monkeypatch):
    keys = cli.CliKeys(box=BOX, source="env", file_path=Path("data/.secret_key"), file_explicit=False,
                       file_status="missing")
    monkeypatch.setattr(cli, "resolve_keys", lambda cfg=None: keys)
    monkeypatch.setattr(cli, "_get_engine", lambda: _Engine())
    plan = cli.DowngradePlan(decrypt=cli.DecryptPlan([], []), tokens=[], markers=[], external_accounts=0)
    state = {"write_error": _db_error(), "audits": []}

    async def fake_run_sync(fn, *args, write=False, **kwargs):
        if not write:
            return plan
        if state["write_error"] is not None:
            raise state["write_error"]
        return {"decrypted": {}, "skipped_unreadable": 0, "tokens_deactivated": 0, "token_reasons": {},
                "markers_removed": [], "external_accounts": 0}

    async def fake_audit(action, details):
        state["audits"].append(action)
        return 1

    monkeypatch.setattr(cli, "_run_sync", fake_run_sync)
    monkeypatch.setattr(cli, "_write_audit", fake_audit)
    return state


def test_db_error_text_would_contain_the_secret():
    # Gegenprobe: ohne Filter stuende das Geheimnis in der Ausgabe
    assert SECRET in str(_db_error())


async def test_prepare_downgrade_db_error_prints_no_values(cli_env, capsys):
    rc = await cli.main(["prepare-downgrade", "--yes"])
    out, err = capsys.readouterr()
    assert rc == cli.EXIT_ERROR
    assert SECRET not in out and SECRET not in err
    assert "UPDATE server_configs" not in err
    assert "Vorbereitung fehlgeschlagen (IntegrityError / Exception 1062)" in err
    assert cli_env["audits"] == []


async def test_unexpected_error_in_command_prints_no_traceback(cli_env, monkeypatch, capsys):
    cli_env["write_error"] = None

    async def failing_audit(action, details):
        exc = StatementError("Audit fehlgeschlagen", "INSERT INTO audit_logs (details) VALUES (%s)", (SECRET,),
                             Exception("kaputt"))
        assert SECRET in str(exc)  # Gegenprobe
        raise exc

    monkeypatch.setattr(cli, "_write_audit", failing_audit)
    rc = await cli.main(["prepare-downgrade", "--yes"])
    out, err = capsys.readouterr()
    assert rc == cli.EXIT_ERROR
    assert SECRET not in out and SECRET not in err and "Traceback" not in err
    assert "unerwarteter Fehler (StatementError / Exception)" in err


def test_safe_exc_only_shows_own_abort_texts():
    assert cli._safe_exc(cli._CliAbort("nicht lesbare Werte")) == "nicht lesbare Werte"
    assert cli._safe_exc(ValueError(SECRET)) == "ValueError"
    assert cli._safe_exc(_db_error()) == "IntegrityError / Exception 1062"


def test_sqlalchemy_does_not_log_statements_at_cli_log_level():
    # run() setzt das Root-Logging auf INFO; die SQLAlchemy-Logger (SQL + Parameter) bleiben auf WARNING
    import app.core.database  # noqa: F401 - legt den Engine an

    assert not logging.getLogger("sqlalchemy.engine.Engine").isEnabledFor(logging.INFO)


# ---------------------------------------------------------------------------------------------
# L8 – Webhook-Sender
# ---------------------------------------------------------------------------------------------

HOOK_URL = "https://hooks.example.com/services/T0/B0/GEHEIMPFAD?token=abc"


async def test_unexpected_send_error_logs_no_url_and_no_traceback(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=sender.__name__)

    async def boom(url, body, headers, t0):
        raise RuntimeError(f"kaputt bei {url}")

    monkeypatch.setattr(sender, "_send", boom)
    res = await sender.send_delivery(url=HOOK_URL, body=b"{}", headers={})
    assert res.error_code == "internal_error" and res.error == "Interner Fehler beim Versand (RuntimeError)"
    assert "GEHEIMPFAD" not in caplog.text and "token=abc" not in caplog.text and "T0/B0" not in caplog.text
    assert all(rec.exc_info is None and rec.exc_text is None for rec in caplog.records)
    (rec,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    msg = rec.getMessage()
    assert "hooks.example.com" in msg and "RuntimeError" in msg and "test_w1_nacharbeit.py:" in msg and "(boom)" in msg


def test_origin_of_without_traceback():
    assert sender._origin_of(RuntimeError("x")) == "?"
