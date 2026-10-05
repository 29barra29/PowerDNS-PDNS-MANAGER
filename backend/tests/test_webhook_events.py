"""Webhook-Ereignisse und Outbox-Kern ohne DB (F6 9.1 Nr. 1–5, Bauplan B.8).

- ``webhook_events``: Payload v2 (ASCII, Kuerzung), Abo-Filter, Abgleich, ``compact_rrset``.
- ``webhook_service``: ``sign``, ``url_display``/``url_host``, neue Ausnahmen.
- ``webhook_outbox``: ``enqueue_event`` (Katalogpruefung, SAVEPOINT, unlesbares Secret/URL [S10], Weckruf nach
  Commit, Beobachter) und ``select_recipients`` (own/zones, Admin-Besitzer, Zonenrechte) gegen eine Fake-Session.
  Die SQL-Seite prueft ``test_webhook_enqueue_db.py`` gegen MariaDB.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from authfakes import FakeResult
from app.core.config import settings
from app.core.request_context import auth_via_ctx
from app.core.secrets import UNREADABLE
from app.models.models import User, UserZoneAccess, Webhook, WebhookDelivery
from app.services import webhook_events as ev
from app.services import webhook_outbox as outbox
from app.services import webhook_service as wsvc
from app.services import webhook_worker

T0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def _payload(data, **kw):
    args = dict(event="record.created", event_id="e-1", delivery_id="d-1", occurred_at=T0, actor_user_id=3,
                actor_username="jürgen", actor_via="session", zone="example.com.", server="ns1",
                audit_log_id=42, data=data)
    args.update(kw)
    return ev.build_payload(**args)


# --------------------------------------------------------------------------- Nr. 1: Envelope v2
def test_build_payload_v2_envelope():
    raw = _payload({"name": "www.example.com.", "note": "Grüße"})
    text = raw.decode("ascii")  # reines ASCII
    body = json.loads(text)
    assert body["v"] == 2 and body["event"] == "record.created"
    assert body["event_id"] == "e-1" and body["delivery_id"] == "d-1"
    assert body["timestamp"] == "2026-10-05T12:00:00+00:00"
    assert body["app"] == settings.APP_NAME and body["app_version"] == settings.APP_VERSION
    assert body["actor_user_id"] == 3
    assert body["actor"] == {"user_id": 3, "username": "jürgen", "via": "session"}
    assert body["zone"] == "example.com." and body["server"] == "ns1" and body["audit_log_id"] == 42
    assert body["data"]["note"] == "Grüße"
    assert "\\u00fc" in text  # Umlaute escaped
    assert set(body) == {"v", "event", "event_id", "delivery_id", "timestamp", "app", "app_version",
                         "actor_user_id", "actor", "zone", "server", "audit_log_id", "data"}


def test_build_payload_serializes_datetimes_and_sets():
    body = json.loads(_payload({"at": T0, "tags": {"a"}}))
    assert body["data"]["at"] == "2026-10-05T12:00:00+00:00" and body["data"]["tags"] == ["a"]


# --------------------------------------------------------------------------- Nr. 2: Kuerzung
def test_payload_truncates_changes_over_limit():
    big = "x" * 300
    changes = [{"name": f"n{i}.example.com.", "type": "TXT", "before": None,
                "after": {"ttl": 60, "records": [{"content": big, "disabled": False}]}} for i in range(5000)]
    raw = _payload({"zone": "example.com.", "changes": changes, "created": 5000})
    assert len(raw) <= ev.MAX_BODY_BYTES
    data = json.loads(raw)["data"]
    assert data["changes"] == [] and data["changes_truncated"] is True and data["changes_count"] == 5000
    assert data["created"] == 5000  # restliche Felder bleiben


def test_payload_truncates_whole_data_when_still_too_big():
    raw = _payload({"blob": "y" * (ev.MAX_BODY_BYTES + 10)})
    assert len(raw) <= ev.MAX_BODY_BYTES
    assert json.loads(raw)["data"] == {"truncated": True}


# --------------------------------------------------------------------------- Nr. 3: Signatur
def test_signature_over_stored_body():
    raw = _payload({"a": "ä"})
    expected = "sha256=" + hmac.new(b"geheim", raw, hashlib.sha256).hexdigest()
    assert wsvc.sign("geheim", raw) == expected
    stored = raw.decode("ascii")  # so landet der Body in webhook_deliveries.body
    assert wsvc.sign("geheim", stored.encode("ascii")) == expected


# --------------------------------------------------------------------------- Nr. 4: Filter
def test_normalize_event_filters():
    assert ev.normalize_event_filters([]) == ["*"]
    assert ev.normalize_event_filters(None) == ["*"]
    assert ev.normalize_event_filters(["*", "record"]) == ["*"]
    assert ev.normalize_event_filters([" Record ", "record", "ZONE.created", ""]) == ["record", "zone.created"]
    assert ev.normalize_event_filters(["record.*", "dnssec"]) == ["record.*", "dnssec"]
    for bad in ("foo", "record.foo", "webhook.test", "foo.*"):
        with pytest.raises(ValueError, match=f"Unbekanntes Ereignis: {bad}"):
            ev.normalize_event_filters([bad])
    with pytest.raises(ValueError, match="Höchstens 30 Ereignisfilter"):
        ev.normalize_event_filters([f"record.created{i}" for i in range(31)])


# --------------------------------------------------------------------------- Nr. 5: Abgleich
def test_matching_new_events():
    assert ev.webhook_wants(["record"], "record.rollback")
    assert ev.webhook_wants(["dnssec.*"], "dnssec.key_deleted")
    assert not ev.webhook_wants(["zone.created"], "zone.deleted")
    assert ev.webhook_wants(["zone.created"], "zone.created")
    assert ev.webhook_wants(["*"], "dyndns.updated") and ev.webhook_wants(None, "zone.imported")
    assert "webhook.test" not in ev.SUBSCRIBABLE_EVENTS and "webhook.test" in ev.EVENT_CATALOG
    assert not ev.webhook_wants(["*"], "webhook.test")  # nie ueber Abos
    assert ev.matches_subscription("zone.*", "zone.imported") and not ev.matches_subscription("zone", "zones.x")


def test_event_catalog_is_plan_b8():
    assert len(ev.EVENT_CATALOG) == len(set(ev.EVENT_CATALOG)) == 21
    for name in ("record.ptr_synced", "dyndns.updated", "dnssec.nsec3_changed", "zone.imported"):
        assert name in ev.EVENT_CATALOG
    assert not any(e.startswith(("record.dyndns", "dnssec.key.")) for e in ev.EVENT_CATALOG)


def test_compact_rrset():
    assert ev.compact_rrset(None) is None
    snap = {"ttl": 300, "records": [{"content": "1.2.3.4", "disabled": False, "x": 1}],
            "comments": [{"content": "c"}]}
    assert ev.compact_rrset(snap) == {"ttl": 300, "records": [{"content": "1.2.3.4", "disabled": False}]}
    raw = {"name": "a.", "type": "A", "ttl": "60", "records": [{"content": "5.6.7.8"}]}
    assert ev.compact_rrset(raw) == {"ttl": 60, "records": [{"content": "5.6.7.8", "disabled": False}]}


# --------------------------------------------------------------------------- webhook_service
def test_url_display_and_host():
    url = "https://u:p@hooks.slack.com/services/T/B/X?x=1"
    assert wsvc.url_display(url) == "https://hooks.slack.com/…"
    assert wsvc.url_host(url) == "hooks.slack.com"
    assert wsvc.url_host("http://example.com:8080/a") == "example.com:8080"
    assert wsvc.url_display("http://example.com:8080/a") == "http://example.com:8080/…"
    assert wsvc.url_display("https://example.com") == "https://example.com"
    assert wsvc.url_host("https://bücher.example/x") == "xn--bcher-kva.example"
    assert wsvc.url_host("http://[2001:db8::1]:9000/") == "[2001:db8::1]:9000"
    assert wsvc.url_display("") == "" and wsvc.url_host(None) == ""
    assert wsvc.MAX_WEBHOOKS_PER_USER == 20


def test_new_exceptions_are_value_errors(monkeypatch):
    assert issubclass(wsvc.WebhookTargetBlocked, ValueError) and issubclass(wsvc.WebhookResolveError, ValueError)
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
    with pytest.raises(wsvc.WebhookTargetBlocked):
        wsvc.validate_webhook_url("http://127.0.0.1/x")

    def boom(*a, **k):
        raise wsvc.socket.gaierror("nope")

    monkeypatch.setattr(wsvc.socket, "getaddrinfo", boom)
    with pytest.raises(wsvc.WebhookResolveError, match="nicht aufgelöst"):
        wsvc.validate_webhook_url("https://does-not-exist.example/x")


def test_legacy_delivery_removed():
    for name in ("deliver_webhooks_background", "_post_one", "_build_payload", "EVENT_VERSION",
                 "_webhook_wants", "_matches_subscription"):
        assert not hasattr(wsvc, name), name


# --------------------------------------------------------------------------- Outbox: Fake-Session
class OutboxSession:
    """Fake-AsyncSession fuer enqueue_event/select_recipients.

    ``hook_rows`` = Ergebnis der Empfaenger-Abfrage (so, wie die DB sie nach WHERE liefern wuerde);
    ``zone_access`` = (user_id, zone_name) fuer die ACL-Abfrage (gefiltert nach Zone und user_id IN).
    """

    def __init__(self, hook_rows=(), zone_access=()):
        self.hook_rows = list(hook_rows)
        self.zone_access = list(zone_access)
        self.added: list = []
        self.flushes = 0
        self.savepoints = 0
        self.savepoint_rollbacks = 0
        self.acl_queries = 0
        self.sync_session = Session()

    async def execute(self, stmt):
        desc = stmt.column_descriptions
        if desc and desc[0].get("entity") is Webhook:
            return FakeResult(list(self.hook_rows))
        if desc and desc[0].get("entity") is UserZoneAccess:
            self.acl_queries += 1
            params = stmt.compile().params
            zones = {v for v in params.values() if isinstance(v, str)}
            ids = {i for v in params.values() if isinstance(v, (list, tuple)) for i in v}
            return FakeResult([uid for uid, z in self.zone_access if z in zones and uid in ids])
        raise AssertionError(f"unerwartetes Statement: {stmt}")

    def add_all(self, rows):
        self.added.extend(rows)

    async def flush(self):
        self.flushes += 1

    @asynccontextmanager
    async def _nested(self):
        self.savepoints += 1
        mark = len(self.added)
        try:
            yield self
        except Exception:
            del self.added[mark:]
            self.savepoint_rollbacks += 1
            raise

    def begin_nested(self):
        return self._nested()


def _hook(hid, owner, *, scope="own", events=("*",), secret="s3cret", url="https://hooks.example/x"):
    return Webhook(id=hid, user_id=owner, name=f"h{hid}", url=url, secret=secret, events=list(events),
                   is_active=True, scope=scope)


def _user(uid, role="user"):
    return User(id=uid, username=f"u{uid}", role=role, is_active=True, hashed_password="x")


async def test_select_recipients_scopes():
    actor = 1
    rows = [
        (_hook(1, actor), "user"),                         # eigener own-Webhook -> ja
        (_hook(2, actor, events=["zone"]), "user"),        # eigener, Filter passt nicht -> nein
        (_hook(3, 2, scope="zones"), "admin"),             # Admin-Besitzer, zones -> ja (alle Zonen)
        (_hook(4, 3, scope="zones"), "user"),              # Nicht-Admin mit Zonenrecht -> ja
        (_hook(5, 4, scope="zones"), "user"),              # Nicht-Admin ohne Zonenrecht -> nein
        (_hook(6, 5, scope="own"), "user"),                # fremder own-Webhook (darf die DB nicht liefern) -> nein
    ]
    db = OutboxSession(rows, zone_access=[(3, "example.com."), (4, "other.example.")])
    hooks = await outbox.select_recipients(db, event="record.created", actor_user_id=actor, zone="example.com.")
    assert [h.id for h in hooks] == [1, 3, 4]
    assert db.acl_queries == 1


async def test_select_recipients_without_zone_only_own_hooks():
    rows = [(_hook(1, 1), "user"), (_hook(3, 2, scope="zones"), "admin")]
    db = OutboxSession(rows)
    hooks = await outbox.select_recipients(db, event="record.created", actor_user_id=1, zone=None)
    assert [h.id for h in hooks] == [1]
    assert db.acl_queries == 0


async def test_enqueue_unknown_event_logs_error(caplog):
    db = OutboxSession([(_hook(1, 1), "user")])
    with caplog.at_level(logging.ERROR):
        n = await outbox.enqueue_event(db, "record.unknown", actor=_user(1), data={})
    assert n == 0 and db.added == [] and db.savepoints == 0
    assert "Unbekanntes Webhook-Ereignis record.unknown" in caplog.text


async def test_enqueue_creates_signed_rows_per_recipient():
    db = OutboxSession([(_hook(1, 1, secret="aaa"), "user"), (_hook(3, 2, scope="zones", secret="bbb"), "admin")])
    token = auth_via_ctx.set("panel_token")
    try:
        n = await outbox.enqueue_event(db, "record.created", actor=_user(1), zone="Example.COM", server="ns1",
                                       data={"name": "www"}, audit_log_id=7)
    finally:
        auth_via_ctx.reset(token)
    assert n == 2 and db.savepoints == 1
    rows = db.added
    assert all(isinstance(r, WebhookDelivery) for r in rows)
    assert len({r.event_id for r in rows}) == 1 and len({r.delivery_id for r in rows}) == 2
    for r, secret in zip(rows, ("aaa", "bbb")):
        assert r.status == "queued" and r.attempts == 0 and r.max_attempts == outbox.MAX_ATTEMPTS
        assert r.zone_name == "example.com." and r.audit_log_id == 7 and r.event == "record.created"
        assert r.signature == wsvc.sign(secret, r.body.encode("ascii"))
        body = json.loads(r.body)
        assert body["delivery_id"] == r.delivery_id and body["event_id"] == r.event_id
        assert body["actor"] == {"user_id": 1, "username": "u1", "via": "panel_token"}
        assert body["zone"] == "example.com." and body["server"] == "ns1" and body["data"] == {"name": "www"}
        assert r.next_attempt_at == r.created_at and r.created_at.tzinfo is None  # naive UTC


async def test_enqueue_marks_unreadable_secret_and_url_dead():
    db = OutboxSession([
        (_hook(1, 1, secret=UNREADABLE), "user"),
        (_hook(2, 1, url=UNREADABLE), "user"),
        (_hook(3, 1, secret=""), "user"),
    ])
    n = await outbox.enqueue_event(db, "zone.created", actor=_user(1), zone="example.com.", data={})
    assert n == 3
    a, b, c = db.added
    assert (a.status, a.last_error_code, a.signature) == ("dead", "secret_unreadable", "")
    assert (b.status, b.last_error_code) == ("dead", "url_unreadable") and b.signature.startswith("sha256=")
    assert (c.status, c.last_error_code) == ("dead", "secret_unreadable")
    assert "Secret erneuern" in a.last_error and "URL" in b.last_error


async def test_enqueue_failure_rolls_back_savepoint_only(monkeypatch, caplog):
    db = OutboxSession([(_hook(1, 1), "user"), (_hook(2, 1), "user")])
    calls = {"n": 0}
    real = ev.build_payload

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("kaputt")
        return real(**kw)

    monkeypatch.setattr(outbox, "build_payload", flaky)
    with caplog.at_level(logging.ERROR):
        n = await outbox.enqueue_event(db, "record.created", actor=_user(1), data={})
    assert n == 0 and db.added == []  # keine halbe Zustellliste
    assert "konnte nicht eingereiht werden" in caplog.text


async def test_enqueue_without_recipients_registers_nothing(monkeypatch):
    db = OutboxSession([])
    seen = []
    monkeypatch.setattr(webhook_worker, "notify_worker", lambda: seen.append(1))
    assert await outbox.enqueue_event(db, "record.created", actor=None, data={}) == 0
    db.sync_session.commit()
    assert seen == []


async def test_wakeup_after_commit_once_and_observer(monkeypatch):
    db = OutboxSession([(_hook(1, 1), "user")])
    woke, observed = [], []
    monkeypatch.setattr(webhook_worker, "notify_worker", lambda: woke.append(1))
    monkeypatch.setattr(outbox, "_enqueue_observers", [])
    outbox.register_enqueue_observer(lambda e, n: observed.append((e, n)))
    outbox.register_enqueue_observer(lambda e, n: 1 / 0)  # fehlerhafter Beobachter stoert nicht
    await outbox.enqueue_event(db, "record.created", actor=_user(1), data={})
    await outbox.enqueue_event(db, "record.updated", actor=_user(1), data={})
    assert woke == []  # erst nach dem Commit
    db.sync_session.commit()
    assert woke == [1]  # einmal je Commit, nicht je Ereignis
    db.sync_session.commit()
    assert woke == [1]
    await outbox.enqueue_event(db, "record.deleted", actor=_user(1), data={})
    db.sync_session.commit()
    assert woke == [1, 1]  # naechste Transaktion -> neuer Weckruf
    assert observed == [("record.created", 1), ("record.updated", 1), ("record.deleted", 1)]


def test_worker_module_api_is_inert():
    state = webhook_worker.worker_state()
    assert state["running"] is False and set(state) == {"enabled", "running", "last_loop_at", "last_error_at"}
    assert webhook_worker.notify_worker() is None
