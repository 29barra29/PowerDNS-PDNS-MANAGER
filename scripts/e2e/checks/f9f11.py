"""E2E-Checks fuer F9/F11 (WS-F9F11-BE): DynDNS (/nic/update, JSON-API) und PTR-Pflege gegen die E2E-PowerDNS.

Neuinstallation:
- Token-Verwaltung und Einstellungen nur per Browser-Session (Admin-Panel-Token -> 403, anonym -> 401).
- Forward-Zone und zwei Reverse-Zonen (alte und neue IP) auf ns1/ns2; DynDNS-Token mit ``update_ptr`` per
  Admin-Session (Klartext nur einmal, in der DB nur der Hash).
- ``/nic/update`` mit Basic-Auth: ``good`` -> A-Record auf ns1 und ns2, alter PTR entfernt, neuer PTR gesetzt;
  derselbe Ping -> ``nochg`` ohne neues Audit. Drift auf pdns2 (an PDNS Manager vorbei): der naechste Ping repariert
  nur ns2, Antwort ``nochg``, Audit ``DYNDNS_UPDATE`` mit ``repair`` [D5].
- JSON-API mit Bearer, ``whoami``, PTR-Lookup, Cross-Site -> 403, Token im Query -> 401 und Token gesperrt,
  Rotation, DynDNS global aus -> 403, Benutzer ohne Zonenrecht -> 403 ohne Zonennamen [S5].
Upgrade: Tabelle ``dyndns_tokens`` leer, Defaults der Einstellungen; ``alice`` (alpha=manage, beta=read) legt einen
Token fuer alpha an (403 fuer beta mit, fuer gamma ohne Zonennamen) und aktualisiert per ``/nic/update`` – alpha
bleibt auf ns1/ns2 inhaltsgleich (Fan-out auf beide, Aufraeumen ueber die Panel-API).
"""
from __future__ import annotations

import base64
import json
from urllib.parse import quote, urlencode

OLD_IP, NEW_IP, NEXT_IP = "198.51.100.1", "203.0.113.5", "203.0.113.6"
REV_OLD, REV_NEW = "100.51.198.in-addr.arpa.", "113.0.203.in-addr.arpa."


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _basic(token: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"dyndns:{token}".encode()).decode()}


def _nic(ctx, token: str | None, headers: dict | None = None, **params):
    q = urlencode({k: v for k, v in params.items() if v is not None})
    hdrs = dict(_basic(token) if token else {})
    hdrs.update(headers or {})
    return ctx.api("GET", "/nic/update" + (f"?{q}" if q else ""), token=None, headers=hdrs)


def _details(row) -> dict:
    d = row["details"]
    return json.loads(d) if isinstance(d, (str, bytes)) else (d or {})


def _audits(ctx, action: str, name: str) -> list[dict]:
    rows = ctx.db("SELECT id, details, status, server_name, zone_name FROM audit_logs WHERE action = %s "
                  "AND resource_name = %s ORDER BY id", (action, name))
    return rows


def _check_session_only(ctx) -> None:
    with ctx.step("Token-Verwaltung und Einstellungen nur per Browser-Session"):
        for path in ("dyndns/tokens", "dyndns/zones", "dyndns/admin/tokens", "settings/dyndns", "settings/ptr"):
            ctx.api("GET", path, expect=403)  # Admin-Panel-Token (allow_admin) reicht nicht
            ctx.api("GET", path, token=None, expect=401)
        ctx.api("POST", "dyndns/tokens", json={"name": "x", "hostnames": ["a.example.com"]}, expect=403)
        ctx.api("PUT", "settings/ptr", json={"auto_default": True}, expect=403)
        info = ctx.api("GET", "dyndns/info", expect=200).json()
        ctx.check(info["update_path"] == "/nic/update" and "proxy_warning" in info, f"dyndns/info: {info}")


def _create_zone(ctx, zone: str, rrsets: list[tuple[str, str, str, int]] = ()) -> None:
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.fail(f"Zone {zone} existiert schon auf {srv} (Rest eines frueheren Laufs?)")
    ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ["ns1.e2e.test."]}, expect=200)
    for name, rtype, content, ttl in rrsets:
        ctx.api("POST", f"records/ns1/{_z(zone)}", json={"name": name, "type": rtype, "ttl": ttl,
                                                         "records": [{"content": content}]}, expect=200)


def _drop_zone(ctx, zone: str) -> None:
    ctx.api("DELETE", f"zones/ns1/{_z(zone)}")
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.pdns.delete_zone(srv, zone)


def check_fresh(ctx) -> None:
    _check_session_only(ctx)
    sess = ctx.admin_session

    with ctx.step("Default-Einstellungen"):
        st = sess.get("settings/dyndns", expect=200).json()
        ctx.eq((st["enabled"], st["allow_private_ips"]), (True, False), "DynDNS-Defaults")
        ctx.eq(sess.get("settings/ptr", expect=200).json(), {"auto_default": False}, "PTR-Default")

    zone = ctx.unique_zone("f9f11")
    host = f"home.{zone}"
    for z in (zone, REV_OLD, REV_NEW):
        ctx.cleanup(lambda z=z: _drop_zone(ctx, z), f"Zone {z} entfernen")
    with ctx.step("Forward- und Reverse-Zonen auf ns1/ns2 anlegen"):
        _create_zone(ctx, zone, [(host, "A", OLD_IP, 60)])
        _create_zone(ctx, REV_OLD, [(f"1.{REV_OLD}", "PTR", host, 3600)])
        _create_zone(ctx, REV_NEW)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, host, "A"), [OLD_IP], f"home A auf {srv}")
        cfg = ctx.api("GET", "ptr/config", expect=200).json()
        ctx.check(cfg["reverse_zones_available"] >= 2, f"ptr/config: {cfg}")
        zones = [z["name"] for z in sess.get("dyndns/zones", expect=200).json()["zones"]]
        ctx.check(zone in zones and REV_NEW in zones, f"dyndns/zones ohne {zone}: {zones}")

    with ctx.step("DynDNS-Token anlegen (Klartext nur einmal, DB nur Hash)"):
        created = sess.post("dyndns/tokens", json={"name": "E2E-Router", "hostnames": [host], "ttl": 60,
                                                   "update_ptr": True}, expect=201).json()
        token = created["plaintext_token"]
        tid = created["token"]["id"]
        ctx.cleanup(lambda: sess.delete(f"dyndns/tokens/{tid}"), "DynDNS-Token loeschen")
        ctx.check(token.startswith("dnsmgr_ddns_") and created["token"]["token_prefix"] == token[:16],
                  f"Token-Format: {created['token']['token_prefix']}")
        row = ctx.db("SELECT * FROM dyndns_tokens WHERE id = %s", (tid,))[0]
        ctx.check(token not in json.dumps(row, default=str), "Klartext-Token in der Datenbank")
        ctx.eq(row["is_active"], 1, "is_active")
        audit = ctx.db("SELECT details FROM audit_logs WHERE action = 'DYNDNS_TOKEN_CREATE' ORDER BY id DESC LIMIT 1")
        ctx.check(audit and token not in str(audit[0]["details"]), "Klartext-Token im Audit-Log")
        listed = sess.get("dyndns/tokens", expect=200).json()["tokens"]
        ctx.check(any(t["id"] == tid and t["hostname_status"][0]["status"] == "ok" for t in listed),
                  f"Token-Liste: {listed}")

    with ctx.step("/nic/update ohne Token -> 401 badauth"):
        r = _nic(ctx, None, hostname=host, myip=NEW_IP)
        ctx.eq((r.status, r.text), (401, "badauth"), "ohne Token")
        ctx.check(r.headers.get("www-authenticate", "").startswith("Basic"), f"WWW-Authenticate: {r.headers}")
        r = _nic(ctx, "dnsmgr_ddns_falsch", hostname=host, myip=NEW_IP)
        ctx.eq((r.status, r.text), (401, "badauth"), "falscher Token")

    with ctx.step("/nic/update -> good, A auf ns1/ns2, PTR umgezogen"):
        r = _nic(ctx, token, hostname=host, myip=NEW_IP)
        ctx.eq((r.status, r.text), (200, f"good {NEW_IP}"), "Antwort")
        ctx.eq(r.headers.get("cache-control"), "no-store", "Cache-Control")
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, host, "A"), [NEW_IP], f"home A auf {srv}")
            ctx.eq(ctx.pdns.rrset(srv, zone, host, "A")["ttl"], 60, f"TTL auf {srv}")
            ctx.eq(ctx.pdns.contents(srv, REV_NEW, f"5.{REV_NEW}", "PTR"), [host], f"neuer PTR auf {srv}")
            ctx.eq(ctx.pdns.contents(srv, REV_OLD, f"1.{REV_OLD}", "PTR"), [], f"alter PTR auf {srv}")
        upd = _audits(ctx, "DYNDNS_UPDATE", host)
        ctx.eq(len(upd), 1, "DYNDNS_UPDATE-Audits")
        d = _details(upd[0])
        ctx.eq((d.get("version"), d.get("zone"), upd[0]["zone_name"]), (2, zone, zone), "Audit v2")
        ctx.check(d["changes"] and d["fanout"] == {"ns1": "saved", "ns2": "saved"}, f"Audit-Details: {d}")
        ctx.check([p["action"] for p in d.get("ptr", [])] == ["removed", "set"], f"PTR im Audit: {d.get('ptr')}")
        ptr_rows = ctx.db("SELECT zone_name FROM audit_logs WHERE action = 'PTR_SYNC' AND zone_name IN (%s, %s)",
                          (REV_OLD, REV_NEW))
        ctx.eq(sorted(r["zone_name"] for r in ptr_rows), sorted([REV_OLD, REV_NEW]), "PTR_SYNC je Reverse-Zone")
        tok = ctx.db("SELECT last_result, last_ip_v4, stale_servers FROM dyndns_tokens WHERE id = %s", (tid,))[0]
        ctx.eq((tok["last_result"], tok["last_ip_v4"], tok["stale_servers"]), ("good", NEW_IP, None), "Token-Status")

    with ctx.step("gleicher Ping -> nochg ohne Audit"):
        r = _nic(ctx, token, hostname=host, myip=NEW_IP)
        ctx.eq((r.status, r.text), (200, f"nochg {NEW_IP}"), "Antwort")
        ctx.eq(len(_audits(ctx, "DYNDNS_UPDATE", host)), 1, "kein neues Audit bei nochg")

    with ctx.step("[D5] Drift auf pdns2 wird beim naechsten Ping repariert (nochg + Audit repair)"):
        ctx.pdns.request("ns2", "PATCH", f"/zones/{zone}", json_body={"rrsets": [{
            "name": host, "type": "A", "ttl": 60, "changetype": "REPLACE",
            "records": [{"content": OLD_IP, "disabled": False}]}]}, expect=(200, 204))
        r = _nic(ctx, token, hostname=host, myip=NEW_IP)
        ctx.eq((r.status, r.text), (200, f"nochg {NEW_IP}"), "Antwort nach Drift")
        ctx.eq(ctx.pdns.contents("ns2", zone, host, "A"), [NEW_IP], "ns2 repariert")
        upd = _audits(ctx, "DYNDNS_UPDATE", host)
        ctx.eq(len(upd), 2, "Reparatur-Audit")
        d = _details(upd[-1])
        ctx.check(d.get("repair") is True and d.get("fanout") == {"ns2": "saved"}, f"Reparatur-Details: {d}")
        ctx.eq(upd[-1]["server_name"], "ns2", "Server des Reparatur-Audits")

    with ctx.step("JSON-API mit Bearer, whoami, PTR-Lookup"):
        r = ctx.api("GET", f"dyndns/update?{urlencode({'hostname': host, 'myip': NEXT_IP})}", token=token, expect=200)
        body = r.json()
        ctx.eq(body["result"], "good", f"JSON-Ergebnis {body}")
        h0 = body["hosts"][0]
        ctx.eq((h0["zone"], h0["changes"][0]["old"], h0["changes"][0]["new"]), (zone, [NEW_IP], NEXT_IP), "Host")
        ctx.check([p["action"] for p in h0["ptr"]] == ["removed", "set"], f"PTR {h0['ptr']}")
        who = ctx.api("GET", "dyndns/whoami", token=token, expect=200).json()
        ctx.eq((who["ok"], who["hostnames"]), (True, [host]), "whoami")
        lk = ctx.api("GET", f"ptr/lookup?{urlencode({'ip': NEXT_IP, 'name': host})}", expect=200).json()
        ctx.eq((lk["status"], lk["zone"], lk["would"]), ("ok", REV_NEW, "unchanged"), f"PTR-Lookup {lk}")
        ctx.api("GET", "ptr/lookup?ip=1.2.3", expect=422)

    with ctx.step("Cross-Site-Browser -> 403"):
        r = _nic(ctx, token, headers={"Sec-Fetch-Site": "cross-site"}, hostname=host, myip=NEXT_IP)
        ctx.eq((r.status, r.text), (403, "badauth"), "Cross-Site")

    with ctx.step("DynDNS global aus -> 403"):
        sess.put("settings/dyndns", json={"enabled": False}, expect=200)
        try:
            r = _nic(ctx, token, hostname=host, myip=NEXT_IP)
            ctx.eq((r.status, r.text), (403, "badauth"), "deaktiviert")
        finally:
            sess.put("settings/dyndns", json={"enabled": True}, expect=200)
        audit = ctx.db_value("SELECT COUNT(*) FROM audit_logs WHERE action = 'DYNDNS_SETTINGS_UPDATE'")
        ctx.check(audit >= 2, "DYNDNS_SETTINGS_UPDATE fehlt")

    with ctx.step("Token im Query -> 401, Token gesperrt; Rotation + Reaktivierung"):
        r = _nic(ctx, None, hostname=host, myip=NEXT_IP, password=token)
        ctx.eq((r.status, r.text), (401, "badauth"), "Token im Query")
        ctx.eq(ctx.db_value("SELECT is_active FROM dyndns_tokens WHERE id = %s", (tid,)), 0, "Token deaktiviert")
        ctx.check(ctx.db_value("SELECT COUNT(*) FROM audit_logs WHERE action = 'DYNDNS_TOKEN_REVOKED'") >= 1,
                  "DYNDNS_TOKEN_REVOKED fehlt")
        ctx.eq(_nic(ctx, token, hostname=host, myip=NEXT_IP).status, 401, "gesperrter Token")
        new_token = sess.post(f"dyndns/tokens/{tid}/rotate", json={}, expect=200).json()["plaintext_token"]
        sess.put(f"dyndns/tokens/{tid}", json={"is_active": True}, expect=200)
        ctx.eq(_nic(ctx, token, hostname=host, myip=NEXT_IP).status, 401, "alter Token nach Rotation")
        r = _nic(ctx, new_token, hostname=host, myip=NEXT_IP)
        ctx.eq((r.status, r.text), (200, f"nochg {NEXT_IP}"), "neuer Token")

    with ctx.step("[S5] Benutzer ohne Zonenrecht: 403 ohne Zonennamen"):
        r = ctx.user_session.post("dyndns/tokens", json={"name": "fremd", "hostnames": [f"x.{zone}"]}, expect=403)
        ctx.check(f"Zone {zone}" not in r.text, f"403 verraet die Zone: {r.text}")

    with ctx.step("Token loeschen"):
        sess.delete(f"dyndns/tokens/{tid}", expect=200)
        ctx.eq(ctx.db_value("SELECT COUNT(*) FROM dyndns_tokens WHERE id = %s", (tid,)), 0, "Token geloescht")
        ctx.eq(_nic(ctx, new_token, hostname=host, myip=NEXT_IP).status, 401, "geloeschter Token")


def check_upgrade(ctx) -> None:
    _check_session_only(ctx)
    zones = ctx.seed.get("zones", {})
    if not {"alpha", "beta", "gamma"} <= set(zones):
        ctx.fail(f"Seed ohne Zonen alpha/beta/gamma: {sorted(zones)}")
    alpha, beta, gamma = zones["alpha"], zones["beta"], zones["gamma"]

    with ctx.step("Tabelle und Defaults nach dem Upgrade"):
        cols = {r["Field"] for r in ctx.db("SHOW COLUMNS FROM dyndns_tokens")}
        ctx.check({"token_hash", "hostnames", "stale_servers", "update_ptr"} <= cols, f"Spalten: {sorted(cols)}")
        ctx.eq(ctx.db_value("SELECT COUNT(*) FROM dyndns_tokens"), 0, "keine Tokens nach dem Upgrade")
        st = ctx.admin_session.get("settings/dyndns", expect=200).json()
        ctx.eq((st["enabled"], st["allow_private_ips"]), (True, False), "DynDNS-Defaults")

    alice = ctx.user_session
    host = f"dyn-{ctx.unique('up')}.{alpha}"
    with ctx.step("alice: Token fuer alpha, 403 fuer beta (mit) und gamma (ohne Zonennamen)"):
        r = alice.post("dyndns/tokens", json={"name": "b", "hostnames": [f"x.{beta}"]}, expect=403)
        ctx.check(beta in r.text, f"beta (Leserecht) sollte genannt werden: {r.text}")
        r = alice.post("dyndns/tokens", json={"name": "g", "hostnames": [f"x.{gamma}"]}, expect=403)
        ctx.check(f"Zone {gamma}" not in r.text, f"gamma (kein Recht) wird genannt: {r.text}")
        created = alice.post("dyndns/tokens", json={"name": "alice-router", "hostnames": [host]}, expect=201).json()
        token, tid = created["plaintext_token"], created["token"]["id"]

    def _cleanup():
        alice.delete(f"dyndns/tokens/{tid}")
        ctx.api("DELETE", f"records/ns1/{_z(alpha)}/delete", json={"name": host, "type": "A"})

    ctx.cleanup(_cleanup, "Token und Record entfernen")
    with ctx.step("/nic/update in der Seed-Zone alpha (beide Server)"):
        r = _nic(ctx, token, hostname=host, myip=NEW_IP)
        ctx.eq((r.status, r.text), (200, f"good {NEW_IP}"), "Antwort")
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, alpha, host, "A"), [NEW_IP], f"A auf {srv}")
        ctx.eq(_nic(ctx, token, hostname=host, myip=NEW_IP).text, f"nochg {NEW_IP}", "zweiter Ping")
        row = _audits(ctx, "DYNDNS_UPDATE", host)
        ctx.check(row and _details(row[0]).get("token_name") == "alice-router", f"Audit: {row}")
