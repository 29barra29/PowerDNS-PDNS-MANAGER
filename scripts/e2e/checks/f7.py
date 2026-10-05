"""E2E-Checks fuer F7 (Record-Historie, Rollback, Audit-Log v2; Workstream WS-F7-BE).

Neuinstallation (gegen echte PowerDNS-Server ns1/ns2 mit getrennten Backends):
- Record anlegen/aendern -> Zonenverlauf zeigt v2-Eintraege mit Vorher/Nachher, ``zone_name`` gesetzt.
- Peer-Erhalt [D1]: ein Wert, der nur auf ns2 existiert, ueberlebt Anlegen/Aendern/Loeschen ueber ns1.
- Rollback-Vorschau, Rollback (Peers folgen), ``reverted_by_id``; Konflikt -> 409, mit ``force`` -> 200.
- Lese-Nutzer: Verlauf ja, Vorschau/Rollback 403; Nutzer ohne Zonenrecht 403.
- Admin-Audit-Log: Filter ``zone`` mit ``total``, Einzeleintrag, CSV mit den neuen Spalten;
  Aufbewahrung nur per Browser-Session (Token -> 403).
Upgrade (Altdaten 2.4.1):
- v1-Eintraege der Seed-Zone erscheinen im Verlauf (``version`` 1, nicht ruecksetzbar), ``zone_name`` nachgetragen.
- Neue Aenderung per Alt-Admin-Token ist v2 und laesst sich zuruecksetzen; ``alice`` (Lesen auf beta) darf nicht.
"""
from __future__ import annotations

import csv
import io
from urllib.parse import quote


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _hist(ctx, zone: str, server: str = "ns1", **kw):
    return ctx.api("GET", f"zones/{server}/{_z(zone)}/history", **kw)


def _entries(ctx, zone: str, **params) -> list[dict]:
    q = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    path = f"zones/ns1/{_z(zone)}/history" + (f"?{q}" if q else "")
    return ctx.api("GET", path, expect=200).json()["entries"]


def _values(rr: dict | None) -> list[str]:
    return sorted(r["content"] for r in (rr or {}).get("records", []))


def _make_zone(ctx, prefix: str) -> str:
    zone = ctx.unique_zone(prefix)

    def _drop():
        ctx.api("DELETE", f"zones/ns1/{_z(zone)}")
        for srv in ctx.pdns.servers:
            if ctx.pdns.zone(srv, zone) is not None:
                ctx.pdns.delete_zone(srv, zone)

    ctx.cleanup(_drop, f"Zone {zone} entfernen")
    ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ["ns1.e2e.test."]}, expect=200)
    for srv in ctx.pdns.servers:
        ctx.check(ctx.pdns.zone(srv, zone) is not None, f"Zone fehlt auf {srv}")
    return zone


def check_fresh(ctx) -> None:
    zone = _make_zone(ctx, "f7")
    www = f"www.{zone}"

    with ctx.step("Record anlegen und aendern -> v2-Verlauf"):
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200,
                json={"name": www, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.10"}]})
        ctx.api("PUT", f"records/ns1/{_z(zone)}", expect=200, json={
            "name": www, "type": "A", "ttl": 600, "old_content": "192.0.2.10", "new_content": "192.0.2.11"})
        body = _hist(ctx, zone, expect=200).json()
        ctx.check(body["total"] >= 3, f"Verlauf zu kurz: {body['total']}")
        create = next(e for e in body["entries"] if e["action"] == "CREATE" and e["resource_type"] == "record")
        update = next(e for e in body["entries"] if e["action"] == "UPDATE" and e["resource_type"] == "record")
        for e in (create, update):
            ctx.eq(e["version"], 2, f"{e['action']}: Details-Version")
            ctx.eq(e["zone_name"], zone, f"{e['action']}: zone_name")
            ctx.eq(e["details"].get("after_source"), "reread", f"{e['action']}: after_source")
            ctx.eq(e["details"].get("primary_outcome"), "ok", f"{e['action']}: primary_outcome")
        ch = update["changes"][0]
        ctx.eq((ch["before"]["ttl"], [r["content"] for r in ch["before"]["records"]]), (300, ["192.0.2.10"]),
               "UPDATE before")
        ctx.eq((ch["after"]["ttl"], [r["content"] for r in ch["after"]["records"]]), (600, ["192.0.2.11"]),
               "UPDATE after")
        ctx.check(update["can_rollback"] is True, f"UPDATE nicht ruecksetzbar: {update['rollback_blocked_reason']}")
        one = ctx.api("GET", f"zones/ns1/{_z(zone)}/history/{update['id']}", expect=200).json()
        ctx.eq(one["id"], update["id"], "Einzeleintrag")
        types = {e["id"] for e in _entries(ctx, zone, type="A", name=www)}
        ctx.check({create["id"], update["id"]} <= types, f"Filter type/name: {types}")

    with ctx.step("Peer-Erhalt: Wert nur auf ns2 bleibt erhalten [D1]"):
        ctx.pdns.request("ns2", "PATCH", f"/zones/{_z(zone)}", json_body={"rrsets": [{
            "name": www, "type": "A", "ttl": 600, "changetype": "REPLACE",
            "records": [{"content": "192.0.2.11", "disabled": False}, {"content": "192.0.2.99", "disabled": False}]}]})
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200,
                json={"name": www, "type": "A", "ttl": 600, "records": [{"content": "192.0.2.12"}]})
        ctx.eq(ctx.pdns.contents("ns2", zone, www, "A"), ["192.0.2.11", "192.0.2.12", "192.0.2.99"], "ns2 nach Anlegen")
        ctx.api("DELETE", f"records/ns1/{_z(zone)}/delete", expect=200,
                json={"name": www, "type": "A", "content": "192.0.2.12"})
        ctx.eq(ctx.pdns.contents("ns2", zone, www, "A"), ["192.0.2.11", "192.0.2.99"], "ns2 nach Loeschen")
        ctx.eq(ctx.pdns.contents("ns1", zone, www, "A"), ["192.0.2.11"], "ns1 nach Loeschen")

    with ctx.step("Rollback-Vorschau und Rollback des UPDATE"):
        prev = ctx.api("GET", f"zones/ns1/{_z(zone)}/history/{update['id']}/rollback-preview", expect=200).json()
        ctx.check(prev["rollbackable"] is True and prev["has_conflicts"] is False, f"Vorschau: {prev}")
        ctx.eq(prev["targets"].get("ns1"), "write", "Ziel ns1")
        res = ctx.api("POST", f"zones/ns1/{_z(zone)}/history/{update['id']}/rollback", json={}, expect=200).json()
        rid = res["details"]["revert_audit_id"]
        ctx.check(rid, f"keine revert_audit_id: {res}")
        rr1 = ctx.pdns.rrset("ns1", zone, www, "A")
        ctx.eq((rr1 or {}).get("ttl"), 300, "TTL nach Rollback (ns1)")
        ctx.eq(_values(rr1), ["192.0.2.10"], "Werte nach Rollback (ns1)")
        ctx.eq(ctx.pdns.contents("ns2", zone, www, "A"), ["192.0.2.10"], "Peer folgt dem Rollback (ns2)")
        again = ctx.api("GET", f"zones/ns1/{_z(zone)}/history/{update['id']}", expect=200).json()
        ctx.eq(again["reverted_by_id"], rid, "reverted_by_id")
        rb = ctx.api("GET", f"zones/ns1/{_z(zone)}/history/{rid}", expect=200).json()
        ctx.eq((rb["action"], rb["revert_of_id"]), ("RECORD_ROLLBACK", update["id"]), "Rollback-Eintrag")

    with ctx.step("Konflikt: 409 ohne force, 200 mit force"):
        ctx.api("PUT", f"records/ns1/{_z(zone)}", expect=200, json={
            "name": www, "type": "A", "ttl": 300, "old_content": "192.0.2.10", "new_content": "192.0.2.13"})
        r = ctx.api("POST", f"zones/ns1/{_z(zone)}/history/{create['id']}/rollback", json={"force": False}, expect=409)
        ctx.eq(r.json()["detail"]["code"], "rollback_conflict", "409-Code")
        res = ctx.api("POST", f"zones/ns1/{_z(zone)}/history/{create['id']}/rollback", json={"force": True},
                      expect=200).json()
        ctx.check(res["details"]["forced"] is True, f"forced fehlt: {res}")
        ctx.eq(ctx.pdns.contents("ns1", zone, www, "A"), [], "RRset nach Rollback der Anlage geloescht (ns1)")

    if ctx.user_token and ctx.user_id:
        with ctx.step("Lese-Nutzer: Verlauf ja, Rollback nein; ohne Recht 403"):
            ctx.cleanup(lambda: ctx.set_user_zones(ctx.user_id, {}), "Zonenrechte e2e-user zuruecksetzen")
            _hist(ctx, zone, token=ctx.user_token, expect=403)
            ctx.set_user_zones(ctx.user_id, {zone: "read"})
            body = _hist(ctx, zone, token=ctx.user_token, expect=200).json()
            ctx.check(all(not e["can_rollback"] for e in body["entries"]), "Lese-Nutzer darf zuruecksetzen")
            ctx.check(all(e["client_ip"] is None for e in body["entries"]), "client_ip fuer Nicht-Admin sichtbar")
            ctx.api("GET", f"zones/ns1/{_z(zone)}/history/{update['id']}/rollback-preview", token=ctx.user_token,
                    expect=403)
            ctx.api("POST", f"zones/ns1/{_z(zone)}/history/{update['id']}/rollback", token=ctx.user_token,
                    json={"force": True}, expect=403)

    with ctx.step("Admin-Audit-Log: Filter, total, Einzeleintrag, CSV"):
        data = ctx.api("GET", f"audit-log?zone={_z(zone)}&limit=2", expect=200).json()
        ctx.check(data["total"] >= 6 and data["count"] == 2, f"audit-log total/count: {data['total']}/{data['count']}")
        ctx.check(all(e["zone_name"] == zone for e in data["entries"]), "Zonenfilter")
        one = ctx.api("GET", f"audit-log/{update['id']}", expect=200).json()
        ctx.eq(one["details"]["version"], 2, "audit-log/{id} Details")
        raw = ctx.api("GET", f"audit-log/export?zone={_z(zone)}", expect=200).text.lstrip("﻿")
        table = list(csv.reader(io.StringIO(raw), delimiter=";"))
        ctx.eq(table[0][-3:], ["zone_name", "username", "revert_of_id"], "CSV-Spalten")
        ctx.check(len(table) - 1 == data["total"], f"CSV-Zeilen {len(table) - 1} != total {data['total']}")

    with ctx.step("Aufbewahrung nur per Browser-Session"):
        ctx.api("GET", "audit-log/settings", expect=403)
        st = ctx.admin_session.get("audit-log/settings", expect=200).json()
        ctx.check("retention_days" in st and "total_entries" in st, f"Settings: {st}")
        ctx.admin_session.put("audit-log/settings", json={"retention_days": 3}, expect=422)


def check_upgrade(ctx) -> None:
    seed = ctx.seed
    zones = seed.get("zones", {})
    if "alpha" not in zones or "beta" not in zones:
        ctx.fail(f"Seed ohne Zonen alpha/beta: {sorted(zones)}")
    alpha, beta = zones["alpha"], zones["beta"]

    with ctx.step("Alt-Eintraege (v1) im Zonenverlauf"):
        entries = _entries(ctx, alpha, limit=200)
        old = [e for e in entries if e["id"] in set(seed.get("audit_ids", []))]
        ctx.check(old, f"keine Alt-Eintraege im Verlauf von {alpha}")
        for e in old:
            ctx.eq(e["zone_name"], alpha, f"zone_name Alt-Eintrag #{e['id']}")
            ctx.eq(e["version"], 1, f"Version Alt-Eintrag #{e['id']}")
            ctx.check(e["can_rollback"] is False, f"Alt-Eintrag #{e['id']} ruecksetzbar")
        ctx.check(any(e["rollback_blocked_reason"] == "legacy_format" for e in old), "legacy_format fehlt")

    host = f"{ctx.unique('f7')}.{alpha}"
    ctx.cleanup(lambda: ctx.api("DELETE", f"records/ns1/{_z(alpha)}/delete", json={"name": host, "type": "A"}),
                "Record entfernen")
    with ctx.step("Neue Aenderung ist v2 und ruecksetzbar"):
        ctx.api("POST", f"records/ns1/{_z(alpha)}", expect=200,
                json={"name": host, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.55"}]})
        e = _entries(ctx, alpha, name=host)[0]
        ctx.eq((e["version"], e["action"]), (2, "CREATE"), "neuer Eintrag")
        ctx.api("POST", f"zones/ns1/{_z(alpha)}/history/{e['id']}/rollback", json={}, expect=200)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, alpha, host, "A"), [], f"Record nach Rollback weg ({srv})")

    if ctx.user_token:
        with ctx.step("alice: Lesen auf beta ohne Zuruecksetzen"):
            body = _hist(ctx, beta, token=ctx.user_token, expect=200).json()
            ctx.check(all(not x["can_rollback"] for x in body["entries"]), "alice darf auf beta zuruecksetzen")
