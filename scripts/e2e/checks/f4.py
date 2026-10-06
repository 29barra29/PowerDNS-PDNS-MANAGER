"""E2E-Checks fuer WS-F4-B (DNSSEC beim Zonenanlegen, Optionen, Schluessel-Lebenszyklus) gegen PowerDNS 4.9.

pdns1/pdns2 haben getrennte gsqlite3-Datenbanken. Ablauf je Modus:

1. Zonenanlage mit ``enable_dnssec`` + ``dnssec_options`` (KSK+ZSK, ECDSAP384SHA384, NSEC3 ``1 0 0 -``) auf ns1 und
   ns2: DNSSEC nur auf ns1 (``created``), ns2 meldet ``created; dnssec-skipped`` und bleibt ohne Schluessel;
   Audits ``CREATE`` (``details.dnssec``) und ``DNSSEC_ENABLE`` (``source=zone_create``); Status zeigt ns2 als
   Peer ``unsigned``.
2. Eigene Master-Zone nur auf ns1: Aktivieren mit Optionen (CSK, RSASHA256/2048, NSEC3 mit Iteration und Salt),
   Schluessel ``published``+inaktiv anlegen (Rollover-Phase ``new_prepublished``), aktivieren (Serial + NOTIFY),
   NSEC3 aendern, DS lesen (Status und ``/ds`` passend zu PowerDNS), SOA-Erhoehung im Zonenverlauf (nicht
   zurueckrollbar), Deaktivieren. Zustand jeweils direkt in PowerDNS nachgeprueft.
Aufraeumen: Zonen auf allen Servern loeschen.
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


def _pdns_keys(ctx, srv: str, zone: str) -> list[dict]:
    return ctx.pdns.request(srv, "GET", f"/zones/{_z(zone)}/cryptokeys", expect=200).json()


def _serial(ctx, zone: str) -> int:
    rr = ctx.pdns.rrset(SRV, zone, zone, "SOA")
    ctx.check(rr is not None, f"SOA fehlt in {zone}")
    return int(rr["records"][0]["content"].split()[2])


def _no_private(ctx, resp, label: str) -> None:
    ctx.check("privatekey" not in resp.text and "Private-key-format" not in resp.text,
              f"{label}: Antwort enthaelt Privatschluessel-Material")


def _zone_create_with_options(ctx, prefix: str) -> None:
    zone = ctx.unique_zone(prefix)
    ctx.cleanup(lambda: _delete_zone_everywhere(ctx, zone), "Zone entfernen")
    options = {"key_model": "ksk_zsk", "algorithm": "ECDSAP384SHA384", "nsec_mode": "nsec3",
               "nsec3_iterations": 0, "nsec3_salt": "-", "nsec3_optout": False, "nsec3narrow": False}

    with ctx.step("Ungueltige DNSSEC-Optionen beim Anlegen -> 422, nichts angelegt"):
        r = ctx.api("POST", "zones", json={"name": zone, "nameservers": NS, "enable_dnssec": True,
                                           "dnssec_options": {"algorithm": "RSASHA1"}}, expect=422)
        ctx.check("nicht unterst" in r.text, f"deutscher Validierungstext fehlt: {r.text[:300]}")
        ctx.check(all(ctx.pdns.zone(s, zone) is None for s in ctx.pdns.servers), "Zone trotz 422 angelegt")

    with ctx.step("Zone mit DNSSEC-Optionen auf ns1 und ns2 anlegen"):
        res = ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": NS, "servers": ["ns1", "ns2"],
                                             "enable_dnssec": True, "dnssec_options": options}, expect=200)
        _no_private(ctx, res, "POST zones")
        details = res.json().get("details", {})
        ctx.eq(details, {"ns1": "created", "ns2": "created; dnssec-skipped"}, "Ergebnis je Server")

    with ctx.step("PowerDNS: Schluessel nur auf ns1 (KSK + ZSK, ECDSAP384SHA384), NSEC3 1 0 0 -"):
        keys = _pdns_keys(ctx, "ns1", zone)
        ctx.eq(sorted(k["keytype"] for k in keys), ["ksk", "zsk"], "Schluesseltypen auf ns1")
        ctx.check(all(k["active"] and str(k["algorithm"]).upper() == "ECDSAP384SHA384" for k in keys),
                  f"Schluessel auf ns1: {keys}")
        ctx.eq(ctx.pdns.zone("ns1", zone).get("nsec3param"), "1 0 0 -", "nsec3param auf ns1")
        ctx.eq(_pdns_keys(ctx, "ns2", zone), [], "keine Schluessel auf ns2 (eigene Datenbank)")

    with ctx.step("Status: signiert, KSK+ZSK-Spuren, ns2 als unsignierter Peer"):
        st = ctx.api("GET", f"dnssec/{SRV}/{_z(zone)}/status", expect=200).json()
        ctx.eq(st["signed"], True, "signed")
        ctx.eq(sorted(k["role"] for k in st["keys"]), ["sep", "zsk"], "Rollen")
        ctx.check(st["rollover"]["zsk"] is not None and st["rollover"]["sep"]["phase"] == "idle",
                  f"Rollover-Spuren: {st['rollover']}")
        peers = {p["server"]: p["state"] for p in st.get("peers") or []}
        ctx.eq(peers.get("ns2"), "unsigned", "Peer ns2")
        ctx.check("peers_unsigned" in {h["code"] for h in st["hints"]}, f"Hinweise: {st['hints']}")

    with ctx.step("Audits: CREATE mit details.dnssec, DNSSEC_ENABLE source=zone_create"):
        rows = ctx.db("SELECT action, server_name, status, details FROM audit_logs WHERE zone_name = %s ORDER BY id",
                      (zone,))
        creates = [r for r in rows if r["action"] == "CREATE" and r["status"] == "success"]
        ctx.eq(sorted(r["server_name"] for r in creates), ["ns1", "ns2"], "CREATE-Audits")
        ctx.check(all('"server": "ns1"' in str(r["details"]) for r in creates), f"details.dnssec.server: {creates}")
        enables = [r for r in rows if r["action"] == "DNSSEC_ENABLE"]
        ctx.eq([(r["server_name"], r["status"]) for r in enables], [("ns1", "success")], "DNSSEC_ENABLE")
        ctx.check('"source": "zone_create"' in str(enables[0]["details"]), f"source: {enables[0]['details']}")


def _key_lifecycle(ctx, prefix: str) -> None:
    zone = ctx.unique_zone(prefix)
    ctx.cleanup(lambda: _delete_zone_everywhere(ctx, zone), "Zone entfernen")
    base = f"dnssec/{SRV}/{_z(zone)}"

    with ctx.step("Master-Zone nur auf ns1 anlegen (ohne DNSSEC)"):
        res = ctx.api("POST", "zones", json={"name": zone, "kind": "Master", "nameservers": NS, "servers": [SRV]},
                      expect=200).json()
        ctx.eq(res.get("details"), {SRV: "created"}, "Zonenanlage")

    with ctx.step("Aktivieren mit Optionen (CSK, RSASHA256/2048, NSEC3 1 0 1 ab)"):
        body = {"key_model": "csk", "algorithm": "RSASHA256", "bits": 2048, "nsec_mode": "nsec3",
                "nsec3_iterations": 1, "nsec3_salt": "AB", "nsec3_optout": False, "nsec3narrow": False}
        r = ctx.api("POST", f"{base}/enable", json=body, expect=200)
        _no_private(ctx, r, "enable")
        d = r.json()["details"]
        ctx.eq(d["already_enabled"], False, "already_enabled")
        ctx.eq(d["nsec3param"], "1 0 1 ab", "nsec3param in der Antwort")
        keys = _pdns_keys(ctx, SRV, zone)
        ctx.eq(len(keys), 1, "ein CSK")
        ctx.eq((keys[0]["keytype"], str(keys[0]["algorithm"]).upper(), keys[0]["bits"]), ("csk", "RSASHA256", 2048),
               "Schluessel in PowerDNS")
        ctx.eq(ctx.pdns.zone(SRV, zone).get("nsec3param"), "1 0 1 ab", "PowerDNS nsec3param")
        st = ctx.api("GET", f"{base}/status?peers=false", expect=200).json()
        codes = {h["code"] for h in st["hints"]}
        ctx.check({"nsec3_iterations_nonzero", "nsec3_salt"} <= codes, f"Hinweise zu Iteration/Salt fehlen: {codes}")
        old_id = keys[0]["id"]

    with ctx.step("Schluessel anlegen: veroeffentlicht und inaktiv (Pre-Publish)"):
        r = ctx.api("POST", f"{base}/keys", json={"keytype": "csk", "algorithm": "RSASHA256", "bits": 2048,
                                                  "active": False, "published": True}, expect=200)
        _no_private(ctx, r, "POST keys")
        new = r.json()["details"]["key"]
        ctx.eq((new["active"], new["published"]), (False, True), "neuer Schluessel in der Antwort")
        pk = {k["id"]: k for k in _pdns_keys(ctx, SRV, zone)}
        ctx.eq((pk[new["id"]]["active"], pk[new["id"]].get("published")), (False, True), "neuer Schluessel in PowerDNS")
        st = ctx.api("GET", f"{base}/status?peers=false", expect=200).json()
        ctx.eq(st["rollover"]["sep"]["phase"], "new_prepublished", "Rollover-Phase")
        ctx.eq(st["rollover"]["sep"]["new_key_id"], new["id"], "neuer Schluessel im Rollover")
        by_id = {k["id"]: k for k in st["keys"]}
        ctx.eq(by_id[new["id"]]["ds_status"], "new", "ds_status des neuen Schluessels")
        ctx.eq(by_id[new["id"]]["rollover_role"], "new", "rollover_role")

    with ctx.step("Neuen Schluessel aktivieren: Serial + NOTIFY (Master)"):
        serial0 = _serial(ctx, zone)
        d = ctx.api("PUT", f"{base}/keys/{new['id']}", json={"active": True}, expect=200).json()["details"]
        ctx.eq(d["serial_bumped"], True, "serial_bumped")
        ctx.eq(d["notified"], True, f"notified (Fehler: {d.get('notify_error')})")
        ctx.check(_serial(ctx, zone) > serial0, "SOA-Serial in PowerDNS nicht erhoeht")
        pk = {k["id"]: k for k in _pdns_keys(ctx, SRV, zone)}
        ctx.eq(pk[new["id"]]["active"], True, "neuer Schluessel aktiv in PowerDNS")
        st = ctx.api("GET", f"{base}/status?peers=false", expect=200).json()
        ctx.eq(st["rollover"]["sep"]["phase"], "both_active", "Rollover-Phase nach dem Aktivieren")

    with ctx.step("Ohne bump_serial: Serial bleibt"):
        serial1 = _serial(ctx, zone)
        d = ctx.api("PUT", f"{base}/keys/{old_id}", json={"active": False, "bump_serial": False},
                    expect=200).json()["details"]
        ctx.eq((d["serial_bumped"], d["notified"]), (False, False), "keine Folgeaktion")
        ctx.eq(_serial(ctx, zone), serial1, "Serial unveraendert")

    with ctx.step("NSEC3 aendern: 1 0 0 - mit Narrow"):
        d = ctx.api("PUT", f"{base}/nsec3", json={"nsec_mode": "nsec3", "nsec3_iterations": 0, "nsec3_salt": "-",
                                                  "nsec3_optout": False, "nsec3narrow": True},
                    expect=200).json()["details"]
        ctx.eq(d["unchanged"], False, "unchanged")
        meta = ctx.pdns.zone(SRV, zone)
        ctx.eq((meta.get("nsec3param"), meta.get("nsec3narrow")), ("1 0 0 -", True), "PowerDNS NSEC3")
        st = ctx.api("GET", f"{base}/status?peers=false", expect=200).json()
        ctx.eq((st["nsec"]["mode"], st["nsec"]["iterations"], st["nsec"]["narrow"]), ("nsec3", 0, True), "Status NSEC")

    with ctx.step("DS lesen: Status und /ds passen zu PowerDNS"):
        st_resp = ctx.api("GET", f"{base}/status", expect=200)
        _no_private(ctx, st_resp, "status")
        st = st_resp.json()
        pk = {k["id"]: k for k in _pdns_keys(ctx, SRV, zone)}
        current = [k for k in st["keys"] if k["ds_status"] == "current"]
        ctx.eq([k["id"] for k in current], [new["id"]], "aktueller SEP-Schluessel")
        lines = {d["ds"] for d in current[0]["ds"]}
        ctx.eq(lines, set(pk[new["id"]].get("ds") or []), "DS-Zeilen wie PowerDNS")
        ctx.check(any(d["digest_type"] == 2 and d["parsed"].get("key_tag") == current[0]["key_tag"]
                      for d in current[0]["ds"]), f"SHA-256-DS mit Key-Tag fehlt: {current[0]['ds']}")
        ds = ctx.api("GET", f"{base}/ds", expect=200).json()
        ctx.check(any(row.get("key_tag") == current[0]["key_tag"] and row.get("ds_status") == "current"
                      for row in ds["ds_records"]), f"/ds ohne aktuellen Schluessel: {ds['ds_records']}")

    with ctx.step("Zonenverlauf: SOA-Erhoehung (source=dnssec) sichtbar, nicht zurueckrollbar"):
        hist = ctx.api("GET", f"zones/{SRV}/{_z(zone)}/history?limit=100", expect=200).json()
        soa = [e for e in hist["entries"] if e["action"] == "UPDATE" and e["resource_type"] == "record"
               and any((c or {}).get("type") == "SOA" for c in (e.get("changes") or []))]
        ctx.check(soa, f"kein SOA-Eintrag im Verlauf: {[e['action'] for e in hist['entries']]}")
        ctx.check(all(not e["can_rollback"] for e in soa), f"SOA-Erhoehung zurueckrollbar: {soa[0]}")
        actions = {e["action"] for e in hist["entries"]}
        ctx.check({"DNSSEC_ENABLE", "KEY_CREATE", "KEY_ACTIVATE", "KEY_DEACTIVATE", "DNSSEC_NSEC3_UPDATE"} <= actions,
                  f"DNSSEC-Aktionen im Verlauf: {sorted(actions)}")

    with ctx.step("Alten Schluessel loeschen, DNSSEC deaktivieren"):
        ctx.api("DELETE", f"{base}/keys/{old_id}", expect=200)
        d = ctx.api("POST", f"{base}/disable", json={}, expect=200).json()["details"]
        ctx.eq([k["key_id"] for k in d["deleted_keys"]], [new["id"]], "geloeschte Schluessel")
        ctx.eq(_pdns_keys(ctx, SRV, zone), [], "Schluessel in PowerDNS nach dem Deaktivieren")
        ctx.eq(ctx.pdns.zone(SRV, zone).get("nsec3param", ""), "", "nsec3param nach dem Deaktivieren")


def check_fresh(ctx) -> None:
    _zone_create_with_options(ctx, "f4")
    _key_lifecycle(ctx, "f4-keys")


def check_upgrade(ctx) -> None:
    # Nach dem Upgrade einer 2.4.1-Datenbank: Zonenanlage mit DNSSEC (neuer Weg ueber dnssec_service) und
    # Schluessel-Lebenszyklus funktionieren unveraendert.
    _zone_create_with_options(ctx, "f4-up")
    _key_lifecycle(ctx, "f4-up-keys")
