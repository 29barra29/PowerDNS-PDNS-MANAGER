"""E2E-Checks fuer F15 (LUA- und Geo-Records) gegen die E2E-PowerDNS (``enable-lua-records=yes``, scripts/e2e/pdns.conf).

Neuinstallation:
- ``/settings/lua`` nur per Browser-Session (Panel-Token -> 403, anonym -> 401), Default-Policy ``admin``.
- ``/lua/server-status?refresh=true``: ns1/ns2 melden ``lua_records = "yes"``; keine anderen pdns.conf-Werte, kein
  API-Key und keine URL in der Antwort. Benutzer ohne Zonenrecht -> 403 [S5], mit Zonenrecht -> 200.
- LUA-Record per API anlegen (Inhalt wird normalisiert), auf ns1/ns2 gespeichert, PowerDNS beantwortet die Anfrage
  per DNS mit der Adresse aus dem Lua-Code (echte Auswertung). Ungueltiger Inhalt und ``TYPE65402`` -> 422.
- Export -> Import-Vorschau: LUA-Zeile wird verstanden, keine Schein-Unterschiede (``lua_count`` = 1).
- Policy ``admin``: Nicht-Admin mit Zonenrecht -> 403 (vor jedem PowerDNS-Zugriff), Policy ``manage`` -> erlaubt.
- Policy ``disabled``: Anlegen 403 (auch Admin), Import-Vorschau ``lua_blocked``/``lua_blocked_lines``, Import 403
  ohne Zone (auch ``IN IN LUA``), Loeschen bleibt erlaubt; ``LUA_SETTINGS_UPDATE`` im Audit-Log (from/to).
Upgrade: kein ``lua_records_policy``-Key -> ``admin``; ``alice`` (alpha=manage) darf keinen LUA-Record anlegen,
sieht aber den Server-Status; Admin legt in einer eigenen Zone einen LUA-Record an, der per DNS antwortet.
"""
from __future__ import annotations

import json
import socket
from urllib.parse import quote

MSG_DENIED_ADMIN = "LUA-Records dürfen nur Administratoren anlegen oder ändern."
MSG_DENIED_DISABLED = "LUA-Records sind in diesem Panel deaktiviert (Einstellungen → DNS-Optionen)."
MSG_IMPORT_BLOCKED = "Die Zonendatei enthält LUA-Records, LUA-Records sind in diesem Panel deaktiviert."
STATUS_KEYS = {"name", "reachable", "lua_records", "geoip_backend", "edns_subnet_processing", "exec_limit",
               "health_checks_interval", "error", "checked_at"}


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _lua(ip: str) -> str:
    """Deterministischer LUA-Inhalt: genau eine Adresse."""
    return f"A \"pickrandom({{'{ip}'}})\""


def _dns_a(ctx, host: str, name: str) -> list[str]:
    """A-Antwort von ``host`` (pdns1/pdns2, Port 53) fuer ``name``; [] bei leerer Antwort oder voruebergehendem
    Fehler (Timeout, Aufloesung) – ``ctx.wait_until`` wiederholt dann."""
    import dns.exception
    import dns.message
    import dns.query
    import dns.rdatatype

    try:
        ip = socket.gethostbyname(host)
        resp = dns.query.udp(dns.message.make_query(name, "A"), ip, timeout=5, port=53)
    except (OSError, dns.exception.DNSException) as exc:
        ctx.log(f"DNS-Abfrage {name} @{host}: {type(exc).__name__}")
        return []
    return sorted(rd.to_text() for rrset in resp.answer if rrset.rdtype == dns.rdatatype.A for rd in rrset)


def _create_zone(ctx, prefix: str) -> str:
    zone = ctx.unique_zone(prefix)

    def _drop():
        ctx.api("DELETE", f"zones/ns1/{_z(zone)}")
        for srv in ctx.pdns.servers:
            if ctx.pdns.zone(srv, zone) is not None:
                ctx.pdns.delete_zone(srv, zone)

    ctx.cleanup(_drop, f"Zone {zone} loeschen")
    ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ["ns1.e2e.test."]}, expect=200)
    return zone


def _create_lua(ctx, zone: str, name: str, content: str, *, token=..., expect=200):
    kw = {} if token is ... else {"token": token}
    return ctx.api("POST", f"records/ns1/{_z(zone)}", json={
        "name": name, "type": "LUA", "ttl": 60, "records": [{"content": content}]}, expect=expect, **kw)


def _set_policy(ctx, policy: str) -> None:
    ctx.admin_session.put("settings/lua", json={"policy": policy}, expect=200)


def _check_lua_answer(ctx, zone: str, name: str, ip: str) -> None:
    for srv, host in (("ns1", "pdns1"), ("ns2", "pdns2")):
        ctx.eq(ctx.pdns.contents(srv, zone, name, "LUA"), [_lua(ip)], f"LUA-RRset auf {srv}")
        got = ctx.wait_until(lambda h=host: _dns_a(ctx, h, name) or None, timeout=15, interval=1,
                             msg=f"{host} beantwortet {name} A nicht")
        ctx.eq(got, [ip], f"DNS-Antwort von {host} (LUA ausgewertet)")


def _check_status(ctx, body: dict) -> None:
    servers = {s["name"]: s for s in body.get("servers", [])}
    ctx.check({"ns1", "ns2"} <= set(servers), f"Server fehlen im Status: {body}")
    for name in ("ns1", "ns2"):
        s = servers[name]
        ctx.check(set(s) <= STATUS_KEYS, f"unerwartete Felder im Status {name}: {sorted(set(s) - STATUS_KEYS)}")
        ctx.eq(s["reachable"], True, f"{name} erreichbar")
        ctx.eq(s["lua_records"], "yes", f"{name}: enable-lua-records")
        ctx.eq(s["error"], None, f"{name}: kein Fehler")


def check_fresh(ctx) -> None:
    with ctx.step("Einstellungen nur per Browser-Session, Default-Policy admin"):
        ctx.api("GET", "settings/lua", expect=403)  # Admin-Panel-Token (allow_admin) reicht nicht
        ctx.api("GET", "settings/lua", token=None, expect=401)
        ctx.api("PUT", "settings/lua", json={"policy": "manage"}, expect=403)
        st = ctx.admin_session.get("settings/lua", expect=200).json()
        ctx.eq(st["policy"], "admin", "Default-Policy")
        ctx.eq(st["max_content_length"], 4000, "max_content_length")
        ctx.check("A" in st["target_types"] and "NS" not in st["target_types"], f"target_types: {st}")
        ctx.admin_session.put("settings/lua", json={"policy": "alle"}, expect=422)
        pol = ctx.api("GET", "lua/policy", expect=200).json()
        ctx.check(pol["policy"] == "admin" and pol["can_write"] is True, f"Admin-Token: {pol}")
        pol = ctx.api("GET", "lua/policy", token=ctx.user_token, expect=200).json()
        ctx.check(pol["can_write"] is False and pol["reason"] == MSG_DENIED_ADMIN, f"Benutzer-Token: {pol}")
    ctx.cleanup(lambda: _set_policy(ctx, "admin"), "LUA-Policy zuruecksetzen")

    with ctx.step("Server-Status: nur Whitelist-Werte, Zonenrecht noetig"):
        r = ctx.api("GET", "lua/server-status?refresh=true", expect=200)
        _check_status(ctx, r.json())
        for leak in (ctx.pdns.api_key("ns1"), ctx.pdns.api_key("ns2"), ":8081", "api-key", "webserver"):
            ctx.check(leak not in r.text, f"Status enthaelt {leak!r}")
        ctx.api("GET", "lua/server-status", token=ctx.user_token, expect=403)
        ctx.api("GET", "lua/server-status", token=None, expect=401)

    zone = _create_zone(ctx, "f15")
    name = f"geo.{zone}"
    with ctx.step("LUA-Record anlegen (Admin), gespeichert auf ns1/ns2, PowerDNS wertet ihn aus"):
        res = _create_lua(ctx, zone, name, "a   \"pickrandom({'192.0.2.50'})\"").json()
        ctx.eq(res.get("details"), {"ns1": "saved", "ns2": "saved"}, "Fan-out")
        _check_lua_answer(ctx, zone, name, "192.0.2.50")

    with ctx.step("Ungueltige Inhalte und Generic-Typ werden abgelehnt"):
        r = _create_lua(ctx, zone, f"bad.{zone}", "A \"pickrandom({'192.0.2.1')\"", expect=422)
        ctx.check("Klammern im LUA-Code" in r.text, f"422-Text: {r.text}")
        r = ctx.api("POST", f"records/ns1/{_z(zone)}", json={"name": f"gen.{zone}", "type": "TYPE65402", "ttl": 60,
                                                              "records": [{"content": _lua("192.0.2.9")}]}, expect=422)
        ctx.check("Generische Typangaben" in r.text, f"422-Text: {r.text}")
        ctx.check(ctx.pdns.rrset("ns1", zone, f"gen.{zone}", "LUA") is None, "TYPE65402 trotzdem gespeichert")

    with ctx.step("Export -> Import-Vorschau ohne Unterschiede"):
        export = ctx.api("GET", f"zones/ns1/{_z(zone)}/export", expect=200).json()["content"]
        ctx.check(" LUA" in export or "\tLUA" in export, f"Export ohne LUA-Zeile: {export[:400]}")
        p = ctx.api("POST", "zones/import/preview", json={"name": zone, "content": export}, expect=200).json()
        ctx.eq(p["parse_error"], None, "parse_error")
        ctx.eq(p["lua_count"], 1, "lua_count")
        ctx.eq(p["lua_issues"], [], "lua_issues")
        ctx.eq((p["would_add_total"], p["would_remove_total"]), (0, 0), f"Schein-Unterschiede: {p}")
        ctx.check(p["lua_policy"] == "admin" and p["lua_blocked"] is False, f"Policy-Felder: {p}")

    with ctx.step("Policy admin: Benutzer mit Zonenrecht -> 403, Status ja; Policy manage -> erlaubt"):
        ctx.set_user_zones(ctx.user_id, {zone: "manage"})
        ctx.cleanup(lambda: ctx.set_user_zones(ctx.user_id, {}), "Zonenrechte e2e-user zuruecksetzen")
        serial = (ctx.pdns.zone("ns1", zone) or {}).get("serial")
        r = _create_lua(ctx, zone, f"user.{zone}", _lua("192.0.2.60"), token=ctx.user_token, expect=403)
        ctx.eq(r.json().get("detail"), MSG_DENIED_ADMIN, "403-Text")
        ctx.eq((ctx.pdns.zone("ns1", zone) or {}).get("serial"), serial, "nichts geschrieben")
        ctx.api("GET", "lua/server-status?refresh=true", expect=200)  # Admin fuellt den Cache frisch
        st = ctx.api("GET", "lua/server-status?refresh=true", token=ctx.user_token, expect=200).json()
        ctx.eq(st["cached"], True, "refresh fuer Nicht-Admins ignoriert (Cache)")
        _set_policy(ctx, "manage")
        _create_lua(ctx, zone, f"user.{zone}", _lua("192.0.2.60"), token=ctx.user_token, expect=200)
        ctx.eq(ctx.pdns.contents("ns2", zone, f"user.{zone}", "LUA"), [_lua("192.0.2.60")], "LUA vom Benutzer auf ns2")

    with ctx.step("Policy disabled: Anlegen und Import gesperrt, Loeschen erlaubt, Audit"):
        _set_policy(ctx, "disabled")
        rows = ctx.db("SELECT details FROM audit_logs WHERE action = 'LUA_SETTINGS_UPDATE' ORDER BY id DESC LIMIT 1")
        ctx.check(rows, "kein Audit LUA_SETTINGS_UPDATE")
        d = rows[0]["details"]
        d = json.loads(d) if isinstance(d, (str, bytes)) else d
        ctx.eq(d.get("changed"), {"policy": {"from": "manage", "to": "disabled"}}, "Audit-Details")
        r = _create_lua(ctx, zone, f"off.{zone}", _lua("192.0.2.70"), expect=403)
        ctx.eq(r.json().get("detail"), MSG_DENIED_DISABLED, "403-Text (Admin)")
        pol = ctx.api("GET", "lua/policy", expect=200).json()
        ctx.check(pol["can_write"] is False and pol["policy"] == "disabled", f"Policy: {pol}")
        imp_zone = ctx.unique_zone("f15-imp")
        content = (f"$ORIGIN {imp_zone}\n@ 3600 IN SOA ns1.e2e.test. hostmaster.e2e.test. 1 10800 3600 604800 3600\n"
                   f"@ 3600 IN NS ns1.e2e.test.\nwww 60 IN LUA {_lua('192.0.2.80')}\n")
        p = ctx.api("POST", "zones/import/preview", json={"name": imp_zone, "content": content}, expect=200).json()
        ctx.check(p["lua_blocked"] is True and p["lua_count"] == 1, f"Vorschau: {p}")
        ctx.eq(p.get("lua_blocked_lines"), [4], "Vorschau: gesperrte Zeilen")
        r = ctx.api("POST", "zones/import", json={"name": imp_zone, "content": content,
                                                  "nameservers": ["ns1.e2e.test."]}, expect=403)
        ctx.eq(r.json().get("detail"), f"{MSG_IMPORT_BLOCKED} Betroffene Zeilen: 4.", "Import-403")
        # Varianten, die PowerDNS als LUA liest, der fruehere Panel-Zaehler aber nicht (Review Welle 3)
        for variant in (f"www 60 IN IN LUA {_lua('192.0.2.81')}", f"www IN 60 IN LUA {_lua('192.0.2.82')}"):
            alt = content.replace(f"www 60 IN LUA {_lua('192.0.2.80')}", variant)
            p = ctx.api("POST", "zones/import/preview", json={"name": imp_zone, "content": alt}, expect=200).json()
            ctx.check(p["lua_blocked"] is True and p.get("lua_blocked_lines") == [4], f"Vorschau ({variant}): {p}")
            ctx.api("POST", "zones/import", json={"name": imp_zone, "content": alt,
                                                  "nameservers": ["ns1.e2e.test."]}, expect=403)
        for srv in ctx.pdns.servers:
            ctx.check(ctx.pdns.zone(srv, imp_zone) is None, f"Zone trotz Sperre auf {srv} angelegt")
        ctx.api("DELETE", f"records/ns1/{_z(zone)}/delete",
                json={"name": f"user.{zone}", "type": "LUA", "content": _lua("192.0.2.60")}, expect=200)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, f"user.{zone}", "LUA"), [], f"LUA nach dem Loeschen auf {srv}")
        _set_policy(ctx, "admin")


def check_upgrade(ctx) -> None:
    with ctx.step("Kein Policy-Key nach dem Upgrade -> admin"):
        n = ctx.db_value("SELECT COUNT(*) FROM system_settings WHERE `key` = 'lua_records_policy'")
        ctx.eq(int(n or 0), 0, "lua_records_policy darf nach dem Upgrade fehlen")
        pol = ctx.api("GET", "lua/policy", expect=200).json()
        ctx.check(pol["policy"] == "admin" and pol["can_write"] is True, f"Alt-Admin-Token: {pol}")

    alpha = (ctx.seed.get("zones") or {}).get("alpha")
    if ctx.user_token and alpha:
        with ctx.step("alice (alpha=manage): LUA-Anlage 403, Server-Status sichtbar"):
            before = ctx.pdns.zone("ns1", alpha)
            r = _create_lua(ctx, alpha, f"lua.{alpha}", _lua("192.0.2.90"), token=ctx.user_token, expect=403)
            ctx.eq(r.json().get("detail"), MSG_DENIED_ADMIN, "403-Text")
            ctx.eq(ctx.pdns.zone("ns1", alpha).get("serial"), before.get("serial"), "alpha unveraendert")
            ctx.api("GET", "lua/server-status", token=ctx.user_token, expect=200)

    zone = _create_zone(ctx, "f15-up")
    with ctx.step("Admin legt nach dem Upgrade einen LUA-Record an, PowerDNS wertet ihn aus"):
        _check_status(ctx, ctx.api("GET", "lua/server-status?refresh=true", expect=200).json())
        _create_lua(ctx, zone, f"www.{zone}", _lua("192.0.2.91"))
        _check_lua_answer(ctx, zone, f"www.{zone}", "192.0.2.91")
