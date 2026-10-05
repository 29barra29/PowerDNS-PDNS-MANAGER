"""E2E-Checks fuer WS-F6-BE (Webhook-Zustellung: Worker, Backoff, Protokoll, Test, Retry).

Neuinstallation (``check_fresh``) gegen den E2E-Webhook-Empfaenger:
- Liste meldet einen laufenden Worker; Panel-Tokens kommen nicht an Protokoll/Test (Session-Pflicht).
- Test-Zustellung (``webhook.test``) kommt synchron an, Signatur ueber die empfangenen Bytes passt.
- Echte Ereignisse (``zone.created``, ``record.created``) werden vom Hintergrund-Worker zugestellt: Body v2,
  Header (Event/Delivery/Attempt), HMAC mit dem Secret, Protokoll ``succeeded``.
- **Retry nach 500:** Empfaenger antwortet beim ersten Versuch je Zustellung mit 500 -> Status ``failed`` mit
  geplantem Backoff (~60 s); faellig gemacht holt der Worker sie selbst erneut (Versuch 2, gleicher Body, gleiche
  Delivery-ID und Signatur) -> ``succeeded``. Eine zweite fehlgeschlagene Zustellung wird ueber
  ``POST …/retry`` sofort erneut gesendet.
- 410 vom Empfaenger -> sofort ``dead`` (``gone``), kein weiterer Versuch.
Upgrade (``check_upgrade``): Alt-Webhook ``seed-admin`` (2.4.1, Klartext -> verschluesselt) bekommt ein neues
``record.created`` zugestellt, signiert mit dem alten Secret. ``check_upgrade_restart``: die zugestellte Zeile
bleibt ``succeeded`` und wird nach dem Neustart nicht erneut gesendet.
"""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote

DELIVERY_TIMEOUT = 30  # Worker pollt alle 5 s, der Weckruf nach dem Commit startet sofort


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _hdr(d: dict, name: str):
    for k, v in (d.get("headers") or {}).items():
        if k.lower() == name.lower():
            return v
    return None


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _create_hook(ctx, name: str, url: str, events) -> tuple[int, str]:
    created = ctx.admin_session.post("auth/me/webhooks", json={"name": name, "url": url, "events": events},
                                     expect=201).json()
    hook_id, secret = created["webhook"]["id"], created["secret"]
    ctx.cleanup(lambda: ctx.admin_session.delete(f"auth/me/webhooks/{hook_id}"), f"Webhook {name} loeschen")
    return hook_id, secret


def _verify_signature(ctx, d: dict, secret: str, what: str) -> dict:
    raw = ctx.receiver.raw_body(d)
    sig = _hdr(d, "X-DNS-Manager-Signature")
    ctx.eq(sig, "sha256=" + ctx.receiver.hmac_sha256(secret, raw), f"{what}: HMAC-Signatur ueber die empfangenen Bytes")
    body = json.loads(raw.decode("ascii"))
    ctx.eq(body.get("v"), 2, f"{what}: Payload-Version")
    ctx.eq(_hdr(d, "X-DNS-Manager-Event"), body.get("event"), f"{what}: Header X-DNS-Manager-Event")
    ctx.eq(_hdr(d, "X-DNS-Manager-Delivery"), body.get("delivery_id"), f"{what}: Header X-DNS-Manager-Delivery")
    ctx.check(str(_hdr(d, "User-Agent") or "").startswith("PDNS-Manager-Webhook/"), f"{what}: User-Agent")
    return body


def _deliveries(ctx, hook_id: int, **params) -> list[dict]:
    q = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    path = f"auth/me/webhooks/{hook_id}/deliveries" + (f"?{q}" if q else "")
    return ctx.admin_session.get(path, expect=200).json()["deliveries"]


def _wait_status(ctx, hook_id: int, event: str, status: str, timeout: float = DELIVERY_TIMEOUT) -> dict:
    def find():
        for d in _deliveries(ctx, hook_id, event=event):
            if d["status"] == status:
                return d
        return None

    return ctx.wait_until(find, timeout=timeout, msg=f"{event} wird nicht {status}")


def _drop_zone(ctx, zone: str) -> None:
    ctx.api("DELETE", f"zones/ns1/{_z(zone)}")
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.pdns.delete_zone(srv, zone)


def check_fresh(ctx) -> None:
    with ctx.step("Worker laeuft, Session-Pflicht fuer Protokoll/Test"):
        body = ctx.admin_session.get("auth/me/webhooks", expect=200).json()
        ctx.check(body.get("worker_enabled") is True, f"worker_enabled: {body.get('worker_enabled')}")
        ctx.check(body.get("worker_running") is True, "Webhook-Worker laeuft nicht")
        ctx.eq(body.get("max_attempts"), 6, "max_attempts")
        ctx.check("webhook.test" not in body.get("available_events", []), "webhook.test ist abonnierbar")

    ok_name = ctx.unique("f6-ok")
    with ctx.step("Webhook anlegen und Test senden"):
        ok_id, ok_secret = _create_hook(ctx, ok_name, ctx.receiver.url(ok_name), ["zone", "record"])
        ctx.api("GET", f"auth/me/webhooks/{ok_id}/deliveries", expect=403)  # Panel-Token
        ctx.api("POST", f"auth/me/webhooks/{ok_id}/test", json={}, expect=403)
        res = ctx.admin_session.post(f"auth/me/webhooks/{ok_id}/test", json={}, expect=200).json()
        ctx.check(res.get("success") is True, f"Test-Zustellung fehlgeschlagen: {res}")
        ctx.eq(res["delivery"]["status"], "succeeded", "Status der Test-Zustellung")
        got = ctx.receiver.wait_for(ok_name, count=1, timeout=5,
                                    predicate=lambda d: _hdr(d, "X-DNS-Manager-Event") == "webhook.test")
        body = _verify_signature(ctx, got[0], ok_secret, "webhook.test")
        ctx.eq(body["data"].get("webhook_id"), ok_id, "webhook.test: data.webhook_id")
        ctx.eq(_hdr(got[0], "X-DNS-Manager-Attempt"), "1", "webhook.test: Versuch")
        r = ctx.admin_session.post(f"auth/me/webhooks/{ok_id}/test", json={})
        ctx.eq(r.status, 429, "zweiter Test innerhalb von 10 s")

    zone = ctx.unique_zone("f6")
    ctx.cleanup(lambda: _drop_zone(ctx, zone), "Zone entfernen")
    retry_name = ctx.unique("f6-retry")
    gone_name = ctx.unique("f6-gone")
    with ctx.step("Retry- und 410-Webhook anlegen"):
        retry_id, retry_secret = _create_hook(ctx, retry_name, ctx.receiver.url(retry_name, fail=1), ["record"])
        gone_id, _ = _create_hook(ctx, gone_name, ctx.receiver.url(gone_name, status=410), ["zone.created"])

    with ctx.step("zone.created und record.created werden vom Worker zugestellt"):
        ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ["ns1.e2e.test."]}, expect=200)
        host = f"www.{zone}"
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200,
                json={"name": host, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.61"}]})
        for event in ("zone.created", "record.created"):
            got = ctx.receiver.wait_for(ok_name, count=1, timeout=DELIVERY_TIMEOUT,
                                        predicate=lambda d, e=event: _hdr(d, "X-DNS-Manager-Event") == e)
            body = _verify_signature(ctx, got[0], ok_secret, event)
            ctx.eq(body.get("zone"), zone, f"{event}: Zone im Body")
            ctx.eq(_hdr(got[0], "X-DNS-Manager-Attempt"), "1", f"{event}: erster Versuch")
            row = _wait_status(ctx, ok_id, event, "succeeded")
            ctx.eq(row["delivery_id"], body["delivery_id"], f"{event}: Delivery-ID im Protokoll")
            ctx.check(row["delivered_at"] and row["last_status_code"] == 200, f"{event}: Protokoll {row}")

    with ctx.step("410 -> sofort dead (gone)"):
        row = _wait_status(ctx, gone_id, "zone.created", "dead")
        ctx.eq(row["last_error_code"], "gone", "Fehlercode bei 410")
        ctx.eq(row["attempts"], 1, "nur ein Versuch bei 410")

    with ctx.step("Retry nach 500: Backoff geplant, Worker sendet erneut"):
        first = _wait_status(ctx, retry_id, "record.created", "failed")
        ctx.eq(first["last_status_code"], 500, "erster Versuch: HTTP 500")
        ctx.eq(first["attempts"], 1, "erster Versuch gezaehlt")
        delay = (_ts(first["next_attempt_at"]) - _ts(first["last_attempt_at"])).total_seconds()
        ctx.check(55 <= delay <= 70, f"Backoff nach Versuch 1: {delay:.1f} s statt ~60 s")
        # Backoff abkuerzen (statt 60 s zu warten): Zeile direkt faellig machen, der Worker holt sie selbst
        ctx.db("UPDATE webhook_deliveries SET next_attempt_at = UTC_TIMESTAMP() - INTERVAL 1 SECOND WHERE id = %s",
               (first["id"],))
        row = _wait_status(ctx, retry_id, "record.created", "succeeded")
        ctx.eq(row["attempts"], 2, "zweiter Versuch erfolgreich")
        recv = [d for d in ctx.receiver.deliveries(retry_name) if _hdr(d, "X-DNS-Manager-Delivery") == row["delivery_id"]]
        ctx.eq([d["response_status"] for d in recv], [500, 200], "Antworten des Empfaengers")
        ctx.eq([_hdr(d, "X-DNS-Manager-Attempt") for d in recv], ["1", "2"], "Versuchszaehler im Header")
        ctx.eq(ctx.receiver.raw_body(recv[0]), ctx.receiver.raw_body(recv[1]), "Body beim Retry byte-identisch")
        ctx.eq(_hdr(recv[0], "X-DNS-Manager-Signature"), _hdr(recv[1], "X-DNS-Manager-Signature"), "gleiche Signatur")
        _verify_signature(ctx, recv[1], retry_secret, "record.created (Retry)")

    with ctx.step("Manueller Retry einer fehlgeschlagenen Zustellung"):
        ctx.api("DELETE", f"records/ns1/{_z(zone)}/delete", json={"name": f"www.{zone}", "type": "A"}, expect=200)
        failed = _wait_status(ctx, retry_id, "record.deleted", "failed")
        ctx.check(failed["can_retry"] is True, f"can_retry fehlt: {failed}")
        res = ctx.admin_session.post(f"auth/me/webhooks/{retry_id}/deliveries/{failed['id']}/retry", json={},
                                     expect=200).json()
        ctx.eq(res.get("message"), "Zustellung neu eingeplant", "Retry-Antwort")
        row = _wait_status(ctx, retry_id, "record.deleted", "succeeded")
        ctx.eq(row["attempts"], 2, "manueller Retry: zweiter Versuch")
        detail = ctx.admin_session.get(f"auth/me/webhooks/{retry_id}/deliveries/{row['id']}", expect=200).json()
        ctx.eq(detail["body"].get("event"), "record.deleted", "Protokoll-Detail: Body")
        audit = ctx.db("SELECT action FROM audit_logs WHERE action = 'WEBHOOK_DELIVERY_RETRY' AND resource_name = %s",
                       (retry_name,))
        ctx.check(len(audit) == 1, f"Audit WEBHOOK_DELIVERY_RETRY fehlt: {audit}")

    with ctx.step("Webhook-Zaehler und Statistik"):
        hooks = {h["id"]: h for h in ctx.admin_session.get("auth/me/webhooks", expect=200).json()["webhooks"]}
        ok = hooks[ok_id]
        ctx.check(ok["last_success_at"] and ok["consecutive_failures"] == 0, f"Zaehler ok-Webhook: {ok}")
        ctx.check(ok["stats"]["succeeded"] >= 3, f"Statistik ok-Webhook: {ok['stats']}")
        ctx.check(hooks[gone_id]["stats"]["dead"] >= 1, f"Statistik 410-Webhook: {hooks[gone_id]['stats']}")


def check_upgrade(ctx) -> None:
    hooks = {h["name"]: h for h in ctx.seed.get("webhooks", [])}
    zones = ctx.seed.get("zones", {})
    if "seed-admin" not in hooks or "alpha" not in zones:
        ctx.fail(f"Seed ohne Webhook seed-admin oder Zone alpha: {sorted(hooks)} / {sorted(zones)}")
    hook, zone = hooks["seed-admin"], zones["alpha"]

    with ctx.step("Worker laeuft nach dem Upgrade, Alt-Webhook lesbar"):
        body = ctx.admin_session.get("auth/me/webhooks", expect=200).json()
        ctx.check(body.get("worker_running") is True, "Webhook-Worker laeuft nicht")
        listed = {w["id"]: w for w in body["webhooks"]}
        ctx.check(hook["id"] in listed, f"Alt-Webhook fehlt: {sorted(listed)}")
        w = listed[hook["id"]]
        ctx.check(w["has_url"] and w["has_secret"] and w["url"] == hook["url"], f"Alt-Webhook nach Upgrade: {w}")

    host = f"{ctx.unique('f6')}.{zone}"
    ctx.cleanup(lambda: ctx.api("DELETE", f"records/ns1/{_z(zone)}/delete", json={"name": host, "type": "A"}),
                "Record entfernen")
    with ctx.step("Alt-Webhook bekommt record.created zugestellt (Alt-Secret)"):
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200,
                json={"name": host, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.62"}]})
        got = ctx.receiver.wait_for(hook["name"], count=1, timeout=DELIVERY_TIMEOUT,
                                    predicate=lambda d: host in (d.get("body_text") or ""))
        body = _verify_signature(ctx, got[0], hook["secret"], "record.created (Alt-Webhook)")
        ctx.eq(body.get("event"), "record.created", "Ereignis")
        row = _wait_status(ctx, hook["id"], "record.created", "succeeded")
        ctx.state["f6_delivery"] = {"id": row["id"], "delivery_id": body["delivery_id"], "path": hook["name"]}


def check_upgrade_restart(ctx) -> None:
    info = ctx.state.get("f6_delivery")
    if not info:
        ctx.skip("keine Zustellung aus check_upgrade (Modul dort fehlgeschlagen?)")
    with ctx.step("Zugestellte Zeile bleibt nach dem Neustart succeeded, kein Doppelversand"):
        rows = ctx.db("SELECT status, attempts FROM webhook_deliveries WHERE id = %s", (info["id"],))
        ctx.check(rows and rows[0]["status"] == "succeeded" and rows[0]["attempts"] == 1,
                  f"Zustellung nach dem Neustart: {rows}")
        body = ctx.admin_session.get("auth/me/webhooks", expect=200).json()
        ctx.check(body.get("worker_running") is True, "Webhook-Worker laeuft nach dem Neustart nicht")
        recv = [d for d in ctx.receiver.deliveries(info["path"])
                if _hdr(d, "X-DNS-Manager-Delivery") == info["delivery_id"]]
        ctx.eq(len(recv), 1, "Zustellungen derselben Delivery-ID beim Empfaenger")
