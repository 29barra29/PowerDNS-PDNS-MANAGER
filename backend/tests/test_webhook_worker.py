"""Webhook-Worker und Verwaltungsrouter ohne DB (F6 5.7, 3.1–3.8; Bauplan B.9/B.10, [S9], [S10]).

- ``_process`` gegen eine Fake-Session-Fabrik (``webhook_worker.async_session`` ersetzt) und ``httpx.MockTransport``:
  Erfolg, Fehler mit Backoff, Verwerfen (Webhook geloescht/inaktiv, Besitzer inaktiv), unlesbare URL, extern
  ``cancelled`` (vor und waehrend des Versands), Zeile waehrend des Versands geloescht.
- Schleife: ``run_once`` mit Fehlern einzelner Zustellungen, Start/Stopp/Weckruf (auch aus einem anderen Thread),
  Abbruch haengender Versuche beim Stopp, ``worker_state``.
- Metrik-Hooks: ``record_attempt`` -> ``pdnsmgr_webhook_deliveries_total`` mit Mapping aus B.10, Beobachter.
- Router mit ``FakeSession``: Serialisierung (``url_display=null`` bei unlesbarer URL), Test-Zustellung inkl.
  Cooldown, Session-Pflicht der neuen Routen, Eingabepruefung. Die SQL-Seite prueft ``test_webhook_outbox_db.py``.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from authfakes import FakeSession, bearer, build_app, make_token, make_user
from app.core import metrics as prom
from app.core.auth import SESSION_REQUIRED_DETAIL, create_access_token
from app.core.config import settings
from app.core.secrets import UNREADABLE
from app.core.timeutil import utcnow
from app.models.models import User, Webhook, WebhookDelivery
from app.routers import webhooks as webhooks_router
from app.services import webhook_sender as sender
from app.services import webhook_service as wsvc
from app.services import webhook_worker as worker

URL = "https://hooks.example.com/p/SECRET"


@pytest.fixture(autouse=True)
def _clean_worker_state():
    worker.reset_for_tests()
    webhooks_router._last_test.clear()
    webhooks_router._tests_running.clear()
    yield
    worker.reset_for_tests()
    webhooks_router._last_test.clear()
    webhooks_router._tests_running.clear()


@pytest.fixture
def transport(monkeypatch):
    """MockTransport mit austauschbarem Handler; Ziele ohne DNS (private Ziele erlaubt -> kein Pinning)."""
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", True)
    state = {"handler": lambda req: httpx.Response(200, text="ok"), "requests": []}

    def handle(req):
        state["requests"].append(req)
        return state["handler"](req)

    mock = httpx.MockTransport(handle)
    monkeypatch.setattr(sender, "_client_factory",
                        lambda: httpx.AsyncClient(transport=mock, follow_redirects=False, trust_env=False))
    return state


@pytest.fixture
def metrics_seen(monkeypatch):
    seen = []
    monkeypatch.setattr(prom, "record_webhook_delivery", lambda status: seen.append(status))
    return seen


# --------------------------------------------------------------------------- Fake-Sessions fuer den Worker
class Store:
    def __init__(self, *objs):
        self.objs = {}
        self.commits = 0
        for o in objs:
            self.put(o)

    def put(self, obj):
        self.objs[(type(obj), obj.id)] = obj

    def drop(self, model, pk):
        self.objs.pop((model, pk), None)

    def factory(self):
        store = self

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, model, pk):
                return store.objs.get((model, pk))

            async def commit(self):
                store.commits += 1

        return _Session()


def _delivery(**kw):
    data = dict(id=11, delivery_id="d-11", event_id="e-1", webhook_id=5, user_id=1, event="record.created",
                body='{"v":2}', signature="sha256=" + "b" * 64, status="in_progress", attempts=1, max_attempts=6,
                next_attempt_at=utcnow(), created_at=utcnow(), last_attempt_at=utcnow())
    data.update(kw)
    return WebhookDelivery(**data)


def _hook(**kw):
    data = dict(id=5, user_id=1, name="h", url=URL, secret="s3cret", events=["*"], is_active=True, scope="own",
                consecutive_failures=0)
    data.update(kw)
    return Webhook(**data)


def _owner(active=True):
    return User(id=1, username="owner", role="user", is_active=active, hashed_password="x")


@pytest.fixture
def store(monkeypatch):
    s = Store(_delivery(), _hook(), _owner())
    monkeypatch.setattr(worker, "async_session", s.factory)
    return s


# --------------------------------------------------------------------------- _process
async def test_process_success(store, transport, metrics_seen):
    observed = []
    worker.register_attempt_observer(lambda st, dur, code: observed.append((st, code)))
    assert await worker._process(11) == "succeeded"
    d, wh = store.objs[(WebhookDelivery, 11)], store.objs[(Webhook, 5)]
    assert d.status == "succeeded" and d.delivered_at is not None and d.last_status_code == 200
    assert d.last_response_excerpt == "ok" and wh.last_success_at is not None and wh.consecutive_failures == 0
    (req,) = transport["requests"]
    assert str(req.url) == URL and req.content == b'{"v":2}'
    assert req.headers["x-dns-manager-attempt"] == "1" and req.headers["x-dns-manager-delivery"] == "d-11"
    assert req.headers["x-dns-manager-signature"] == "sha256=" + "b" * 64
    assert metrics_seen == ["succeeded"] and observed == [("succeeded", 200)]
    assert store.commits == 1  # nur der Ergebnis-Commit (Lesen ohne Commit)


async def test_process_failure_schedules_backoff(store, transport, metrics_seen, caplog):
    transport["handler"] = lambda req: httpx.Response(500, text="kaputt")
    before = utcnow()
    assert await worker._process(11) == "failed"
    d, wh = store.objs[(WebhookDelivery, 11)], store.objs[(Webhook, 5)]
    assert d.last_error_code == "http_status" and d.last_response_excerpt == "kaputt"
    assert before + timedelta(seconds=59) <= d.next_attempt_at <= utcnow() + timedelta(seconds=67)
    assert wh.consecutive_failures == 1 and wh.last_failure_at is not None
    assert metrics_seen == ["failed"]
    # letzter Versuch -> dead + WARNING ohne URL-Pfad
    d.status, d.attempts = "in_progress", 6
    assert await worker._process(11) == "dead"
    assert "endgueltig fehlgeschlagen" in caplog.text and "SECRET" not in caplog.text
    assert "hooks.example.com" in caplog.text
    assert metrics_seen == ["failed", "dead"]


@pytest.mark.parametrize("mutate,code,metric", [
    (lambda s: s.drop(Webhook, 5), "webhook_deleted", "cancelled"),
    (lambda s: setattr(s.objs[(Webhook, 5)], "is_active", False), "webhook_inactive", "cancelled"),
    (lambda s: setattr(s.objs[(User, 1)], "is_active", False), "owner_inactive", "cancelled"),
    (lambda s: s.drop(User, 1), "owner_inactive", "cancelled"),
    (lambda s: setattr(s.objs[(Webhook, 5)], "url", UNREADABLE), "url_unreadable", "dead"),
    (lambda s: setattr(s.objs[(WebhookDelivery, 11)], "signature", ""), "secret_unreadable", "dead"),
])
async def test_process_discards_without_sending(store, transport, metrics_seen, mutate, code, metric):
    mutate(store)
    assert await worker._process(11) == "dead"
    d = store.objs[(WebhookDelivery, 11)]
    assert d.status == "dead" and d.last_error_code == code and d.last_error
    assert transport["requests"] == [] and metrics_seen == [metric] and store.commits == 1


@pytest.mark.parametrize("status", ["cancelled", "queued", "succeeded", "dead"])
async def test_process_ignores_rows_not_in_progress(store, transport, metrics_seen, status):
    store.objs[(WebhookDelivery, 11)].status = status
    assert await worker._process(11) is None
    assert store.objs[(WebhookDelivery, 11)].status == status
    assert transport["requests"] == [] and metrics_seen == [] and store.commits == 0


async def test_process_missing_row(store, transport):
    store.drop(WebhookDelivery, 11)
    assert await worker._process(11) is None and transport["requests"] == []


async def test_process_accepts_external_cancel_during_send(store, transport, metrics_seen):
    """[S9] access_revocation setzt waehrend des Versands ``cancelled`` -> der Worker ueberschreibt das nicht."""
    def handler(req):
        store.objs[(WebhookDelivery, 11)].status = "cancelled"
        return httpx.Response(500)

    transport["handler"] = handler
    assert await worker._process(11) == "cancelled"
    d, wh = store.objs[(WebhookDelivery, 11)], store.objs[(Webhook, 5)]
    assert d.status == "cancelled" and d.last_status_code == 500
    assert wh.consecutive_failures == 0  # kein Zaehler fuer einen verworfenen Versuch
    assert metrics_seen == ["cancelled"]


async def test_process_row_deleted_during_send(store, transport, metrics_seen):
    def handler(req):
        store.drop(WebhookDelivery, 11)
        return httpx.Response(200)

    transport["handler"] = handler
    assert await worker._process(11) is None and metrics_seen == []


# --------------------------------------------------------------------------- Metriken
def _counter(label):
    return prom.WEBHOOK_DELIVERIES.labels(label)._value.get()


@pytest.mark.parametrize("status,label", [
    ("succeeded", "success"), ("failed", "retry"), ("dead", "dead"), ("cancelled", "cancelled"),
])
def test_record_attempt_maps_to_metric_labels(status, label):
    before = _counter(label)
    worker.record_attempt(status, 0.1, 200)
    assert _counter(label) == before + 1


def test_record_attempt_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(prom, "record_webhook_delivery", boom)
    worker.register_attempt_observer(boom)
    worker.record_attempt("succeeded")  # kein Fehler


# --------------------------------------------------------------------------- Schleife
async def test_run_once_processes_claimed_and_survives_errors(monkeypatch, caplog):
    seen = []

    async def claim(limit=worker.CLAIM_BATCH, **kw):
        return [1, 2, 3]

    async def process(pk):
        seen.append(pk)
        if pk == 2:
            raise RuntimeError("geheim https://x.example/p")
        return "succeeded"

    monkeypatch.setattr(worker, "claim_due", claim)
    monkeypatch.setattr(worker, "_process", process)
    w = worker.WebhookWorker()
    assert await w.run_once() == 3
    assert sorted(seen) == [1, 2, 3]
    assert "unerwarteter Fehler (RuntimeError)" in caplog.text and "x.example" not in caplog.text


async def test_run_once_respects_parallel_limit(monkeypatch):
    active = {"now": 0, "max": 0}

    async def claim(limit=worker.CLAIM_BATCH, **kw):
        return list(range(10))

    async def process(pk):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.01)
        active["now"] -= 1

    monkeypatch.setattr(worker, "claim_due", claim)
    monkeypatch.setattr(worker, "_process", process)
    assert await worker.WebhookWorker().run_once() == 10
    assert active["max"] == worker.MAX_PARALLEL


@pytest.fixture
def loop_fakes(monkeypatch):
    calls = {"claims": 0, "stale": [], "housekeeping": 0, "due": []}

    async def claim(limit=worker.CLAIM_BATCH, **kw):
        calls["claims"] += 1
        due, calls["due"] = calls["due"], []
        return due

    async def stale(*, older_than_seconds, now=None):
        calls["stale"].append(older_than_seconds)
        return 0

    async def housekeeping(now=None, **kw):
        calls["housekeeping"] += 1
        return 0

    async def process(pk):
        return "succeeded"

    monkeypatch.setattr(worker, "claim_due", claim)
    monkeypatch.setattr(worker, "reset_stale", stale)
    monkeypatch.setattr(worker, "housekeeping", housekeeping)
    monkeypatch.setattr(worker, "_process", process)
    monkeypatch.setattr(worker, "POLL_SECONDS", 30.0)
    return calls


async def _until(cond, timeout=2.0):
    end = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > end:
            raise AssertionError("Bedingung nicht erreicht")
        await asyncio.sleep(0.01)


async def test_worker_start_notify_stop(loop_fakes):
    assert worker.worker_state()["running"] is False
    worker.notify_worker()  # ohne Worker wirkungslos
    worker.start_worker()
    worker.start_worker()  # idempotent
    await _until(lambda: loop_fakes["claims"] >= 1)
    state = worker.worker_state()
    assert state["running"] is True and state["last_loop_at"] is not None
    assert state["enabled"] == bool(settings.BACKGROUND_WORKERS_ENABLED)
    assert loop_fakes["stale"] == [worker.STALE_IN_PROGRESS_SECONDS] and loop_fakes["housekeeping"] == 1
    # Weckruf: trotz POLL_SECONDS=30 laeuft sofort die naechste Runde
    loop_fakes["due"] = [7]
    worker.notify_worker()
    await _until(lambda: loop_fakes["claims"] >= 3)  # Runde mit Zeile 7, danach sofort die naechste
    assert loop_fakes["housekeeping"] == 1  # hoechstens einmal pro Stunde
    await worker.stop_worker()
    assert worker.worker_state()["running"] is False
    await worker.stop_worker()  # idempotent


async def test_worker_wake_from_other_thread(loop_fakes):
    worker.start_worker()
    await _until(lambda: loop_fakes["claims"] >= 1)
    t = threading.Thread(target=worker.notify_worker)
    t.start()
    t.join()
    await _until(lambda: loop_fakes["claims"] >= 2)
    await worker.stop_worker()


async def test_worker_loop_error_backs_off_and_records(monkeypatch, loop_fakes, caplog):
    async def broken(limit=worker.CLAIM_BATCH, **kw):
        loop_fakes["claims"] += 1
        raise RuntimeError("DB weg")

    monkeypatch.setattr(worker, "claim_due", broken)
    worker.start_worker()
    await _until(lambda: loop_fakes["claims"] >= 1)
    await _until(lambda: worker.worker_state()["last_error_at"] is not None)
    assert worker.worker_state()["running"] is True  # Schleife laeuft weiter
    assert "Fehler in der Schleife" in caplog.text
    await worker.stop_worker()


async def test_worker_stop_cancels_hanging_delivery(monkeypatch, loop_fakes):
    started = asyncio.Event()

    async def hanging(pk):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(worker, "_process", hanging)
    monkeypatch.setattr(worker, "STOP_TIMEOUT_SECONDS", 0.2)
    loop_fakes["due"] = [1]
    worker.start_worker()
    await asyncio.wait_for(started.wait(), 2)
    t0 = asyncio.get_running_loop().time()
    await worker._worker.stop(timeout=0.2)
    assert asyncio.get_running_loop().time() - t0 < 2
    assert worker.worker_state()["running"] is False


# --------------------------------------------------------------------------- Router (FakeSession)
def _session_client(session, user):
    c = TestClient(build_app(session, webhooks_router), raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(user.id)}, user=user)}"
    return c


def test_list_serialization_marks_unreadable_url():
    user = make_user(role="user")
    ok = _hook(id=5, consecutive_failures=None, scope=None, events=None)
    broken = _hook(id=6, url=UNREADABLE, secret=UNREADABLE)
    session = FakeSession(user_row=user, extra={Webhook: [broken, ok]})
    r = _session_client(session, user).get("/api/v1/auth/me/webhooks")
    assert r.status_code == 200, r.text
    body = r.json()
    b, o = body["webhooks"]
    assert (b["url"], b["url_display"], b["has_url"], b["has_secret"]) == (None, None, False, False)
    assert o["url"] == URL and o["url_display"] == "https://hooks.example.com/…" and o["has_url"] is True
    assert o["scope"] == "own" and o["events"] == ["*"] and o["consecutive_failures"] == 0
    assert o["stats"] == {"queued": 0, "in_progress": 0, "failed": 0, "succeeded": 0, "dead": 0, "cancelled": 0}
    assert "webhook.test" not in body["available_events"] and "record.created" in body["available_events"]
    assert body["event_categories"] == ["record", "zone", "dnssec", "dyndns"]
    assert body["max_attempts"] == 6 and body["retention_days"] == 30 and body["max_webhooks"] == 20
    assert body["worker_running"] is False and body["worker_enabled"] == bool(settings.BACKGROUND_WORKERS_ENABLED)


def test_test_endpoint_sends_once_and_enforces_cooldown(transport, metrics_seen):
    user = make_user(role="user")
    hook = _hook(is_active=False)  # Test geht auch fuer inaktive Webhooks
    session = FakeSession(user_row=user, extra={Webhook: [hook]})
    c = _session_client(session, user)
    r = c.post("/api/v1/auth/me/webhooks/5/test", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True and body["message"].startswith("Test erfolgreich (HTTP 200,")
    d = body["delivery"]
    assert d["event"] == "webhook.test" and d["status"] == "succeeded"
    assert d["attempts"] == 1 and d["max_attempts"] == 1 and d["can_retry"] is False  # Webhook inaktiv
    (req,) = transport["requests"]
    assert req.headers["x-dns-manager-event"] == "webhook.test"
    sent = req.content.decode("ascii")
    assert '"webhook_id":5' in sent and '"via":"session"' in sent
    assert req.headers["x-dns-manager-signature"] == wsvc.sign("s3cret", req.content)
    assert hook.last_success_at is not None and metrics_seen == ["succeeded"]
    (row,) = [o for o in session.added if isinstance(o, WebhookDelivery)]
    assert row.status == "succeeded" and row.zone_name is None
    # Cooldown
    r = c.post("/api/v1/auth/me/webhooks/5/test", json={})
    assert r.status_code == 429 and "Sekunden warten" in r.json()["detail"]
    assert len(transport["requests"]) == 1


def test_test_endpoint_failure_and_unreadable_secret(transport, metrics_seen):
    user = make_user(role="user")
    transport["handler"] = lambda req: httpx.Response(500)
    session = FakeSession(user_row=user, extra={Webhook: [_hook()]})
    r = _session_client(session, user).post("/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False and body["message"] == "Test fehlgeschlagen: HTTP 500"
    assert body["delivery"]["status"] == "dead" and body["delivery"]["can_retry"] is True  # max_attempts=1
    webhooks_router._last_test.clear()
    session = FakeSession(user_row=user, extra={Webhook: [_hook(secret=UNREADABLE)]})
    r = _session_client(session, user).post("/api/v1/auth/me/webhooks/5/test")
    body = r.json()
    assert body["success"] is False and body["delivery"]["last_error_code"] == "secret_unreadable"
    assert "Secret erneuern" in body["message"]
    assert len(transport["requests"]) == 1  # unlesbares Secret: nichts gesendet


class TxSession(FakeSession):
    """FakeSession mit Transaktionszustand wie AsyncSession: Lesen/Schreiben oeffnet (Autobegin, Verbindung
    aus dem Pool), ``commit``/``rollback`` gibt frei. ``on_refresh`` simuliert Aenderungen waehrend des Versands."""

    def __init__(self, *a, on_refresh=None, **kw):
        super().__init__(*a, **kw)
        self.open = False
        self.on_refresh = on_refresh
        self.refreshed = []

    async def execute(self, stmt, *args, **kwargs):
        self.open = True
        return await super().execute(stmt, *args, **kwargs)

    async def flush(self):
        self.open = True
        await super().flush()

    async def commit(self):
        self.open = False
        await super().commit()

    async def rollback(self):
        self.open = False
        await super().rollback()

    async def refresh(self, obj):
        self.open = True
        self.refreshed.append(type(obj).__name__)
        if self.on_refresh is not None:
            self.on_refresh(obj)


def test_test_endpoint_holds_no_transaction_during_send(transport, metrics_seen):
    """Regression: waehrend des HTTP-Versands (bis 30 s) darf die Request-Session keine Transaktion und damit
    keine Pool-Verbindung halten; die Testzeile ist vorher committet, das Ergebnis wird danach neu geladen."""
    user = make_user(role="user")
    session = TxSession(user_row=user, extra={Webhook: [_hook()]})
    seen = {}

    def handler(req):
        rows = [o for o in session.added if isinstance(o, WebhookDelivery)]
        seen.update(open=session.open, commits=session.commits, status=[r.status for r in rows],
                    running=set(webhooks_router._tests_running))
        return httpx.Response(200, text="ok")

    transport["handler"] = handler
    r = _session_client(session, user).post("/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 200, r.text
    assert seen["open"] is False and seen["commits"] >= 1 and seen["status"] == ["in_progress"]
    assert seen["running"] == {user.id}  # waehrend des Versands als laufend markiert
    assert session.refreshed == ["WebhookDelivery", "Webhook"]
    assert r.json()["delivery"]["status"] == "succeeded" and metrics_seen == ["succeeded"]
    assert webhooks_router._tests_running == set()


def test_test_endpoint_limits_concurrent_tests(transport):
    user = make_user(role="user")
    session = FakeSession(user_row=user, extra={Webhook: [_hook()]})
    c = _session_client(session, user)
    # je Benutzer hoechstens ein laufender Test
    webhooks_router._tests_running.add(user.id)
    r = c.post("/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 429 and r.json()["detail"] == webhooks_router.TEST_BUSY_USER
    # global hoechstens TEST_MAX_CONCURRENT
    webhooks_router._tests_running.clear()
    webhooks_router._tests_running.update(range(10_000, 10_000 + webhooks_router.TEST_MAX_CONCURRENT))
    r = c.post("/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 429 and r.json()["detail"] == webhooks_router.TEST_BUSY_GLOBAL
    assert transport["requests"] == [] and webhooks_router._last_test == {}  # abgelehnt: kein Cooldown verbraucht
    assert not [o for o in session.added if isinstance(o, WebhookDelivery)]
    # ein Platz frei -> Test laeuft, danach ist der Benutzer wieder ausgetragen
    webhooks_router._tests_running.discard(10_000)
    r = c.post("/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 200, r.text
    assert user.id not in webhooks_router._tests_running and len(transport["requests"]) == 1


def test_test_endpoint_releases_slot_on_error(transport, monkeypatch):
    user = make_user(role="user")

    async def boom(**kw):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(sender, "send_delivery", boom)
    r = _session_client(FakeSession(user_row=user, extra={Webhook: [_hook()]}), user).post(
        "/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 500
    assert webhooks_router._tests_running == set()


def test_test_endpoint_webhook_deleted_during_send(transport, metrics_seen):
    from sqlalchemy.exc import InvalidRequestError

    def gone(obj):
        raise InvalidRequestError("Could not refresh instance")

    user = make_user(role="user")
    session = TxSession(user_row=user, extra={Webhook: [_hook()]}, on_refresh=gone)
    r = _session_client(session, user).post("/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 404 and r.json()["detail"] == "Webhook nicht gefunden"
    assert len(transport["requests"]) == 1 and metrics_seen == []
    assert webhooks_router._tests_running == set()


def test_test_endpoint_cancelled_during_send_keeps_status(transport, metrics_seen):
    hook = _hook()

    def revoke(obj):
        if isinstance(obj, WebhookDelivery):
            obj.status, obj.last_error_code, obj.last_error = "cancelled", "access_revoked", "Zugang widerrufen"
        else:
            obj.is_active = False

    user = make_user(role="user")
    session = TxSession(user_row=user, extra={Webhook: [hook]}, on_refresh=revoke)
    r = _session_client(session, user).post("/api/v1/auth/me/webhooks/5/test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is False and body["delivery"]["status"] == "cancelled"
    assert body["delivery"]["last_status_code"] == 200 and body["delivery"]["can_retry"] is False
    assert hook.last_success_at is None and metrics_seen == ["cancelled"]


def test_test_endpoint_unknown_webhook_is_404():
    user = make_user(role="user")
    r = _session_client(FakeSession(user_row=user), user).post("/api/v1/auth/me/webhooks/99/test")
    assert r.status_code == 404 and r.json()["detail"] == "Webhook nicht gefunden"


NEW_ROUTES = [
    ("POST", "/api/v1/auth/me/webhooks/5/test"),
    ("GET", "/api/v1/auth/me/webhooks/5/deliveries"),
    ("GET", "/api/v1/auth/me/webhooks/5/deliveries/1"),
    ("POST", "/api/v1/auth/me/webhooks/5/deliveries/1/retry"),
]


@pytest.mark.parametrize("method,path", NEW_ROUTES)
def test_new_routes_reject_panel_tokens(method, path):
    session = FakeSession(token_row=make_token(scope_zones=None, allow_admin=True), user_row=make_user(),
                          extra={Webhook: [_hook()]})
    c = TestClient(build_app(session, webhooks_router), raise_server_exceptions=False)
    r = c.request(method, path, headers=bearer())
    assert r.status_code == 403 and r.json()["detail"] == SESSION_REQUIRED_DETAIL
    assert session.added == []


def test_create_and_update_validate_input(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_URLS", False)
    threads = []

    def fake_resolve(url):
        threads.append(threading.get_ident())
        raise wsvc.WebhookTargetBlocked(wsvc.PRIVATE_WEBHOOK_ERROR)

    monkeypatch.setattr(wsvc, "_resolve_checked", fake_resolve)
    user = make_user(role="user")
    session = FakeSession(user_row=user, extra={Webhook: [_hook()]})
    c = _session_client(session, user)
    r = c.post("/api/v1/auth/me/webhooks", json={"name": "h", "url": "https://intern.example/x", "events": ["foo"]})
    assert r.status_code == 400 and r.json()["detail"] == "Unbekanntes Ereignis: foo"
    r = c.post("/api/v1/auth/me/webhooks", json={"name": "h", "url": "https://intern.example/x"})
    assert r.status_code == 400 and r.json()["detail"] == wsvc.PRIVATE_WEBHOOK_ERROR
    assert threads and threading.get_ident() not in threads  # Pruefung im Thread
    r = c.post("/api/v1/auth/me/webhooks", json={"name": "h", "url": "https://x.example/a b"})
    assert r.status_code == 422
    r = c.put("/api/v1/auth/me/webhooks/5", json={"url": "ftp://x.example/abc"})
    assert r.status_code == 400 and "http://" in r.json()["detail"]
    r = c.put("/api/v1/auth/me/webhooks/5", json={"scope": "alle"})
    assert r.status_code == 422
    assert not [o for o in session.added if isinstance(o, Webhook)]


def test_deliveries_query_validation():
    user = make_user(role="user")
    c = _session_client(FakeSession(user_row=user, extra={Webhook: [_hook()]}), user)
    base = "/api/v1/auth/me/webhooks/5/deliveries"
    assert c.get(base, params={"limit": 101}).status_code == 422
    assert c.get(base, params={"status": "unbekannt"}).status_code == 422
    assert c.get(base, params={"event": "Record.Created"}).status_code == 422
    r = c.get(base, params={"status": "cancelled", "event": "record.created", "offset": 25})
    assert r.status_code == 200 and r.json() == {"total": 0, "limit": 25, "offset": 25, "deliveries": []}


def test_delivery_out_next_attempt_only_when_pending():
    now = utcnow()
    for status, expect in (("queued", True), ("failed", True), ("in_progress", False), ("succeeded", False),
                           ("dead", False), ("cancelled", False)):
        out = webhooks_router.delivery_out(_delivery(status=status, next_attempt_at=now), webhook_active=True)
        assert (out["next_attempt_at"] is not None) is expect, status
        assert out["can_retry"] is (status in ("failed", "dead", "succeeded", "cancelled")), status
    assert webhooks_router.delivery_out(_delivery(status="dead"), webhook_active=False)["can_retry"] is False


def test_worker_never_claims_terminal_statuses():
    assert "cancelled" in worker.TERMINAL_STATUSES and "cancelled" not in worker.PENDING_STATUSES
    assert set(worker.PENDING_STATUSES) == {"queued", "failed"}


def test_worker_state_shape_without_worker():
    st = worker.worker_state()
    assert set(st) == {"enabled", "running", "last_loop_at", "last_error_at"} and st["running"] is False


def test_compute_next_delay_is_jittered_not_constant():
    values = {round(worker.compute_next_delay(3), 3) for _ in range(20)}
    assert len(values) > 1 and all(240 <= v <= 264 for v in values)


def test_apply_result_truncates_long_texts():
    d = _delivery()
    res = sender.SendResult(ok=False, status_code=None, error_code="x" * 40, error="e" * 900, excerpt="a" * 2000,
                            duration_ms=1)
    worker.apply_result(d, None, res, datetime(2026, 1, 1))
    assert len(d.last_error) == 512 and len(d.last_error_code) == 32 and len(d.last_response_excerpt) == 1024
