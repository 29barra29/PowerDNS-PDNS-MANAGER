"""E2E-Checks fuer WS-F4-A (DNSSEC-Backend) gegen echtes PowerDNS (4.9, gsqlite3 mit DNSSEC).

Ablauf je Modus mit eigener Master-Zone (nur auf ns1, damit ns2 als Peer "zone_missing" meldet):
Status vor dem Aktivieren -> Aktivieren (Standard bzw. im Upgrade-Pfad mit 2.x-Body ``nsec3param``) -> idempotentes
Wiederholen -> Status -> CSK-Rollover (Pre-Publish, Schutzregel 409, Aktivieren mit Serial-Erhoehung + NOTIFY,
Deaktivieren, Loeschen) -> veroeffentlichen/zurueckziehen -> NSEC umstellen -> Lesen ohne ``privatekey`` ->
Zonenrecht -> Deaktivieren -> Audit-Eintraege. Der Zustand wird jeweils direkt in PowerDNS nachgeprueft.
Aufraeumen: Zone auf allen Servern loeschen.
"""
from __future__ import annotations

from urllib.parse import quote

NS = ["ns1.e2e.test.", "ns2.e2e.test."]
SRV = "ns1"


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _delete_zone_everywhere(ctx, zone: str) -> None:
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.pdns.delete_zone(srv, zone)


def _pdns_keys(ctx, zone: str) -> list[dict]:
    return ctx.pdns.request(SRV, "GET", f"/zones/{_z(zone)}/cryptokeys", expect=200).json()


def _serial(ctx, zone: str) -> int:
    rr = ctx.pdns.rrset(SRV, zone, zone, "SOA")
    ctx.check(rr is not None, f"SOA fehlt in {zone}")
    return int(rr["records"][0]["content"].split()[2])


def _no_private(ctx, resp, label: str) -> None:
    ctx.check("privatekey" not in resp.text and "Private-key-format" not in resp.text,
              f"{label}: Antwort enthaelt Privatschluessel-Material")


def _flow(ctx, prefix: str, enable_body: dict, expected_nsec3param: str) -> None:
    zone = ctx.unique_zone(prefix)
    ctx.cleanup(lambda: _delete_zone_everywhere(ctx, zone), "Zone entfernen")
    base = f"dnssec/{SRV}/{_z(zone)}"

    with ctx.step("Master-Zone nur auf ns1 anlegen"):
        res = ctx.api("POST", "zones", json={"name": zone, "kind": "Master", "nameservers": NS, "servers": [SRV]},
                      expect=200).json()
        ctx.check(str(res.get("details", {}).get(SRV, "")).startswith("created"), f"Zonenanlage: {res}")

    with ctx.step("Status vor dem Aktivieren"):
        st = ctx.api("GET", f"{base}/status", expect=200).json()
        ctx.eq(st["signed"], False, "signed vor dem Aktivieren")
        ctx.eq(st["keys"], [], "keine Schluessel")
        ctx.eq(st["zone_kind"], "Master", "zone_kind")
        ctx.check(str(st.get("server_version") or "").startswith("4."), f"server_version: {st.get('server_version')}")
        ctx.eq(st["capabilities"]["supports_published"], True, "supports_published (PowerDNS 4.9)")
        ctx.eq(st["can_write"], True, "can_write fuer den Admin")

    with ctx.step("DNSSEC aktivieren"):
        serial0 = _serial(ctx, zone)
        r = ctx.api("POST", f"{base}/enable", json=enable_body, expect=200)
        _no_private(ctx, r, "enable")
        d = r.json()["details"]
        ctx.eq(d["already_enabled"], False, "already_enabled")
        ctx.eq(d["nsec3param"], expected_nsec3param, "nsec3param in der Antwort")
        ctx.check(d["key"] and d["key"]["keytype"] == "csk" and d["key"]["active"] is True, f"Schluessel: {d['key']}")
        ctx.check(isinstance(d["key"].get("key_tag"), int), f"key_tag fehlt: {d['key']}")
        ctx.check(any(" 13 2 " in ds for ds in d["ds_records"]), f"DS (Alg. 13, SHA-256) fehlt: {d['ds_records']}")
        ctx.eq(d["serial_bumped"], True, "Serial nach dem Aktivieren erhoeht (Master)")
        meta = ctx.pdns.zone(SRV, zone)
        ctx.eq(meta.get("dnssec"), True, "PowerDNS dnssec-Flag")
        ctx.eq(meta.get("nsec3param", ""), expected_nsec3param, "PowerDNS nsec3param")
        ctx.eq(meta.get("api_rectify"), True, "PowerDNS api_rectify")
        ctx.check(_serial(ctx, zone) > serial0, "SOA-Serial in PowerDNS nicht erhoeht")
        old_id = d["key"]["id"]

    with ctx.step("Erneutes Aktivieren ist idempotent"):
        d = ctx.api("POST", f"{base}/enable", json={}, expect=200).json()["details"]
        ctx.eq(d["already_enabled"], True, "already_enabled beim zweiten Aufruf")
        ctx.eq(len(_pdns_keys(ctx, zone)), 1, "Anzahl Schluessel in PowerDNS")

    with ctx.step("Status signiert"):
        st = ctx.api("GET", f"{base}/status", expect=200).json()
        ctx.eq(st["signed"], True, "signed")
        ctx.eq([k["ds_status"] for k in st["keys"]], ["current"], "ds_status")
        ctx.eq(st["rollover"]["sep"]["phase"], "idle", "Rollover-Phase")
        ctx.eq(st["nsec"]["mode"], "nsec3" if expected_nsec3param else "nsec", "NSEC-Modus")
        peers = {p["server"]: p["state"] for p in st["peers"] or []}
        ctx.eq(peers.get("ns2"), "zone_missing", "Peer ns2 (Zone nur auf ns1)")
        ctx.check("secondaries_serial" in {h["code"] for h in st["hints"]}, f"Hinweise: {st['hints']}")

    with ctx.step("Rollover: neuen CSK vorab veroeffentlichen"):
        r = ctx.api("POST", f"{base}/keys", json={"keytype": "csk", "active": False, "published": True}, expect=200)
        _no_private(ctx, r, "POST keys")
        new_id = r.json()["details"]["key"]["id"]
        st = ctx.api("GET", f"{base}/status?peers=false", expect=200).json()
        ctx.eq(st["rollover"]["sep"]["phase"], "new_prepublished", "Phase nach Pre-Publish")
        ctx.eq(st["rollover"]["sep"]["new_key_id"], new_id, "neuer Schluessel")
        ctx.check(st["key_history"].get(str(new_id), {}).get("created_at"), f"key_history: {st['key_history']}")

    with ctx.step("Schutzregel: alten Schluessel nicht ohne aktiven Ersatz deaktivieren"):
        r = ctx.api("PUT", f"{base}/keys/{old_id}", json={"active": False}, expect=409).json()
        ctx.eq(r["detail"]["code"], "last_active_key", "Schutzregel-Code")
        ctx.eq(r["detail"]["force_possible"], True, "force_possible")

    with ctx.step("Neuen Schluessel aktivieren: Serial + NOTIFY"):
        serial1 = _serial(ctx, zone)
        d = ctx.api("PUT", f"{base}/keys/{new_id}", json={"active": True}, expect=200).json()["details"]
        ctx.eq(d["serial_bumped"], True, "serial_bumped")
        ctx.eq(d["notified"], True, f"NOTIFY gesendet (Fehler: {d.get('notify_error')})")
        ctx.check(_serial(ctx, zone) > serial1, "SOA-Serial nach dem Aktivieren nicht erhoeht")
        ctx.check(d["serial"] == _serial(ctx, zone), f"gemeldeter Serial {d['serial']} != PowerDNS")

    with ctx.step("Alten Schluessel deaktivieren und loeschen"):
        ctx.api("PUT", f"{base}/keys/{old_id}", json={"active": False}, expect=200)
        st = ctx.api("GET", f"{base}/status?peers=false", expect=200).json()
        ctx.eq(st["rollover"]["sep"]["phase"], "old_retired", "Phase nach dem Deaktivieren")
        ctx.api("DELETE", f"{base}/keys/{old_id}", expect=200)
        ctx.eq([k["id"] for k in _pdns_keys(ctx, zone)], [new_id], "Schluessel in PowerDNS")

    with ctx.step("Veroeffentlichen und zurueckziehen"):
        r = ctx.api("POST", f"{base}/keys", json={"keytype": "zsk", "active": False, "published": False,
                                                  "bump_serial": False}, expect=200)
        zsk = r.json()["details"]["key"]
        ctx.eq(zsk["published"], False, "ZSK unveroeffentlicht angelegt")
        d = ctx.api("PUT", f"{base}/keys/{zsk['id']}", json={"published": True}, expect=200).json()["details"]
        ctx.eq(d["published"], True, "published in der Antwort")
        pk = {k["id"]: k for k in _pdns_keys(ctx, zone)}
        ctx.eq(pk[zsk["id"]].get("published"), True, "published in PowerDNS")
        ctx.api("PUT", f"{base}/keys/{zsk['id']}", json={"published": False}, expect=200)
        ctx.api("DELETE", f"{base}/keys/{zsk['id']}", expect=200)

    with ctx.step("NSEC/NSEC3 aendern"):
        d = ctx.api("PUT", f"{base}/nsec3", json={"nsec_mode": "nsec"}, expect=200).json()["details"]
        ctx.eq(d["unchanged"], False, "unchanged beim Wechsel auf NSEC")
        ctx.eq(ctx.pdns.zone(SRV, zone).get("nsec3param", ""), "", "PowerDNS nsec3param nach NSEC")
        d = ctx.api("PUT", f"{base}/nsec3", json={"nsec_mode": "nsec"}, expect=200).json()["details"]
        ctx.eq(d["unchanged"], True, "zweiter Aufruf unveraendert")
        ctx.api("PUT", f"{base}/nsec3", json={"nsec_mode": "nsec3", "nsec3_iterations": 0}, expect=200)
        ctx.eq(ctx.pdns.zone(SRV, zone).get("nsec3param"), "1 0 0 -", "PowerDNS nsec3param nach NSEC3")

    with ctx.step("Lesen ohne privatekey"):
        for path in (f"{base}/keys", f"{base}/ds", f"{base}/keys/{new_id}", f"{base}/status"):
            _no_private(ctx, ctx.api("GET", path, expect=200), path)

    with ctx.step("Zonenrecht: Benutzer ohne Recht"):
        if ctx.user_token:  # fresh: e2e-user ohne Zonenrechte; upgrade: alice (nur alpha/beta)
            ctx.api("GET", f"{base}/status", token=ctx.user_token, expect=403)
            ctx.api("POST", f"{base}/enable", token=ctx.user_token, json={}, expect=403)

    with ctx.step("DNSSEC deaktivieren"):
        d = ctx.api("POST", f"{base}/disable", json={}, expect=200).json()["details"]
        ctx.eq([k["key_id"] for k in d["deleted_keys"]], [new_id], "geloeschte Schluessel")
        ctx.eq(_pdns_keys(ctx, zone), [], "Schluessel in PowerDNS nach dem Deaktivieren")
        meta = ctx.pdns.zone(SRV, zone)
        ctx.eq(meta.get("nsec3param", ""), "", "nsec3param nach dem Deaktivieren")
        ctx.eq(meta.get("dnssec"), False, "dnssec-Flag nach dem Deaktivieren")
        d = ctx.api("POST", f"{base}/disable", expect=200).json()["details"]
        ctx.eq(d["already_disabled"], True, "already_disabled")

    with ctx.step("Audit-Eintraege"):
        rows = ctx.db("SELECT action, resource_type, status, server_name FROM audit_logs WHERE zone_name = %s "
                      "ORDER BY id", (zone,))
        actions = {r["action"] for r in rows if r["status"] == "success"}
        expected = {"DNSSEC_ENABLE", "KEY_CREATE", "KEY_ACTIVATE", "KEY_DEACTIVATE", "KEY_DELETE", "KEY_PUBLISH",
                    "KEY_UNPUBLISH", "DNSSEC_NSEC3_UPDATE", "DNSSEC_DISABLE", "ZONE_NOTIFY", "UPDATE"}
        ctx.check(expected <= actions, f"Audit-Aktionen fehlen: {sorted(expected - actions)}")
        soa = ctx.db("SELECT COUNT(*) AS n FROM audit_logs WHERE zone_name = %s AND action = 'UPDATE' "
                     "AND resource_type = 'record' AND details LIKE %s", (zone, '%"source": "dnssec"%'))
        ctx.check(soa and soa[0]["n"] >= 1, "kein SOA-Audit mit source=dnssec")
        ctx.check(all(r["server_name"] == SRV for r in rows if r["resource_type"] == "dnssec_key"),
                  "dnssec_key-Audits mit falschem Server")


def check_fresh(ctx) -> None:
    _flow(ctx, "f4a", {}, "1 0 0 -")


def check_upgrade(ctx) -> None:
    # 2.x-Skripte senden den alten Body {"algorithm", "nsec3param"} – muss weiter funktionieren
    _flow(ctx, "f4a-up", {"algorithm": "ECDSAP256SHA256", "nsec3param": "1 0 1 ab"}, "1 0 1 ab")
