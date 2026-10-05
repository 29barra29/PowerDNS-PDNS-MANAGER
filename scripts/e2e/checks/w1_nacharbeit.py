"""E2E-Checks fuer WS-W1-NACHARBEIT (Nacharbeiten aus dem Review der Welle 1).

L10: Ein Rollback, dessen RRsets schon auf dem Vorher-Stand sind, ist kein Konflikt. Gegen echte PowerDNS-Server:
- Record anlegen und aendern (Verlaufseintrag U), danach von Hand auf den alten Wert zuruecksetzen.
- Vorschau fuer U: ``has_conflicts`` false, Plan-Eintrag ``noop`` true / ``conflict`` false.
- Rollback ohne ``force``: 200 mit ``details.noop`` (bisher 409), kein neuer ``RECORD_ROLLBACK``-Eintrag, kein Schreiben.
Neuinstallation und Upgrade pruefen dasselbe (eigene Zone; keine neue API).
"""
from __future__ import annotations

from urllib.parse import quote


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _make_zone(ctx, prefix: str) -> str:
    zone = ctx.unique_zone(prefix)

    def _drop():
        ctx.api("DELETE", f"zones/ns1/{_z(zone)}")
        for srv in ctx.pdns.servers:
            if ctx.pdns.zone(srv, zone) is not None:
                ctx.pdns.delete_zone(srv, zone)

    ctx.cleanup(_drop, f"Zone {zone} entfernen")
    ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ["ns1.e2e.test."]}, expect=200)
    return zone


def _noop_rollback(ctx, prefix: str) -> None:
    zone = _make_zone(ctx, prefix)
    www = f"www.{zone}"

    with ctx.step("Record anlegen, aendern und von Hand zuruecksetzen"):
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200,
                json={"name": www, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.20"}]})
        ctx.api("PUT", f"records/ns1/{_z(zone)}", expect=200, json={
            "name": www, "type": "A", "ttl": 300, "old_content": "192.0.2.20", "new_content": "192.0.2.21"})
        entries = ctx.api("GET", f"zones/ns1/{_z(zone)}/history?type=A", expect=200).json()["entries"]
        update = next(e for e in entries if e["action"] == "UPDATE" and e["resource_type"] == "record")
        ctx.api("PUT", f"records/ns1/{_z(zone)}", expect=200, json={
            "name": www, "type": "A", "ttl": 300, "old_content": "192.0.2.21", "new_content": "192.0.2.20"})
        ctx.eq(ctx.pdns.contents("ns1", zone, www, "A"), ["192.0.2.20"], "ns1 wieder auf dem Vorher-Stand")

    with ctx.step("Vorschau: kein Konflikt, Eintrag ist Noop"):
        prev = ctx.api("GET", f"zones/ns1/{_z(zone)}/history/{update['id']}/rollback-preview", expect=200).json()
        ctx.check(prev["rollbackable"] is True, f"Vorschau nicht ruecksetzbar: {prev}")
        ctx.eq(prev["has_conflicts"], False, "has_conflicts")
        ctx.eq([(i["noop"], i["conflict"]) for i in prev["plan"]], [(True, False)], "Plan noop/conflict")

    with ctx.step("Rollback ohne force: 200 Noop statt 409"):
        before_total = ctx.api("GET", f"zones/ns1/{_z(zone)}/history", expect=200).json()["total"]
        res = ctx.api("POST", f"zones/ns1/{_z(zone)}/history/{update['id']}/rollback", json={"force": False},
                      expect=200).json()
        ctx.eq(res["details"].get("noop"), True, "details.noop")
        after = ctx.api("GET", f"zones/ns1/{_z(zone)}/history", expect=200).json()
        ctx.eq(after["total"], before_total, "kein neuer Verlaufseintrag")
        ctx.check(all(e["action"] != "RECORD_ROLLBACK" for e in after["entries"]), "RECORD_ROLLBACK geschrieben")
        ctx.eq(ctx.pdns.contents("ns1", zone, www, "A"), ["192.0.2.20"], "ns1 unveraendert")


def check_fresh(ctx) -> None:
    _noop_rollback(ctx, "w1n")


def check_upgrade(ctx) -> None:
    _noop_rollback(ctx, "w1nu")
