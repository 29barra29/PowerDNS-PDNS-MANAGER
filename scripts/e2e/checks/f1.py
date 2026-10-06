"""E2E-Checks fuer F1 (Bulk-Editor; Workstream WS-F1).

Neuinstallation (gegen echte PowerDNS-Server ns1/ns2 mit getrennten Backends):
- Vorschau (Auswahl) schreibt nichts; der Body der Vorschau geht unveraendert an ``/bulk`` -> je Server ein PATCH,
  Audit ``BULK_UPDATE`` v2 im Zonenverlauf (ruecksetzbar, Rollback stellt die TTL wieder her).
- Kommentar-Erhalt (E-F1-3): Bulk sendet REPLACE ohne ``comments`` – PowerDNS behaelt vorhandene Kommentare.
- Textfluss: Zeilenfehler mit Zeilennummern; ``sync_scope`` ersetzt geladene RRsets und loescht entfernte.
- Optimistische Sperre: Aenderung zwischen Vorschau und Anwenden -> 409, mit ``force`` -> 200; ``expected`` deckt
  auch RRsets ab, die die Vorschau als unveraendert zeigt (REPLACE mit gleichen Werten).
- Peer-Drift: fehlt ein Wert nur auf ns2, wird er dort uebersprungen (``peer_drift``), kein Fehler.
- Schutz: leere Anfrage/leere Werteliste 422, Apex-NS komplett loeschen 422, Lese-Nutzer 403 (Vorschau und Anwenden).
Upgrade (Altdaten 2.4.1):
- Alt-Admin-Token kann Vorschau und Bulk nutzen; ``alice`` (alpha=manage, beta=read) darf die Vorschau fuer alpha,
  nicht fuer beta. Die Seed-Zonen bleiben unveraendert (Vorschau schreibt nichts).
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
    for srv in ctx.pdns.servers:
        ctx.check(ctx.pdns.zone(srv, zone) is not None, f"Zone fehlt auf {srv}")
    return zone


def _preview(ctx, zone: str, body: dict, **kw):
    return ctx.api("POST", f"records/ns1/{_z(zone)}/bulk/preview", json=body, **kw)


def _bulk(ctx, zone: str, body: dict, **kw):
    return ctx.api("POST", f"records/ns1/{_z(zone)}/bulk", json=body, **kw)


def _ttl(ctx, srv: str, zone: str, name: str, rtype: str):
    return (ctx.pdns.rrset(srv, zone, name, rtype) or {}).get("ttl")


def _bulk_entries(ctx, zone: str) -> list[dict]:
    return ctx.api("GET", f"zones/ns1/{_z(zone)}/history?action=BULK_UPDATE", expect=200).json()["entries"]


def check_fresh(ctx) -> None:
    zone = _make_zone(ctx, "f1")
    www, txt, mail = f"www.{zone}", f"txt.{zone}", f"mail.{zone}"

    with ctx.step("Ausgangsdaten anlegen"):
        _bulk(ctx, zone, {"merge": [
            {"name": www, "type": "A", "ttl": 3600, "records": [{"content": "192.0.2.1"}, {"content": "192.0.2.2"}]},
            {"name": mail, "type": "MX", "ttl": 3600, "records": [{"content": f"10 mx1.{zone}"},
                                                                    {"content": f"20 mx2.{zone}"}]},
        ]}, expect=200)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, www, "A"), ["192.0.2.1", "192.0.2.2"], f"www auf {srv}")

    with ctx.step("Vorschau (Auswahl) schreibt nichts"):
        p = _preview(ctx, zone, {"ops": {"source": "selection", "set_ttl": [{"name": www, "type": "A", "ttl": 300}]}},
                     expect=200).json()
        ctx.eq(p["blocking"], False, "blocking")
        ctx.eq([(c["name"], c["op"], c["ttl_before"], c["ttl_after"]) for c in p["changes"]],
               [(www, "update", 3600, 300)], "Vorschau changes")
        ctx.eq(p["peers"], ["ns2"], "peers")
        ctx.check(p["ops"] and p["ops"]["expected"][0]["fingerprint"] != "absent", f"expected fehlt: {p['ops']}")
        ctx.eq(_ttl(ctx, "ns1", zone, www, "A"), 3600, "ns1 unveraendert nach Vorschau")

    with ctx.step("Anwenden mit dem Body der Vorschau -> beide Server, Audit v2 ruecksetzbar"):
        res = _bulk(ctx, zone, p["ops"], expect=200).json()
        ctx.eq(res["details"]["fanout"], {"ns1": "saved", "ns2": "saved"}, "fanout")
        ctx.eq(res["details"]["changed_rrsets"], 1, "changed_rrsets")
        for srv in ctx.pdns.servers:
            ctx.eq(_ttl(ctx, srv, zone, www, "A"), 300, f"TTL auf {srv}")
        entry = next(e for e in _bulk_entries(ctx, zone) if e["id"] == res["details"]["audit_id"])
        ctx.eq(entry["version"], 2, "Details-Version")
        ctx.eq(entry["details"].get("source"), "selection", "source")
        ctx.eq([(c["name"], c["type"]) for c in entry["changes"]], [(www, "A")], "changes")
        ctx.check(entry["can_rollback"] is True, f"nicht ruecksetzbar: {entry.get('rollback_blocked_reason')}")
        ctx.api("POST", f"zones/ns1/{_z(zone)}/history/{entry['id']}/rollback", json={}, expect=200)
        ctx.eq(_ttl(ctx, "ns1", zone, www, "A"), 3600, "TTL nach Rollback")

    with ctx.step("Kommentar-Erhalt (E-F1-3): REPLACE ohne comments behaelt Kommentare"):
        comment = {"content": "e2e-kommentar", "account": "e2e", "modified_at": 1700000000}
        for srv in ctx.pdns.servers:
            ctx.pdns.request(srv, "PATCH", f"/zones/{_z(zone)}", json_body={"rrsets": [{
                "name": txt, "type": "TXT", "ttl": 3600, "changetype": "REPLACE",
                "records": [{"content": '"hallo"', "disabled": False}], "comments": [comment]}]})
        _bulk(ctx, zone, {"source": "selection", "set_ttl": [{"name": txt, "type": "TXT", "ttl": 600}],
                          "set_disabled": [{"name": www, "type": "A", "content": "192.0.2.2", "disabled": True}]},
              expect=200)
        for srv in ctx.pdns.servers:
            rr = ctx.pdns.rrset(srv, zone, txt, "TXT") or {}
            ctx.eq(rr.get("ttl"), 600, f"TXT-TTL auf {srv}")
            ctx.eq([c.get("content") for c in rr.get("comments") or []], ["e2e-kommentar"], f"Kommentar auf {srv}")
            www_rr = ctx.pdns.rrset(srv, zone, www, "A") or {}
            ctx.eq({r["content"]: r["disabled"] for r in www_rr.get("records", [])},
                   {"192.0.2.1": False, "192.0.2.2": True}, f"disabled auf {srv}")

    with ctx.step("Textfluss: Zeilenfehler und sync_scope"):
        bad = _preview(ctx, zone, {"text": {"content": "ok A 192.0.2.5\nkaputt A 999.1.1.1\n$INCLUDE x"}},
                       expect=200).json()
        ctx.eq(bad["blocking"], True, "blocking bei Zeilenfehlern")
        ctx.eq([(i["code"], i["line"]) for i in bad["issues"]], [("parse_error", 2), ("directive_unsupported", 3)],
               "Zeilenfehler")
        text = f"$ORIGIN {zone}\nwww 3600 IN A 192.0.2.1\nwww 3600 IN A 192.0.2.3\n"
        p = _preview(ctx, zone, {"text": {"content": text, "mode": "sync_scope", "default_ttl": 3600, "scope": [
            {"name": www, "type": "A"}, {"name": txt, "type": "TXT"}]}}, expect=200).json()
        ctx.eq(p["blocking"], False, f"sync_scope blocking: {p['issues']}")
        by = {(c["name"], c["type"]): c for c in p["changes"]}
        ctx.eq(sorted(by[(www, "A")]["added"]), ["192.0.2.3"], "added")
        ctx.eq(sorted(by[(www, "A")]["removed"]), ["192.0.2.2"], "removed")
        ctx.eq(by[(txt, "TXT")]["op"], "delete", "TXT geloescht (nicht mehr im Text)")
        _bulk(ctx, zone, p["ops"], expect=200)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, www, "A"), ["192.0.2.1", "192.0.2.3"], f"www nach sync auf {srv}")
            ctx.eq(ctx.pdns.rrset(srv, zone, txt, "TXT"), None, f"TXT nach sync auf {srv}")

    with ctx.step("Optimistische Sperre: 409 nach fremder Aenderung, force -> 200"):
        p = _preview(ctx, zone, {"ops": {"source": "selection", "delete": [
            {"name": mail, "type": "MX", "content": f"20 mx2.{zone}"}]}}, expect=200).json()
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200, json={
            "name": mail, "type": "MX", "ttl": 3600, "records": [{"content": f"30 mx3.{zone}"}]})
        r = _bulk(ctx, zone, p["ops"], expect=409).json()
        ctx.eq(r["detail"]["conflicts"], [{"name": mail, "type": "MX"}], "conflicts")
        ctx.eq(sorted(ctx.pdns.contents("ns1", zone, mail, "MX")),
               sorted([f"10 mx1.{zone}", f"20 mx2.{zone}", f"30 mx3.{zone}"]), "nichts geschrieben")
        res = _bulk(ctx, zone, {**p["ops"], "force": True}, expect=200).json()
        entry = next(e for e in _bulk_entries(ctx, zone) if e["id"] == res["details"]["audit_id"])
        ctx.eq(entry["details"].get("forced"), True, "forced im Audit")
        ctx.eq(sorted(ctx.pdns.contents("ns1", zone, mail, "MX")), sorted([f"10 mx1.{zone}", f"30 mx3.{zone}"]),
               "MX nach force")

    with ctx.step("Optimistische Sperre deckt auch unveraenderte RRsets der Ops ab (REPLACE)"):
        mx_now = sorted(ctx.pdns.contents("ns1", zone, mail, "MX"))
        p = _preview(ctx, zone, {"ops": {"source": "api", "create": [
            {"name": mail, "type": "MX", "ttl": 3600, "records": [{"content": c} for c in mx_now]}],
            "set_ttl": [{"name": www, "type": "A", "ttl": 600}]}}, expect=200).json()
        ctx.eq([(c["name"], c["type"]) for c in p["changes"]], [(www, "A")], "nur www geaendert")
        ctx.eq(sorted((e["name"], e["type"]) for e in p["ops"]["expected"]), sorted([(mail, "MX"), (www, "A")]),
               "expected deckt alle beruehrten RRsets ab")
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200, json={
            "name": mail, "type": "MX", "ttl": 3600, "records": [{"content": f"40 mx4.{zone}"}]})
        r = _bulk(ctx, zone, p["ops"], expect=409).json()
        ctx.eq(r["detail"]["conflicts"], [{"name": mail, "type": "MX"}], "conflicts (unveraendertes REPLACE)")
        ctx.eq(sorted(ctx.pdns.contents("ns1", zone, mail, "MX")), sorted(mx_now + [f"40 mx4.{zone}"]),
               "fremder MX-Wert bleibt erhalten")
        ctx.eq(_ttl(ctx, "ns1", zone, www, "A"), 3600, "www unveraendert nach 409")

    with ctx.step("Peer-Drift: Wert fehlt nur auf ns2"):
        ctx.pdns.request("ns2", "PATCH", f"/zones/{_z(zone)}", json_body={"rrsets": [{
            "name": www, "type": "A", "ttl": 3600, "changetype": "REPLACE",
            "records": [{"content": "192.0.2.1", "disabled": False}]}]})
        res = _bulk(ctx, zone, {"delete": [{"name": www, "type": "A", "content": "192.0.2.3"}]}, expect=200).json()
        ctx.eq(res["details"]["fanout"].get("ns1"), "saved", "ns1 gespeichert")
        ctx.eq(res["details"]["fanout"].get("ns2"), "skipped (no changes needed)", "ns2 ohne Aenderung")
        ctx.eq(res["details"]["peer_drift"], {"ns2": 1}, "peer_drift")
        ctx.eq(ctx.pdns.contents("ns1", zone, www, "A"), ["192.0.2.1"], "ns1 nach Loeschen")

    with ctx.step("Schutz: 422 bei leerer Anfrage, leerer Werteliste und Apex-NS-Loeschung"):
        _bulk(ctx, zone, {"create": [], "delete": []}, expect=422)
        _bulk(ctx, zone, {"create": [{"name": www, "type": "A", "records": []}]}, expect=422)
        r = _bulk(ctx, zone, {"delete": [{"name": zone, "type": "NS"}]}, expect=422).json()
        ctx.eq(r["detail"]["issues"][0]["code"], "apex_ns_delete", "Apex-NS")
        ctx.check(ctx.pdns.rrset("ns1", zone, zone, "NS") is not None, "Apex-NS noch vorhanden")

    if ctx.user_token and ctx.user_id:
        with ctx.step("Lese-Nutzer: Vorschau und Anwenden 403"):
            ctx.cleanup(lambda: ctx.set_user_zones(ctx.user_id, {}), "Zonenrechte e2e-user zuruecksetzen")
            ctx.set_user_zones(ctx.user_id, {zone: "read"})
            body = {"delete": [{"name": mail, "type": "MX"}]}
            _preview(ctx, zone, {"ops": body}, token=ctx.user_token, expect=403)
            _bulk(ctx, zone, body, token=ctx.user_token, expect=403)
            ctx.check(ctx.pdns.rrset("ns1", zone, mail, "MX") is not None, "MX noch vorhanden")


def check_upgrade(ctx) -> None:
    seed_zones = ctx.seed.get("zones") or {}
    alpha, beta = seed_zones.get("alpha"), seed_zones.get("beta")
    if not alpha or not beta:
        ctx.fail(f"Seed-Zonen fehlen: {seed_zones}")

    with ctx.step("Alt-Admin-Token: Vorschau auf der Seed-Zone schreibt nichts"):
        before = ctx.pdns.zone("ns1", alpha)
        p = _preview(ctx, alpha, {"ops": {"source": "selection", "set_ttl": [
            {"name": f"www.{alpha}", "type": "A", "ttl": 120}]}}, expect=200).json()
        ctx.eq([c["name"] for c in p["changes"]], [f"www.{alpha}"], "Vorschau alpha")
        ctx.eq(ctx.pdns.zone("ns1", alpha).get("serial"), before.get("serial"), "Serial unveraendert")

    with ctx.step("Alt-Admin-Token: Bulk auf eigener Zone"):
        zone = _make_zone(ctx, "f1-up")
        res = _bulk(ctx, zone, {"merge": [{"name": f"www.{zone}", "type": "A", "records": [{"content": "192.0.2.7"}]}]},
                    expect=200).json()
        ctx.eq(res["details"]["fanout"], {"ns1": "saved", "ns2": "saved"}, "fanout")
        entry = next(e for e in _bulk_entries(ctx, zone) if e["id"] == res["details"]["audit_id"])
        ctx.eq(entry["version"], 2, "v2 nach Upgrade")

    if ctx.user_token:
        with ctx.step("alice: Vorschau fuer alpha (manage) ja, fuer beta (read) nein"):
            _preview(ctx, alpha, {"ops": {"delete": [{"name": f"www.{alpha}", "type": "A"}]}}, token=ctx.user_token,
                     expect=200)
            _preview(ctx, beta, {"ops": {"delete": [{"name": f"www.{beta}", "type": "A"}]}}, token=ctx.user_token,
                     expect=403)
            _bulk(ctx, beta, {"delete": [{"name": f"www.{beta}", "type": "A"}]}, token=ctx.user_token, expect=403)
            ctx.check(ctx.pdns.rrset("ns1", beta, f"www.{beta}", "A") is not None, "beta unveraendert")
