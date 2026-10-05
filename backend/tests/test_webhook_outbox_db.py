"""Webhook-Outbox, Worker und Verwaltung gegen MariaDB (F6 9.2; ``requires_db``, Fixture ``fresh_db``).

Abgedeckt (Nummern aus F6 9.2; Nr. 12 ``test_record_create_enqueues_v2`` gehoert zu WS-F7-BE [D8]):
1 Commit/Rollback mit dem Request, 2 Fehler beim Einreihen bricht den Request nicht, 3 Empfaengerauswahl,
4 Claim mit ``FOR UPDATE SKIP LOCKED``, 5 Backoff bis ``dead``, 6 Erfolg, 7 inaktiver/geloeschter Webhook,
8 Startup-Reset, 9 Stale-Reset, 10 Housekeeping, 11 HTTP-Ebene (Anlegen/Liste/Aendern/Rotation/Deaktivieren/
Loeschen mit Audit, URL-Maske per Panel-Token, Protokoll mit Paginierung und Filtern, Retry-Regeln,
Test-Zustellung mit Cooldown, Limit 20), 13 ``zone.deleted`` vor dem Entfernen der Zonenrechte,
14 Benutzer loeschen entfernt Zustellungen. Zusaetzlich: unlesbare Ziel-URL -> ``dead/url_unreadable`` [S10],
``cancelled`` (Widerruf aller Zugaenge) wird nie gesendet [S9], ``queue_depth``/``stats_for_user``.

Jeder Test leert ``webhook_deliveries`` und ``webhooks`` (Wegwerf-Datenbank des Moduls), weil der Worker
global alle faelligen Zeilen holt. HTTP geht an ``httpx.MockTransport`` (keine echten Ziele, keine DNS-Aufloesung).
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text, update

from dbutil import requires_db

pytestmark = requires_db

HOOK_URL = "https://hooks.example.com/services/T0/B0/SECRETPATH"


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- Fixtures / Helfer
@pytest.fixture(autouse=True)
def _db(fresh_db, monkeypatch):
    from app.core import secrets as secret_store
    from app.core.config import settings
    from app.routers import webhooks as webhooks_router
    from app.services import webhook_worker

    secret_store.configure_for_tests(os.environ["SECRET_ENCRYPTION_KEY"])
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", True)  # keine DNS-Aufloesung im Test
    webhooks_router._last_test.clear()
    webhook_worker.reset_for_tests()
    _run(_clear())
    yield fresh_db
    webhooks_router._last_test.clear()


async def _clear():
    from app.core.database import async_session
    from app.models.models import Webhook, WebhookDelivery

    async with async_session() as s:
        await s.execute(delete(WebhookDelivery))
        await s.execute(delete(Webhook))
        await s.commit()


@pytest.fixture
def transport(monkeypatch):
    from app.services import webhook_sender

    state = {"handler": lambda req: httpx.Response(200, text="ok"), "requests": []}

    def handle(req):
        state["requests"].append(req)
        return state["handler"](req)

    mock = httpx.MockTransport(handle)
    monkeypatch.setattr(webhook_sender, "_client_factory",
                        lambda: httpx.AsyncClient(transport=mock, follow_redirects=False, trust_env=False))
    return state


def _name(key: str) -> str:
    return f"f6_{key}_{uuid.uuid4().hex[:8]}"


async def _user(key: str, role: str = "user", active: bool = True) -> int:
    from app.core.database import async_session
    from app.models.models import User

    async with async_session() as s:
        u = User(username=_name(key), hashed_password="x-hash", role=role, is_active=active, display_name=key)
        s.add(u)
        await s.commit()
        return u.id


async def _hook(user_id: int, *, key: str = "h", scope: str = "own", events=("*",), active: bool = True,
                url: str = HOOK_URL, secret: str = "geheim-1", failures: int = 0) -> int:
    from app.core.database import async_session
    from app.models.models import Webhook

    async with async_session() as s:
        w = Webhook(user_id=user_id, name=_name(key), url=url, secret=secret, events=list(events),
                    is_active=active, scope=scope, consecutive_failures=failures)
        s.add(w)
        await s.commit()
        return w.id


async def _delivery(webhook_id: int, user_id: int, *, secret: str = "geheim-1", status: str = "queued",
                    attempts: int = 0, max_attempts: int = 6, age: timedelta = timedelta(0),
                    due_in: timedelta = timedelta(seconds=-1), event: str = "record.created",
                    zone: str | None = "example.com.", last_attempt_ago: timedelta | None = None) -> int:
    from app.core.database import async_session
    from app.core.timeutil import utcnow
    from app.models.models import WebhookDelivery
    from app.services.webhook_service import sign

    now = utcnow()
    did = str(uuid.uuid4())
    body = json.dumps({"v": 2, "event": event, "delivery_id": did, "data": {"n": did[:8]}},
                      separators=(",", ":"), ensure_ascii=True)
    async with async_session() as s:
        d = WebhookDelivery(
            delivery_id=did, event_id=str(uuid.uuid4()), webhook_id=webhook_id, user_id=user_id, event=event,
            zone_name=zone, body=body, signature=sign(secret, body.encode("ascii")), status=status,
            attempts=attempts, max_attempts=max_attempts, next_attempt_at=now + due_in, created_at=now - age,
            last_attempt_at=(now - last_attempt_ago) if last_attempt_ago is not None else None,
        )
        s.add(d)
        await s.commit()
        return d.id


async def _get(model, pk):
    from app.core.database import async_session

    async with async_session() as s:
        return await s.get(model, pk)


async def _rows(**where):
    from app.core.database import async_session
    from app.models.models import WebhookDelivery

    async with async_session() as s:
        stmt = select(WebhookDelivery).order_by(WebhookDelivery.id)
        for k, v in where.items():
            stmt = stmt.where(getattr(WebhookDelivery, k) == v)
        return list((await s.execute(stmt)).scalars().all())


async def _make_due(pk: int) -> None:
    from app.core.database import async_session
    from app.core.timeutil import utcnow
    from app.models.models import WebhookDelivery

    async with async_session() as s:
        await s.execute(update(WebhookDelivery).where(WebhookDelivery.id == pk)
                        .values(next_attempt_at=utcnow() - timedelta(seconds=1)))
        await s.commit()


async def _audits(user_id: int, resource_type: str = "webhook"):
    from app.core.database import async_session
    from app.models.models import AuditLog

    async with async_session() as s:
        return list((await s.execute(
            select(AuditLog).where(AuditLog.user_id == user_id, AuditLog.resource_type == resource_type)
            .order_by(AuditLog.id)
        )).scalars().all())


def _app(*routers) -> FastAPI:
    app = FastAPI()
    for mod in routers:
        app.include_router(mod.router, prefix="/api/v1")
    return app


def _client(user_id: int, *routers) -> TestClient:
    from app.core.auth import create_access_token
    from app.models.models import User
    from app.routers import webhooks as webhooks_router

    user = _run(_get(User, user_id))
    c = TestClient(_app(*(routers or (webhooks_router,))), raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"
    return c


# --------------------------------------------------------------------------- Nr. 1-3: Einreihen
async def test_enqueue_commits_with_request():
    from app.core.database import async_session
    from app.models.models import User
    from app.services.audit import write_audit
    from app.services.webhook_outbox import enqueue_event

    uid = await _user("actor")
    hid = await _hook(uid)
    async with async_session() as s:
        actor = await s.get(User, uid)
        audit = await write_audit(s, "CREATE", "record", "www.example.com.", user_id=uid,
                                  details={"zone": "example.com."}, zone_name="example.com.")
        assert await enqueue_event(s, "record.created", actor=actor, zone="example.com.", server="ns1",
                                   data={"name": "www"}, audit_log_id=audit.id) == 1
        await s.commit()
        audit_id = audit.id
    (row,) = await _rows(webhook_id=hid)
    assert row.status == "queued" and row.audit_log_id == audit_id and row.attempts == 0
    # Rollback: weder Zustellung noch Audit
    async with async_session() as s:
        actor = await s.get(User, uid)
        await write_audit(s, "UPDATE", "record", "x", user_id=uid)
        assert await enqueue_event(s, "record.updated", actor=actor, zone="example.com.", data={}) == 1
        await s.rollback()
    assert len(await _rows(webhook_id=hid)) == 1


async def test_enqueue_failure_does_not_break_request(monkeypatch):
    from app.core.database import async_session
    from app.models.models import AuditLog, User
    from app.services import webhook_outbox
    from app.services.audit import write_audit

    uid = await _user("actor")
    hid = await _hook(uid)

    def broken(**kw):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(webhook_outbox, "build_payload", broken)
    async with async_session() as s:
        actor = await s.get(User, uid)
        audit = await write_audit(s, "CREATE", "record", "a.example.com.", user_id=uid)
        assert await webhook_outbox.enqueue_event(s, "record.created", actor=actor, data={}) == 0
        await s.commit()
        audit_id = audit.id
    assert await _rows(webhook_id=hid) == []
    assert (await _get(AuditLog, audit_id)) is not None


async def test_select_recipients_scopes():
    from app.core.database import async_session
    from app.models.models import UserZoneAccess
    from app.services.webhook_outbox import select_recipients

    zone = f"{_name('z')}.example."
    actor, admin, reader, stranger, inactive = (
        await _user("actor"), await _user("admin", "admin"), await _user("reader"), await _user("stranger"),
        await _user("inactive", active=False),
    )
    async with async_session() as s:
        s.add(UserZoneAccess(user_id=reader, zone_name=zone, permission="read"))
        s.add(UserZoneAccess(user_id=inactive, zone_name=zone, permission="manage"))
        await s.commit()
    hooks = {
        "own": await _hook(actor),
        "own_filtered": await _hook(actor, events=("zone",)),
        "own_inactive": await _hook(actor, active=False),
        "admin_zones": await _hook(admin, scope="zones"),
        "admin_own": await _hook(admin),
        "reader_zones": await _hook(reader, scope="zones", events=("record.*",)),
        "reader_zones_other_event": await _hook(reader, scope="zones", events=("dnssec",)),
        "stranger_zones": await _hook(stranger, scope="zones"),
        "inactive_owner": await _hook(inactive, scope="zones"),
    }
    async with async_session() as s:
        got = {w.id for w in await select_recipients(s, event="record.created", actor_user_id=actor, zone=zone)}
        no_zone = {w.id for w in await select_recipients(s, event="record.created", actor_user_id=actor, zone=None)}
    assert got == {hooks["own"], hooks["admin_zones"], hooks["reader_zones"]}
    assert no_zone == {hooks["own"]}


# --------------------------------------------------------------------------- Nr. 4: Claim
async def test_claim_skip_locked():
    from app.core.database import async_session
    from app.models.models import WebhookDelivery
    from app.services.webhook_worker import claim_due

    uid = await _user("claim")
    hid = await _hook(uid)
    a = await _delivery(hid, uid)
    b = await _delivery(hid, uid)
    c = await _delivery(hid, uid, status="failed", attempts=2)
    future = await _delivery(hid, uid, due_in=timedelta(minutes=5))
    others = [await _delivery(hid, uid, status=st) for st in ("in_progress", "succeeded", "dead", "cancelled")]

    async with async_session() as locker:
        await locker.execute(select(WebhookDelivery.id).where(WebhookDelivery.id == a).with_for_update())
        claimed = await asyncio.wait_for(claim_due(20), 15)  # darf nicht auf die Sperre warten
        assert sorted(claimed) == sorted([b, c])
        await locker.rollback()
    rows = {r.id: r for r in await _rows(webhook_id=hid)}
    assert rows[b].status == "in_progress" and rows[b].attempts == 1 and rows[b].last_attempt_at is not None
    assert rows[c].status == "in_progress" and rows[c].attempts == 3
    assert rows[a].status == "queued" and rows[future].status == "queued"
    assert [rows[o].status for o in others] == ["in_progress", "succeeded", "dead", "cancelled"]
    assert await claim_due(20) == [a]
    assert await claim_due(20) == []


# --------------------------------------------------------------------------- Nr. 5-7: Worker
async def test_worker_run_once_backoff_to_dead(transport, caplog):
    from app.models.models import Webhook
    from app.services.webhook_worker import WebhookWorker

    transport["handler"] = lambda req: httpx.Response(500, text="Fehler")
    uid = await _user("backoff")
    hid = await _hook(uid)
    pk = await _delivery(hid, uid)
    worker = WebhookWorker()
    statuses = []
    for _ in range(6):
        await _make_due(pk)
        assert await worker.run_once() == 1
        (row,) = await _rows(id=pk)
        statuses.append(row.status)
    assert statuses == ["failed"] * 5 + ["dead"]
    assert row.attempts == 6 and row.last_status_code == 500 and row.last_error_code == "http_status"
    assert row.last_response_excerpt == "Fehler" and row.delivered_at is None
    wh = await _get(Webhook, hid)
    assert wh.consecutive_failures == 6 and wh.last_failure_at is not None and wh.last_success_at is None
    reqs = transport["requests"]
    assert [r.headers["x-dns-manager-attempt"] for r in reqs] == ["1", "2", "3", "4", "5", "6"]
    assert len({r.content for r in reqs}) == 1 and len({r.headers["x-dns-manager-signature"] for r in reqs}) == 1
    assert "endgueltig fehlgeschlagen" in caplog.text and "SECRETPATH" not in caplog.text
    assert await worker.run_once() == 0  # dead wird nie wieder geholt


async def test_worker_backoff_schedule_between_attempts(transport):
    from app.core.timeutil import utcnow
    from app.services.webhook_worker import WebhookWorker

    transport["handler"] = lambda req: httpx.Response(503, headers={"retry-after": "900"})
    uid = await _user("retry_after")
    hid = await _hook(uid)
    pk = await _delivery(hid, uid)
    t0 = utcnow()
    await WebhookWorker().run_once()
    (row,) = await _rows(id=pk)
    assert row.status == "failed" and row.next_attempt_at >= t0 + timedelta(seconds=899)
    assert await WebhookWorker().run_once() == 0  # noch nicht faellig


async def test_worker_success_sets_delivered_end_to_end(transport):
    """enqueue_event -> Commit -> Worker: der Empfaenger bekommt exakt den gespeicherten, signierten Body."""
    from app.core.database import async_session
    from app.models.models import User, Webhook
    from app.services.webhook_outbox import enqueue_event
    from app.services.webhook_service import sign
    from app.services.webhook_worker import WebhookWorker

    transport["handler"] = lambda req: httpx.Response(204)
    uid = await _user("ok")
    hid = await _hook(uid, failures=3, secret="mein-secret")
    async with async_session() as s:
        actor = await s.get(User, uid)
        await enqueue_event(s, "zone.updated", actor=actor, zone="Example.COM", server="ns1",
                            data={"zone": "example.com.", "server": "ns1", "changed": {"kind": "Master"}})
        await s.commit()
    assert await WebhookWorker().run_once() == 1
    (row,) = await _rows(webhook_id=hid)
    assert row.status == "succeeded" and row.delivered_at is not None and row.last_status_code == 204
    assert row.attempts == 1 and row.last_error_code is None
    wh = await _get(Webhook, hid)
    assert wh.last_success_at is not None and wh.consecutive_failures == 0
    (req,) = transport["requests"]
    assert str(req.url) == HOOK_URL  # entschluesselte URL
    assert req.content == row.body.encode("ascii")
    assert req.headers["x-dns-manager-signature"] == sign("mein-secret", req.content)
    assert json.loads(req.content)["zone"] == "example.com."


async def test_inactive_or_deleted_webhook_marks_dead(transport):
    from app.core.database import async_session
    from app.models.models import Webhook
    from app.services.webhook_worker import WebhookWorker

    uid = await _user("inactive_hook")
    inactive_owner = await _user("gone_owner")
    h_inactive, h_deleted, h_owner = await _hook(uid), await _hook(uid), await _hook(inactive_owner)
    d1, d2, d3 = (await _delivery(h_inactive, uid), await _delivery(h_deleted, uid),
                  await _delivery(h_owner, inactive_owner))
    async with async_session() as s:
        await s.execute(update(Webhook).where(Webhook.id == h_inactive).values(is_active=False))
        await s.execute(delete(Webhook).where(Webhook.id == h_deleted))
        await s.execute(text("UPDATE users SET is_active = 0 WHERE id = :i"), {"i": inactive_owner})
        await s.commit()
    assert await WebhookWorker().run_once() == 3
    rows = {r.id: r for r in await _rows()}
    assert (rows[d1].status, rows[d1].last_error_code) == ("dead", "webhook_inactive")
    assert (rows[d2].status, rows[d2].last_error_code) == ("dead", "webhook_deleted")
    assert (rows[d3].status, rows[d3].last_error_code) == ("dead", "owner_inactive")
    assert transport["requests"] == []


async def test_unreadable_url_marks_dead(transport):
    """[S10] Wird die verschluesselte Ziel-URL unlesbar (z. B. Schluessel verloren), wird nichts gesendet."""
    from app.core.database import async_session
    from app.services.webhook_worker import WebhookWorker

    uid = await _user("broken_url")
    hid = await _hook(uid)
    pk = await _delivery(hid, uid)
    async with async_session() as s:
        await s.execute(text("UPDATE webhooks SET url = 'enc:v1:kaputt' WHERE id = :i"), {"i": hid})
        await s.commit()
    assert await WebhookWorker().run_once() == 1
    (row,) = await _rows(id=pk)
    assert (row.status, row.last_error_code) == ("dead", "url_unreadable") and transport["requests"] == []


async def test_cancelled_rows_are_never_sent(transport):
    """[S9] access_revocation setzt queued -> cancelled per direktem UPDATE: kein Versand, kein Reset."""
    from app.core.database import async_session
    from app.models.models import WebhookDelivery
    from app.services.webhook_worker import WebhookWorker, housekeeping, reset_in_progress_on_startup

    uid = await _user("revoked")
    hid = await _hook(uid)
    pk = await _delivery(hid, uid)
    old = await _delivery(hid, uid, age=timedelta(days=40))
    async with async_session() as s:  # Muster aus services/access_revocation.py (F2F3)
        await s.execute(update(WebhookDelivery).where(WebhookDelivery.user_id == uid,
                                                      WebhookDelivery.status == "queued").values(status="cancelled"))
        await s.commit()
    assert await WebhookWorker().run_once() == 0
    assert await reset_in_progress_on_startup() == 0
    assert (await _rows(id=pk))[0].status == "cancelled" and transport["requests"] == []
    assert await housekeeping() == 1
    assert await _rows(id=old) == [] and len(await _rows(id=pk)) == 1


# --------------------------------------------------------------------------- Nr. 8-10: Reset, Housekeeping
async def test_startup_reset_in_progress():
    from app.services.webhook_worker import reset_in_progress_on_startup

    uid = await _user("reset")
    hid = await _hook(uid)
    retry = await _delivery(hid, uid, status="in_progress", attempts=2, last_attempt_ago=timedelta(seconds=5))
    exhausted = await _delivery(hid, uid, status="in_progress", attempts=6, last_attempt_ago=timedelta(seconds=5))
    untouched = await _delivery(hid, uid, status="queued")
    assert await reset_in_progress_on_startup() == 2
    rows = {r.id: r for r in await _rows()}
    assert (rows[retry].status, rows[retry].last_error_code) == ("failed", "interrupted")
    assert "unterbrochen" in rows[retry].last_error and rows[retry].attempts == 2
    assert (rows[exhausted].status, rows[exhausted].last_error_code) == ("dead", "interrupted")
    assert rows[untouched].status == "queued" and rows[untouched].last_error_code is None
    assert await reset_in_progress_on_startup() == 0


async def test_stale_reset_only_old_rows():
    from app.services.webhook_worker import STALE_IN_PROGRESS_SECONDS, reset_stale

    uid = await _user("stale")
    hid = await _hook(uid)
    old = await _delivery(hid, uid, status="in_progress", attempts=1, last_attempt_ago=timedelta(minutes=10))
    fresh = await _delivery(hid, uid, status="in_progress", attempts=1, last_attempt_ago=timedelta(seconds=10))
    assert await reset_stale(older_than_seconds=STALE_IN_PROGRESS_SECONDS) == 1
    rows = {r.id: r for r in await _rows()}
    assert rows[old].status == "failed" and rows[fresh].status == "in_progress"


async def test_housekeeping_retention(monkeypatch):
    from app.services import webhook_worker

    monkeypatch.setattr(webhook_worker, "HOUSEKEEPING_BATCH", 2)  # mehrere Bloecke
    uid = await _user("hk")
    hid = await _hook(uid)
    gone = [await _delivery(hid, uid, status=st, age=timedelta(days=31))
            for st in ("succeeded", "dead", "cancelled", "succeeded", "dead")]
    keep = [
        await _delivery(hid, uid, status="succeeded", age=timedelta(days=29)),
        await _delivery(hid, uid, status="failed", age=timedelta(days=40)),
        await _delivery(hid, uid, status="queued", age=timedelta(days=40)),
        await _delivery(hid, uid, status="in_progress", age=timedelta(days=40)),
    ]
    assert await webhook_worker.housekeeping() == 5
    assert sorted(r.id for r in await _rows()) == sorted(keep)
    assert await webhook_worker.housekeeping() == 0
    assert all(g not in keep for g in gone)


async def test_queue_depth_and_stats_for_user():
    from app.core.database import async_session
    from app.services.webhook_outbox import queue_depth, stats_for_user

    uid = await _user("stats")
    h1, h2 = await _hook(uid), await _hook(uid)
    for st in ("queued", "queued", "failed", "dead", "cancelled"):
        await _delivery(h1, uid, status=st)
    await _delivery(h2, uid, status="succeeded")
    async with async_session() as s:
        assert await stats_for_user(s, uid) == {h1: {"queued": 2, "failed": 1, "dead": 1, "cancelled": 1},
                                                h2: {"succeeded": 1}}
        depth = await queue_depth(s)
    assert depth == {"queued": 2, "failed": 1, "dead": 1, "cancelled": 1, "succeeded": 1}


# --------------------------------------------------------------------------- Nr. 11: HTTP-Ebene
def test_create_list_update_delete_audited():
    from app.models.models import Webhook
    from app.services.webhook_service import sign

    uid = _run(_user("crud"))
    c = _client(uid)
    r = c.post("/api/v1/auth/me/webhooks", json={"name": " Slack ", "url": HOOK_URL, "events": ["record", "zone.*"],
                                                 "scope": "zones"})
    assert r.status_code == 201, r.text
    created = r.json()
    hid, secret = created["webhook"]["id"], created["secret"]
    assert len(secret) >= 40 and "nur jetzt angezeigt" in created["warning"]
    assert created["webhook"]["name"] == "Slack" and created["webhook"]["scope"] == "zones"
    raw = _run(_raw_webhook(hid))
    assert raw["url"].startswith("enc:v1:") and raw["secret"].startswith("enc:v1:")  # verschluesselt [S10]

    # Liste (Session): volle URL, Statistik, Katalog
    pending = [_run(_delivery(hid, uid, secret=secret, status=st)) for st in ("queued", "failed")]
    dead = _run(_delivery(hid, uid, secret=secret, status="dead"))
    body = c.get("/api/v1/auth/me/webhooks").json()
    (item,) = body["webhooks"]
    assert item["url"] == HOOK_URL and item["url_display"] == "https://hooks.example.com/…" and item["has_url"]
    assert item["stats"]["queued"] == 1 and item["stats"]["failed"] == 1 and item["stats"]["dead"] == 1
    assert item["events"] == ["record", "zone.*"] and item["has_secret"] is True
    assert "webhook.test" not in body["available_events"] and body["max_webhooks"] == 20

    # Aendern: Name/Ereignisse/Scope, gleicher Host + anderer Pfad -> "url": "changed"
    r = c.put(f"/api/v1/auth/me/webhooks/{hid}", json={"name": "Teams", "events": ["dnssec"], "scope": "own",
                                                       "url": "https://hooks.example.com/anderer/pfad"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "Teams" and out["events"] == ["dnssec"] and out["scope"] == "own"
    assert "new_secret" not in out and out["updated_at"] is not None
    # Rotation: offene Zustellungen neu signiert, tote nicht
    r = c.put(f"/api/v1/auth/me/webhooks/{hid}", json={"rotate_secret": True})
    new_secret = r.json()["new_secret"]
    assert new_secret and new_secret != secret
    rows = {d.id: d for d in _run(_rows(webhook_id=hid))}
    for pk in pending:
        assert rows[pk].signature == sign(new_secret, rows[pk].body.encode("ascii"))
    assert rows[dead].signature == sign(secret, rows[dead].body.encode("ascii"))
    assert _run(_get(Webhook, hid)).secret == new_secret
    # Deaktivieren: offene -> dead/webhook_inactive
    r = c.put(f"/api/v1/auth/me/webhooks/{hid}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    rows = {d.id: d for d in _run(_rows(webhook_id=hid))}
    assert all((rows[pk].status, rows[pk].last_error_code) == ("dead", "webhook_inactive") for pk in pending)
    # Nichts geaendert -> kein Audit
    assert c.put(f"/api/v1/auth/me/webhooks/{hid}", json={"is_active": False}).status_code == 200
    # Fremder Webhook -> 404
    other = _run(_user("crud_other"))
    assert _client(other).put(f"/api/v1/auth/me/webhooks/{hid}", json={"name": "x"}).status_code == 404
    assert _client(other).delete(f"/api/v1/auth/me/webhooks/{hid}").status_code == 404
    # Loeschen: Zustellungen weg
    r = c.delete(f"/api/v1/auth/me/webhooks/{hid}")
    assert r.status_code == 200 and r.json() == {"message": "Webhook gelöscht", "deleted_deliveries": 3}
    assert _run(_rows(webhook_id=hid)) == [] and _run(_get(Webhook, hid)) is None

    audits = _run(_audits(uid))
    assert [a.action for a in audits] == ["WEBHOOK_CREATE", "WEBHOOK_UPDATE", "WEBHOOK_SECRET_ROTATE",
                                          "WEBHOOK_UPDATE", "WEBHOOK_DELETE"]
    create, update1, rotate, deactivate, delete_ = audits
    assert create.details["url_host"] == "hooks.example.com" and create.details["scope"] == "zones"
    assert create.details["events"] == ["record", "zone.*"] and create.details["webhook_id"] == hid
    ch = update1.details["changed"]
    assert ch["name"] == {"from": "Slack", "to": "Teams"} and ch["url"] == "changed" and "url_host" not in ch
    assert ch["scope"] == {"from": "zones", "to": "own"} and ch["events"]["to"] == ["dnssec"]
    assert rotate.details == {"webhook_id": hid, "resigned_pending": 2}
    assert deactivate.details["changed"]["is_active"] == {"from": True, "to": False}
    assert deactivate.details["cancelled_pending"] == 2
    assert delete_.details == {"webhook_id": hid, "url_host": "hooks.example.com", "deleted_deliveries": 3}
    dump = json.dumps([a.details for a in audits])
    for leak in ("SECRETPATH", "anderer/pfad", secret, new_secret):
        assert leak not in dump


async def _raw_webhook(hid):
    from app.core.database import async_session

    async with async_session() as s:
        row = (await s.execute(text("SELECT url, secret FROM webhooks WHERE id = :i"), {"i": hid})).one()
        return {"url": row[0], "secret": row[1]}


def test_update_changes_host_and_reports_url_host():
    uid = _run(_user("host"))
    hid = _run(_hook(uid))
    r = _client(uid).put(f"/api/v1/auth/me/webhooks/{hid}", json={"url": "https://other.example.org:8443/x"})
    assert r.status_code == 200 and r.json()["url_display"] == "https://other.example.org:8443/…"
    (a,) = _run(_audits(uid))
    assert a.details["changed"] == {"url_host": {"from": "hooks.example.com", "to": "other.example.org:8443"}}


def test_list_masks_url_for_panel_token():
    from app.core.database import async_session
    from app.services import panel_token

    uid = _run(_user("token"))
    hid = _run(_hook(uid))

    async def mk():
        async with async_session() as s:
            _, plain = await panel_token.create_token(s, uid, "ci")
            await s.commit()
            return plain

    plain = _run(mk())
    from app.routers import webhooks as webhooks_router

    c = TestClient(_app(webhooks_router), raise_server_exceptions=False)
    h = {"Authorization": f"Bearer {plain}"}
    r = c.get("/api/v1/auth/me/webhooks", headers=h)
    assert r.status_code == 200, r.text
    (item,) = r.json()["webhooks"]
    assert item["url"] is None and item["url_display"] == "https://hooks.example.com/…"
    assert "SECRETPATH" not in r.text
    for method, path in (("GET", f"/api/v1/auth/me/webhooks/{hid}/deliveries"),
                         ("POST", f"/api/v1/auth/me/webhooks/{hid}/test"),
                         ("PUT", f"/api/v1/auth/me/webhooks/{hid}")):
        assert c.request(method, path, headers=h, json={}).status_code == 403, path


def test_list_shows_unreadable_url_as_null():
    uid = _run(_user("unreadable"))
    hid = _run(_hook(uid))

    async def break_url():
        from app.core.database import async_session

        async with async_session() as s:
            await s.execute(text("UPDATE webhooks SET url = 'enc:v1:kaputt' WHERE id = :i"), {"i": hid})
            await s.commit()

    _run(break_url())
    (item,) = _client(uid).get("/api/v1/auth/me/webhooks").json()["webhooks"]
    assert item["url"] is None and item["url_display"] is None and item["has_url"] is False


def test_deliveries_pagination_and_filters():
    from app.core.database import async_session
    from app.core.timeutil import utcnow
    from app.models.models import WebhookDelivery

    uid = _run(_user("pages"))
    hid = _run(_hook(uid))
    ids = [_run(_delivery(hid, uid, status="dead" if i % 6 == 0 else "succeeded",
                          event="zone.created" if i % 10 == 0 else "record.created",
                          age=timedelta(minutes=60 - i))) for i in range(30)]

    async def stamp():  # created_at eindeutig steigend
        async with async_session() as s:
            for i, pk in enumerate(ids):
                await s.execute(update(WebhookDelivery).where(WebhookDelivery.id == pk)
                                .values(created_at=utcnow() - timedelta(minutes=60 - i)))
            await s.commit()

    _run(stamp())
    c = _client(uid)
    base = f"/api/v1/auth/me/webhooks/{hid}/deliveries"
    page1 = c.get(base).json()
    assert page1["total"] == 30 and page1["limit"] == 25 and page1["offset"] == 0
    assert [d["id"] for d in page1["deliveries"]] == list(reversed(ids))[:25]  # neueste zuerst
    page2 = c.get(base, params={"offset": 25}).json()
    assert [d["id"] for d in page2["deliveries"]] == list(reversed(ids))[25:]
    dead = c.get(base, params={"status": "dead"}).json()
    assert dead["total"] == 5 and all(d["status"] == "dead" for d in dead["deliveries"])
    zones = c.get(base, params={"event": "zone.created", "limit": 2}).json()
    assert zones["total"] == 3 and len(zones["deliveries"]) == 2
    d0 = page1["deliveries"][0]
    assert "body" not in d0 and d0["can_retry"] is True and d0["next_attempt_at"] is None
    # Einzelne Zustellung mit Body und Headern
    detail = c.get(f"{base}/{ids[0]}").json()
    assert detail["body"]["v"] == 2 and detail["request_headers"]["X-DNS-Manager-Delivery"] == detail["delivery_id"]
    assert detail["request_headers"]["X-DNS-Manager-Signature"].startswith("sha256=")
    # Fremde Webhooks / Zustellungen -> 404
    other = _run(_user("pages_other"))
    other_hook = _run(_hook(other))
    other_delivery = _run(_delivery(other_hook, other))
    assert _client(other).get(base).status_code == 404
    assert c.get(f"{base}/{other_delivery}").status_code == 404
    assert c.get(f"/api/v1/auth/me/webhooks/{other_hook}/deliveries/{other_delivery}").status_code == 404
    assert c.get(f"{base}/99999999").json()["detail"] == "Zustellung nicht gefunden"


def test_retry_rules():
    from app.core.timeutil import utcnow
    from app.models.models import Webhook
    from app.services.webhook_service import sign

    uid = _run(_user("retry"))
    hid = _run(_hook(uid, secret="alt"))
    c = _client(uid)
    base = f"/api/v1/auth/me/webhooks/{hid}/deliveries"
    queued = _run(_delivery(hid, uid, status="queued"))
    running = _run(_delivery(hid, uid, status="in_progress", attempts=1))
    failed = _run(_delivery(hid, uid, status="failed", attempts=2, due_in=timedelta(minutes=10)))
    dead = _run(_delivery(hid, uid, status="dead", attempts=6, secret="noch-aelter"))
    cancelled = _run(_delivery(hid, uid, status="cancelled"))
    for pk in (queued, running):
        r = c.post(f"{base}/{pk}/retry")
        assert r.status_code == 409 and "bereits eingeplant" in r.json()["detail"]
    t0 = utcnow()
    r = c.post(f"{base}/{failed}/retry")
    assert r.status_code == 200 and r.json()["message"] == "Zustellung neu eingeplant"
    (row,) = _run(_rows(id=failed))
    assert row.status == "failed" and row.max_attempts == 6 and row.next_attempt_at <= t0 + timedelta(seconds=5)
    r = c.post(f"{base}/{dead}/retry")
    assert r.status_code == 200 and r.json()["delivery"]["status"] == "queued"
    (row,) = _run(_rows(id=dead))
    assert row.status == "queued" and row.attempts == 6 and row.max_attempts == 7
    assert row.signature == sign("alt", row.body.encode("ascii"))  # neu signiert mit aktuellem Secret
    assert c.post(f"{base}/{cancelled}/retry").status_code == 200
    # inaktiver Webhook -> 409
    _run(_set(Webhook, hid, is_active=False))
    r = c.post(f"{base}/{dead}/retry")
    assert r.status_code == 409 and "deaktiviert" in r.json()["detail"]
    audits = _run(_audits(uid))
    assert [a.action for a in audits] == ["WEBHOOK_DELIVERY_RETRY"] * 3
    assert audits[1].details["previous_status"] == "dead" and audits[1].details["event"] == "record.created"
    assert audits[0].details["previous_status"] == "failed" and len(audits[0].details["delivery_id"]) == 36


async def _set(model, pk, **values):
    from app.core.database import async_session

    async with async_session() as s:
        await s.execute(update(model).where(model.id == pk).values(**values))
        await s.commit()


def test_retry_with_unreadable_secret_is_409():
    uid = _run(_user("retry_secret"))
    hid = _run(_hook(uid))
    pk = _run(_delivery(hid, uid, status="dead", attempts=6))

    async def break_secret():
        from app.core.database import async_session

        async with async_session() as s:
            await s.execute(text("UPDATE webhooks SET secret = 'enc:v1:kaputt' WHERE id = :i"), {"i": hid})
            await s.commit()

    _run(break_secret())
    r = _client(uid).post(f"/api/v1/auth/me/webhooks/{hid}/deliveries/{pk}/retry")
    assert r.status_code == 409 and "Secret erneuern" in r.json()["detail"]
    assert _run(_rows(id=pk))[0].status == "dead"


def test_test_endpoint_sync_and_cooldown(transport):
    from app.routers import webhooks as webhooks_router

    uid = _run(_user("test"))
    hid = _run(_hook(uid, events=("zone",)))  # Filter wird ignoriert
    c = _client(uid)
    r = c.post(f"/api/v1/auth/me/webhooks/{hid}/test", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and "HTTP 200" in body["message"]
    (row,) = _run(_rows(webhook_id=hid))
    assert (row.status, row.max_attempts, row.attempts, row.event) == ("succeeded", 1, 1, "webhook.test")
    assert json.loads(row.body)["data"]["webhook_id"] == hid
    r = c.post(f"/api/v1/auth/me/webhooks/{hid}/test", json={})
    assert r.status_code == 429
    webhooks_router._last_test.clear()
    transport["handler"] = lambda req: httpx.Response(500)
    r = c.post(f"/api/v1/auth/me/webhooks/{hid}/test", json={})
    assert r.status_code == 200 and r.json()["success"] is False
    rows = _run(_rows(webhook_id=hid))
    assert [x.status for x in rows] == ["succeeded", "dead"]
    assert len(transport["requests"]) == 2
    # der Worker fasst Test-Zeilen nie an
    from app.services.webhook_worker import WebhookWorker

    assert _run(WebhookWorker().run_once()) == 0


def test_limit_20_webhooks():
    uid = _run(_user("limit"))
    for _ in range(20):
        _run(_hook(uid))
    r = _client(uid).post("/api/v1/auth/me/webhooks", json={"name": "x", "url": HOOK_URL})
    assert r.status_code == 400 and r.json()["detail"] == "Maximal 20 Webhooks pro Benutzer"


# --------------------------------------------------------------------------- Nr. 13/14: Fremd-Endpunkte
def test_zone_delete_event_before_acl_cleanup(monkeypatch):
    from authfakes import FakePDNS
    from app.core.database import async_session
    from app.models.models import UserZoneAccess
    from app.routers import zones as zones_router
    from app.services.pdns_client import pdns_manager

    monkeypatch.setattr(pdns_manager, "clients", {"srv1": FakePDNS()})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    zone = f"{_name('zd')}.example."
    admin = _run(_user("zd_admin", "admin"))
    member = _run(_user("zd_member"))
    outsider = _run(_user("zd_outsider"))

    async def acl():
        async with async_session() as s:
            s.add(UserZoneAccess(user_id=member, zone_name=zone, permission="read"))
            await s.commit()

    _run(acl())
    member_hook = _run(_hook(member, scope="zones"))
    outsider_hook = _run(_hook(outsider, scope="zones"))
    r = _client(admin, zones_router).delete(f"/api/v1/zones/srv1/{zone}")
    assert r.status_code == 200, r.text
    (row,) = _run(_rows(webhook_id=member_hook))
    assert row.event == "zone.deleted" and row.zone_name == zone and row.status == "queued"
    assert _run(_rows(webhook_id=outsider_hook)) == []

    async def acl_left():
        async with async_session() as s:
            return (await s.execute(select(UserZoneAccess).where(UserZoneAccess.zone_name == zone))).all()

    assert _run(acl_left()) == []  # Rechte danach entfernt


def test_user_delete_removes_deliveries():
    from app.models.models import Webhook
    from app.routers import auth as auth_router

    admin = _run(_user("ud_admin", "admin"))
    victim = _run(_user("ud_victim"))
    hid = _run(_hook(victim))
    for _ in range(3):
        _run(_delivery(hid, victim))
    keep_hook = _run(_hook(admin))
    keep = _run(_delivery(keep_hook, admin))
    r = _client(admin, auth_router).delete(f"/api/v1/auth/users/{victim}")
    assert r.status_code == 200, r.text
    assert _run(_rows(user_id=victim)) == [] and _run(_get(Webhook, hid)) is None
    assert [d.id for d in _run(_rows(user_id=admin))] == [keep]
    (a,) = [x for x in _run(_audits(admin, "user")) if x.action == "USER_DELETE"]
    assert a.details["deleted_webhook_deliveries"] == 3 and a.details["deleted_webhooks"] == 1


# --------------------------------------------------------------------------- Welle-1-Integration (F2F3)
@pytest.mark.wave_integration
async def test_access_revocation_cancels_and_worker_accepts(transport):
    """[S9] ``access_revocation.revoke_all`` (WS-F2F3) deaktiviert die Webhooks und setzt ``queued`` -> ``cancelled``;
    der Worker sendet nichts mehr, ``failed`` wird beim naechsten Versuch als ``webhook_inactive`` verworfen."""
    from app.core.database import async_session
    from app.models.models import Webhook
    from app.services import access_revocation
    from app.services.webhook_worker import WebhookWorker

    uid = await _user("revoke_all")
    hid = await _hook(uid)
    queued = await _delivery(hid, uid)
    failed = await _delivery(hid, uid, status="failed", attempts=1)
    async with async_session() as s:
        await access_revocation.revoke_all(s, uid, reason="test")
        await s.commit()
    assert (await _get(Webhook, hid)).is_active is False
    assert await WebhookWorker().run_once() == 1  # nur die failed-Zeile
    rows = {r.id: r for r in await _rows(webhook_id=hid)}
    assert rows[queued].status == "cancelled"
    assert (rows[failed].status, rows[failed].last_error_code) == ("dead", "webhook_inactive")
    assert transport["requests"] == []
