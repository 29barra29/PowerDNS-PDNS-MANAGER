"""E2E-Checks fuer W0-INT-BE2b (main.py-Umbau, Webhook-Outbox-Kern, /health, /metrics).

Neuinstallation:
- ``/health`` von aussen nur mit ``status``/``database``/``servers`` (Zusatzfelder nur fuer Loopback-Peers).
- ``/metrics`` liefert 404, solange Prometheus-Metriken nicht eingeschaltet sind; ``/api/v1/metrics`` nur fuer
  effektive Admins.
- Outbox: Zone und Record anlegen -> je eine Zeile ``zone.created``/``record.created`` in ``webhook_deliveries``,
  Body v2, Signatur passt zum Secret, ``audit_log_id`` zeigt auf den Audit-Eintrag. Die Zustellung selbst
  baut WS-F6-BE (dort ``checks/f6.py``); hier sind ``queued`` und alle spaeteren Zustaende erlaubt.
Upgrade: dieselben Pruefungen mit den Altdaten aus 2.4.1 (Alt-Webhook ``seed-admin`` mit Klartext-Secret und
-URL – nach dem Upgrade verschluesselt, Signatur weiterhin mit dem alten Secret).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import quote

PUBLIC_HEALTH_KEYS = {"status", "database", "servers"}
LOCAL_HEALTH_KEYS = {"secrets", "migration_errors", "schema", "background", "servers_not_loaded"}
DELIVERY_STATES = {"queued", "in_progress", "succeeded", "failed", "dead"}


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _sig(secret: str, body: str) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()


def _check_health_and_metrics(ctx) -> None:
    with ctx.step("/health von aussen: nur status/database/servers"):
        health = ctx.api("GET", "/health", token=None, expect=200).json()
        ctx.check(set(health) <= PUBLIC_HEALTH_KEYS, f"/health verraet Zusatzfelder: {sorted(health)}")
        ctx.check(not (set(health) & LOCAL_HEALTH_KEYS), f"/health mit Loopback-Feldern: {sorted(health)}")
        ctx.check(health.get("status") in ("healthy", "degraded"), f"/health-Status: {health}")

    with ctx.step("/metrics ist ohne Aktivierung 404 (auch mit Token)"):
        r = ctx.api("GET", "/metrics", token=None, expect=404)
        ctx.eq(r.json(), {"error": "Not found"}, "/metrics-Antwort")
        ctx.api("GET", "/metrics", token="irgendein-token-mit-genug-zeichen-123", expect=404)
        ctx.check("text/html" not in r.headers.get("content-type", ""), "/metrics liefert HTML (SPA-Catch-all?)")

    with ctx.step("/api/v1/metrics nur fuer Admins"):
        data = ctx.api("GET", "metrics", expect=200).json()
        ctx.check(isinstance(data.get("uptime_seconds"), int), f"/api/v1/metrics: {data}")
        if ctx.user_token:
            ctx.api("GET", "metrics", token=ctx.user_token, expect=403)


def _deliveries(ctx, webhook_id: int, zone: str) -> list[dict]:
    return ctx.db(
        "SELECT id, event, status, signature, body, zone_name, audit_log_id, last_error_code "
        "FROM webhook_deliveries WHERE webhook_id = %s AND zone_name = %s ORDER BY id",
        (webhook_id, zone),
    )


def _check_delivery(ctx, row: dict, secret: str, zone: str, event: str) -> dict:
    ctx.check(row["status"] in DELIVERY_STATES, f"{event}: unbekannter Status {row['status']}")
    ctx.check(row["status"] != "dead", f"{event}: Zustellung tot ({row.get('last_error_code')})")
    ctx.eq(row["signature"], _sig(secret, row["body"]), f"{event}: Signatur ueber den gespeicherten Body")
    body = json.loads(row["body"])
    ctx.eq(body.get("v"), 2, f"{event}: Payload-Version")
    ctx.eq(body.get("event"), event, f"{event}: Ereignisname im Body")
    ctx.eq(body.get("zone"), zone, f"{event}: Zone im Body")
    ctx.check(isinstance(body.get("actor"), dict) and body["actor"].get("user_id"), f"{event}: actor fehlt: {body}")
    return body


def check_fresh(ctx) -> None:
    _check_health_and_metrics(ctx)

    zone = ctx.unique_zone("be2b")
    name = ctx.unique("be2b-hook")
    with ctx.step("Webhook (Session) anlegen"):
        created = ctx.admin_session.post("auth/me/webhooks", json={
            "name": name, "url": ctx.receiver.url("be2b"), "events": ["*"]}, expect=201).json()
        hook_id, secret = created["webhook"]["id"], created["secret"]
        ctx.cleanup(lambda: ctx.admin_session.delete(f"auth/me/webhooks/{hook_id}"), "Webhook loeschen")
        raw = ctx.db("SELECT secret, url FROM webhooks WHERE id = %s", (hook_id,))[0]
        ctx.check(str(raw["secret"]).startswith("enc:v1:"), "Webhook-Secret liegt unverschluesselt in der DB")
        ctx.check(str(raw["url"]).startswith("enc:v1:"), "Webhook-URL liegt unverschluesselt in der DB")

    def _drop_zone():
        ctx.api("DELETE", f"zones/ns1/{_z(zone)}")
        for srv in ctx.pdns.servers:
            if ctx.pdns.zone(srv, zone) is not None:
                ctx.pdns.delete_zone(srv, zone)

    ctx.cleanup(_drop_zone, "Zone entfernen")
    with ctx.step("Zone und Record anlegen -> Outbox-Zeilen"):
        ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ["ns1.e2e.test."]}, expect=200)
        ctx.api("POST", f"records/ns1/{_z(zone)}", json={
            "name": f"www.{zone}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.10"}]}, expect=200)
        rows = _deliveries(ctx, hook_id, zone)
        events = [r["event"] for r in rows]
        ctx.check(events.count("zone.created") == 1 and events.count("record.created") == 1,
                  f"erwartet je ein zone.created/record.created, gefunden: {events}")
        for row in rows:
            _check_delivery(ctx, row, secret, zone, row["event"])
        rec = next(r for r in rows if r["event"] == "record.created")
        ctx.check(rec["audit_log_id"], "record.created ohne audit_log_id")
        audit = ctx.db("SELECT action, zone_name FROM audit_logs WHERE id = %s", (rec["audit_log_id"],))
        ctx.check(audit and audit[0]["action"] == "CREATE" and audit[0]["zone_name"] == zone,
                  f"Audit zur Zustellung passt nicht: {audit}")

    with ctx.step("Zone loeschen -> zone.deleted"):
        ctx.api("DELETE", f"zones/ns1/{_z(zone)}", expect=200)
        events = [r["event"] for r in _deliveries(ctx, hook_id, zone)]
        ctx.check("zone.deleted" in events, f"zone.deleted fehlt: {events}")


def check_upgrade(ctx) -> None:
    _check_health_and_metrics(ctx)

    hooks = {h["name"]: h for h in ctx.seed.get("webhooks", [])}
    zones = ctx.seed.get("zones", {})
    if "seed-admin" not in hooks or "alpha" not in zones:
        ctx.fail(f"Seed ohne Webhook seed-admin oder Zone alpha: {sorted(hooks)} / {sorted(zones)}")
    hook = hooks["seed-admin"]
    zone = zones["alpha"]

    with ctx.step("Alt-Webhook nach dem Upgrade verschluesselt"):
        raw = ctx.db("SELECT secret, url FROM webhooks WHERE id = %s", (hook["id"],))[0]
        ctx.check(str(raw["secret"]).startswith("enc:v1:"), "Alt-Secret nicht verschluesselt")
        ctx.check(str(raw["url"]).startswith("enc:v1:"), "Alt-URL nicht verschluesselt")

    host = f"{ctx.unique('be2b')}.{zone}"
    ctx.cleanup(lambda: ctx.api("DELETE", f"records/ns1/{_z(zone)}/delete", json={"name": host, "type": "A"}),
                "Record entfernen")
    with ctx.step("Record per Alt-Admin-Token -> record.created fuer den Alt-Webhook"):
        before = {r["id"] for r in _deliveries(ctx, hook["id"], zone)}
        ctx.api("POST", f"records/ns1/{_z(zone)}", json={
            "name": host, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.77"}]}, expect=200)
        new = [r for r in _deliveries(ctx, hook["id"], zone) if r["id"] not in before]
        ctx.eq([r["event"] for r in new], ["record.created"], "neue Zustellungen des Alt-Webhooks")
        body = _check_delivery(ctx, new[0], hook["secret"], zone, "record.created")
        ctx.eq(body["data"].get("name"), host, "Record-Name im Payload")
        ctx.state["be2b_delivery_id"] = new[0]["id"]


def check_upgrade_restart(ctx) -> None:
    with ctx.step("/health nach dem zweiten Start"):
        health = ctx.api("GET", "/health", token=None, expect=200).json()
        ctx.check(set(health) <= PUBLIC_HEALTH_KEYS, f"/health verraet Zusatzfelder: {sorted(health)}")
    did = ctx.state.get("be2b_delivery_id")
    if did:
        with ctx.step("Outbox-Zeile ueberlebt den Neustart"):
            rows = ctx.db("SELECT status FROM webhook_deliveries WHERE id = %s", (did,))
            ctx.check(rows and rows[0]["status"] in DELIVERY_STATES, f"Zustellung {did} fehlt nach Neustart: {rows}")
