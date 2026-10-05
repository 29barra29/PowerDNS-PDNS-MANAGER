"""Outbox gegen MariaDB (``requires_db``, Fixture ``fresh_db``): SQL-Pfade von ``enqueue_event``/``select_recipients``.

Prueft, was die Fake-Session in ``test_webhook_events.py`` nicht kann: Join auf ``users``, Zonenrechte aus
``user_zone_access``, verschluesselte ``webhooks.secret``/``webhooks.url`` (transparent entschluesselt, unlesbar ->
``dead``), Commit/Rollback zusammen mit dem Audit-Eintrag und den Weckruf nach dem Commit. Die vollstaendigen
Outbox-DB-Tests (Claim, Retry, Housekeeping) liefert WS-F6-BE (``test_webhook_outbox_db.py``).
"""
import asyncio
import json
import os
import uuid

from sqlalchemy import select, text

from dbutil import requires_db

pytestmark = requires_db


def _run(coro):
    return asyncio.run(coro)


def _keys():
    from app.core import secrets as secret_store

    secret_store.configure_for_tests(os.environ["SECRET_ENCRYPTION_KEY"])


async def _seed(prefix: str, zone: str) -> dict:
    from app.core.auth import hash_password
    from app.core.database import async_session
    from app.models.models import User, UserZoneAccess, Webhook

    ids: dict = {}
    async with async_session() as s:
        def user(key, role="user", active=True):
            u = User(username=f"{prefix}_{key}", hashed_password=hash_password("x-passwort-1"), role=role,
                     is_active=active, display_name=key)
            s.add(u)
            return u

        users = {
            "actor": user("actor"), "admin": user("admin", "admin"), "zoneuser": user("zoneuser"),
            "stranger": user("stranger"), "inactive": user("inactive", active=False),
        }
        await s.flush()
        s.add(UserZoneAccess(user_id=users["zoneuser"].id, zone_name=zone, permission="read"))
        s.add(UserZoneAccess(user_id=users["stranger"].id, zone_name="anders." + zone, permission="manage"))

        def hook(key, owner, scope="own", events=("*",), active=True):
            w = Webhook(user_id=users[owner].id, name=f"{prefix}-{key}", url=f"https://hooks.example/{key}",
                        secret=f"secret-{key}", events=list(events), is_active=active, scope=scope)
            s.add(w)
            return w

        hooks = {
            "own": hook("own", "actor"),
            "own_filtered": hook("own_filtered", "actor", events=("zone",)),
            "own_inactive": hook("own_inactive", "actor", active=False),
            "admin_zones": hook("admin_zones", "admin", scope="zones"),
            "zoneuser_zones": hook("zoneuser_zones", "zoneuser", scope="zones"),
            "stranger_zones": hook("stranger_zones", "stranger", scope="zones"),
            "stranger_own": hook("stranger_own", "stranger"),
            "inactive_owner": hook("inactive_owner", "inactive", scope="zones"),
        }
        await s.commit()
        ids["users"] = {k: u.id for k, u in users.items()}
        ids["hooks"] = {k: w.id for k, w in hooks.items()}
    return ids


def test_enqueue_commits_with_audit_and_selects_recipients(fresh_db, monkeypatch):
    from app.core.database import async_session
    from app.models.models import User, Webhook, WebhookDelivery
    from app.services import webhook_service, webhook_worker
    from app.services.audit import write_audit
    from app.services.webhook_outbox import enqueue_event

    _keys()
    prefix = "wo_" + uuid.uuid4().hex[:6]
    zone = f"{prefix}.example."
    ids = _run(_seed(prefix, zone))
    woke = []
    monkeypatch.setattr(webhook_worker, "notify_worker", lambda: woke.append(1))

    async def scenario():
        async with async_session() as s:
            actor = await s.get(User, ids["users"]["actor"])
            audit = await write_audit(s, "CREATE", "record", f"www.{zone}", user_id=actor.id,
                                      details={"zone": zone}, zone_name=zone)
            n = await enqueue_event(s, "record.created", actor=actor, zone=zone.upper(), server="ns1",
                                    data={"name": f"www.{zone}"}, audit_log_id=audit.id)
            assert woke == []
            await s.commit()
            assert woke == [1]
            rows = (await s.execute(select(WebhookDelivery).where(WebhookDelivery.audit_log_id == audit.id)
                                    .order_by(WebhookDelivery.webhook_id))).scalars().all()
            hooks = {w.id: w for w in (await s.execute(select(Webhook))).scalars().all()}
            return n, audit.id, rows, hooks

    n, audit_id, rows, hooks = _run(scenario())
    h = ids["hooks"]
    mine = set(h.values())
    expected = sorted([h["own"], h["admin_zones"], h["zoneuser_zones"]])
    # Admin-Webhooks mit scope=zones aus anderen Tests derselben DB empfangen ebenfalls (alle Zonen)
    assert n == len(rows) and sorted(r.webhook_id for r in rows if r.webhook_id in mine) == expected
    assert len({r.event_id for r in rows}) == 1
    for r in rows:
        assert r.status == "queued" and r.zone_name == zone and r.event == "record.created"
        assert r.user_id == hooks[r.webhook_id].user_id
        secret = hooks[r.webhook_id].secret  # transparent entschluesselt
        assert secret.startswith("secret-")
        assert r.signature == webhook_service.sign(secret, r.body.encode("ascii"))
        body = json.loads(r.body)
        assert body["v"] == 2 and body["audit_log_id"] == audit_id and body["zone"] == zone
    # Secrets und URLs liegen verschluesselt in der DB
    async def raw():
        async with async_session() as s:
            return (await s.execute(text("SELECT secret, url FROM webhooks WHERE id = :i"), {"i": h["own"]})).one()

    secret_raw, url_raw = _run(raw())
    assert secret_raw.startswith("enc:v1:") and url_raw.startswith("enc:v1:")


def test_rollback_discards_deliveries_and_unreadable_url_is_dead(fresh_db, monkeypatch):
    from app.core.database import async_session
    from app.models.models import User, WebhookDelivery
    from app.services import webhook_worker
    from app.services.webhook_outbox import enqueue_event

    _keys()
    prefix = "wr_" + uuid.uuid4().hex[:6]
    zone = f"{prefix}.example."
    ids = _run(_seed(prefix, zone))
    woke = []
    monkeypatch.setattr(webhook_worker, "notify_worker", lambda: woke.append(1))

    async def rolled_back():
        async with async_session() as s:
            actor = await s.get(User, ids["users"]["actor"])
            n = await enqueue_event(s, "zone.updated", actor=actor, zone=zone, data={})
            await s.rollback()
            return n

    assert _run(rolled_back()) >= 4  # own, own_filtered ("zone"), admin_zones, zoneuser_zones (+ fremde Admins)
    assert woke == []

    async def count(event):
        async with async_session() as s:
            return (await s.execute(text(
                "SELECT COUNT(*) FROM webhook_deliveries WHERE event = :e AND zone_name = :z"),
                {"e": event, "z": zone})).scalar()

    assert _run(count("zone.updated")) == 0

    async def break_url():
        async with async_session() as s:
            await s.execute(text("UPDATE webhooks SET url = 'enc:v1:kaputt' WHERE id = :i"), {"i": ids["hooks"]["own"]})
            await s.commit()

    _run(break_url())

    async def unreadable():
        async with async_session() as s:
            actor = await s.get(User, ids["users"]["actor"])
            await enqueue_event(s, "zone.deleted", actor=actor, zone=zone, data={})
            await s.commit()
            return (await s.execute(select(WebhookDelivery).where(
                WebhookDelivery.webhook_id == ids["hooks"]["own"], WebhookDelivery.event == "zone.deleted"))).scalar_one()

    row = _run(unreadable())
    assert row.status == "dead" and row.last_error_code == "url_unreadable"


def test_foreign_action_without_zone_only_reaches_own_hooks(fresh_db):
    from app.core.database import async_session
    from app.models.models import User, WebhookDelivery
    from app.services.webhook_outbox import enqueue_event

    _keys()
    prefix = "wn_" + uuid.uuid4().hex[:6]
    ids = _run(_seed(prefix, f"{prefix}.example."))

    async def scenario():
        async with async_session() as s:
            actor = await s.get(User, ids["users"]["stranger"])
            n = await enqueue_event(s, "record.bulk", actor=actor, zone=None, data={})
            await s.commit()
            rows = (await s.execute(select(WebhookDelivery.webhook_id).where(WebhookDelivery.event == "record.bulk",
                                    WebhookDelivery.user_id == actor.id))).scalars().all()
            return n, rows

    n, rows = _run(scenario())
    assert n == 2 and sorted(rows) == sorted([ids["hooks"]["stranger_zones"], ids["hooks"]["stranger_own"]])
