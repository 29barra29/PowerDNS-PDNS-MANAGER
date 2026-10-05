"""Basis-Checks (Funktionsumfang 2.4.1): Server, Zonen, Records-CRUD mit Fan-out, Export, Zonenrechte,
Webhook-Anlage, Audit-Log; im Upgrade-Pfad: Alt-Daten aus seed_241.py weiter nutzbar.

Gehoert W0-TESTINFRA. Feature-Workstreams aendern diese Datei nicht, sondern liefern eigene
``checks/<ws>.py`` (siehe README.md).
"""
from __future__ import annotations

from urllib.parse import quote


def _z(zone: str) -> str:
    """Zonenname fuer Pfadsegmente (Punkt am Ende bleibt)."""
    return quote(zone, safe=".")


def _server_names(ctx) -> dict:
    data = ctx.api("GET", "servers", expect=200).json()
    return {s["name"]: s for s in data.get("servers", [])}


def _check_servers(ctx) -> None:
    servers = _server_names(ctx)
    for name in ctx.pdns.servers:
        ctx.check(name in servers, f"Server {name} fehlt in GET /servers ({sorted(servers)})")
        ctx.check(servers[name].get("is_reachable") is True, f"Server {name} nicht erreichbar: {servers[name]}")


def _delete_zone_everywhere(ctx, zone: str) -> None:
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.pdns.delete_zone(srv, zone)


def _webhook_delivery_info(ctx, hook_name: str, zone: str) -> None:
    """Zustellung ist in 2.4.1 defekt (Koroutine wird nicht awaited) – nur protokollieren.
    Die verbindliche Zustellungspruefung liefert F6-BE in checks/f6.py."""
    try:
        got = ctx.receiver.wait_for(hook_name, count=1, timeout=5,
                                    predicate=lambda d: zone in (d.get("body_text") or ""))
        ctx.log(f"Webhook-Zustellung erhalten ({len(got)}): {got[0].get('headers', {}).get('X-DNS-Manager-Event', '?')}")
    except Exception:  # noqa: BLE001 - reine Info
        ctx.log("keine Webhook-Zustellung innerhalb 5 s (2.4.1: Zustellung defekt; Pruefung ab F6-BE)")


# ---------------------------------------------------------------------------------------------
# Neuinstallation
# ---------------------------------------------------------------------------------------------
def check_fresh(ctx) -> None:
    zone = ctx.unique_zone("base")
    www = f"www.{zone}"
    ns = ["ns1.e2e.test.", "ns2.e2e.test."]
    ctx.cleanup(lambda: _delete_zone_everywhere(ctx, zone), "Zone entfernen")

    with ctx.step("Health und Server"):
        health = ctx.api("GET", "/health", token=None, expect=200).json()
        ctx.check(isinstance(health, dict), f"/health liefert kein Objekt: {health}")
        _check_servers(ctx)

    with ctx.step("Anmeldung: falsches Passwort und anonymer Zugriff"):
        resp, _ = ctx.login(ctx.admin_username, "falsch-" + ctx.unique(), expect_ok=False)
        ctx.eq(resp.status, 401, "Login mit falschem Passwort")
        ctx.api("GET", "zones/ns1", token=None, expect=401)
        ctx.api("GET", "auth/me", token="dnsmgr_usr_ungueltig", expect=401)
        me = ctx.api("GET", "auth/me", expect=200).json()
        ctx.eq(me.get("username"), ctx.admin_username, "auth/me per Admin-Token")

    with ctx.step("Zone anlegen (Fan-out auf alle schreibbaren Server)"):
        res = ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ns}, expect=200).json()
        for srv in ctx.pdns.servers:
            ctx.check(str(res.get("details", {}).get(srv, "")).startswith(("created", "synced")),
                      f"Zonenanlage auf {srv}: {res}")
            ctx.check(ctx.pdns.zone(srv, zone) is not None, f"Zone fehlt in PowerDNS {srv}")
        listed = ctx.api("GET", "zones/ns1", expect=200).json()
        names = [z.get("name") for z in listed.get("zones", [])]
        ctx.check(zone in names, f"Zone fehlt in GET /zones/ns1 ({len(names)} Zonen)")
        ctx.api("GET", f"zones/ns1/{_z(zone)}/detail", expect=200)

    with ctx.step("Records anlegen (zwei Werte, Fan-out)"):
        ctx.api("POST", f"records/ns1/{_z(zone)}",
                json={"name": www, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.10"}]}, expect=200)
        res = ctx.api("POST", f"records/ns1/{_z(zone)}",
                      json={"name": www, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.11"}]},
                      expect=200).json()
        ctx.check(all(res.get("details", {}).get(s) == "saved" for s in ctx.pdns.servers), f"Fan-out: {res}")
        ctx.api("POST", f"records/ns1/{_z(zone)}",
                json={"name": zone, "type": "TXT", "ttl": 3600, "records": [{"content": '"e2e base"'}]}, expect=200)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, www, "A"), ["192.0.2.10", "192.0.2.11"], f"A-Werte auf {srv}")
            ctx.eq(ctx.pdns.contents(srv, zone, zone, "TXT"), ['"e2e base"'], f"TXT auf {srv}")

    with ctx.step("Records lesen"):
        data = ctx.api("GET", f"records/ns1/{_z(zone)}", expect=200).json()
        got = sorted(r["content"] for r in data.get("records", []) if r.get("name") == www and r.get("type") == "A")
        ctx.eq(got, ["192.0.2.10", "192.0.2.11"], "GET /records")

    with ctx.step("Record aendern (Wert und TTL)"):
        ctx.api("PUT", f"records/ns1/{_z(zone)}", json={
            "name": www, "type": "A", "ttl": 600, "old_content": "192.0.2.11", "new_content": "192.0.2.12"},
            expect=200)
        for srv in ctx.pdns.servers:
            rr = ctx.pdns.rrset(srv, zone, www, "A") or {}
            ctx.eq(sorted(r["content"] for r in rr.get("records", [])), ["192.0.2.10", "192.0.2.12"],
                   f"A-Werte nach Update auf {srv}")
            ctx.eq(rr.get("ttl"), 600, f"TTL nach Update auf {srv}")
        ctx.api("PUT", f"records/ns1/{_z(zone)}", json={
            "name": www, "type": "A", "ttl": 600, "old_content": "198.51.100.99", "new_content": "192.0.2.13"},
            expect=404)

    with ctx.step("Records loeschen (einzelner Wert, ganzes RRset)"):
        ctx.api("DELETE", f"records/ns1/{_z(zone)}/delete",
                json={"name": www, "type": "A", "content": "192.0.2.10"}, expect=200)
        ctx.api("DELETE", f"records/ns1/{_z(zone)}/delete", json={"name": zone, "type": "TXT"}, expect=200)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, www, "A"), ["192.0.2.12"], f"A-Werte nach Loeschen auf {srv}")
            ctx.eq(ctx.pdns.contents(srv, zone, zone, "TXT"), [], f"TXT nach Loeschen auf {srv}")

    with ctx.step("Zonen-Export (BIND)"):
        exp = ctx.api("GET", f"zones/ns1/{_z(zone)}/export", expect=200).json()
        content = exp.get("content") or ""
        if isinstance(content, dict):
            # 2.4.1 reicht die JSON-Antwort von PowerDNS 4.9 durch: {"zone": "<Zonendatei>"}
            content = content.get("zone") or ""
        ctx.check(isinstance(content, str), f"Export-Inhalt hat unerwarteten Typ: {type(content).__name__}")
        ctx.check(www in content and "192.0.2.12" in content, f"Export ohne www-Record: {content[:300]}")
        ctx.check("SOA" in content, "Export ohne SOA")

    with ctx.step("Zonenrechte eines Benutzers (keine / read / manage)"):
        utok = ctx.user_token
        ctx.api("GET", f"records/ns1/{_z(zone)}", token=utok, expect=403)
        ctx.set_user_zones(ctx.user_id, {zone: "read"})
        ctx.api("GET", f"records/ns1/{_z(zone)}", token=utok, expect=200)
        ctx.api("POST", f"records/ns1/{_z(zone)}", token=utok, expect=403,
                json={"name": f"user.{zone}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.20"}]})
        ctx.set_user_zones(ctx.user_id, {zone: "manage"})
        ctx.api("POST", f"records/ns1/{_z(zone)}", token=utok, expect=200,
                json={"name": f"user.{zone}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.20"}]})
        ctx.api("POST", "zones", token=utok, json={"name": ctx.unique_zone("forbidden"), "kind": "Native",
                                                    "nameservers": ns}, expect=403)
        ctx.api("GET", "audit-log", token=utok, expect=403)
        ctx.set_user_zones(ctx.user_id, {})
        ctx.api("GET", f"records/ns1/{_z(zone)}", token=utok, expect=403)

    with ctx.step("Webhook anlegen, auflisten, loeschen"):
        hook_name = ctx.unique("base")
        created = ctx.admin_session.post("auth/me/webhooks", json={
            "name": hook_name, "url": ctx.receiver.url(hook_name), "events": ["*"]}, expect=(200, 201)).json()
        hook = created.get("webhook") or {}
        ctx.check(hook.get("id"), f"Webhook ohne ID: {created}")
        ctx.check(created.get("secret"), "Webhook-Secret wird bei der Anlage nicht einmalig angezeigt")
        ctx.cleanup(lambda: ctx.admin_session.delete(f"auth/me/webhooks/{hook['id']}"), "Webhook loeschen")
        listed = ctx.admin_session.get("auth/me/webhooks", expect=200).json().get("webhooks", [])
        ctx.check(any(w.get("id") == hook["id"] for w in listed), "Webhook fehlt in der Liste")
        ctx.api("POST", f"records/ns1/{_z(zone)}", expect=200,
                json={"name": f"hook.{zone}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.30"}]})
        _webhook_delivery_info(ctx, hook_name, zone)

    with ctx.step("Audit-Log enthaelt die Aenderungen"):
        entries = ctx.api("GET", "audit-log?limit=200", expect=200).json().get("entries", [])
        ctx.check(any(e.get("resource_type") == "zone" and e.get("resource_name") == zone
                      and e.get("action") == "CREATE" for e in entries), "kein Zone-CREATE im Audit-Log")
        for action in ("CREATE", "UPDATE", "DELETE"):
            ctx.check(any(e.get("resource_type") == "record" and e.get("action") == action
                          and zone in (e.get("resource_name") or "") for e in entries),
                      f"kein Record-{action} im Audit-Log")

    with ctx.step("Zone loeschen (je Server)"):
        for srv in ctx.pdns.servers:
            ctx.api("DELETE", f"zones/{srv}/{_z(zone)}", expect=200)
            ctx.check(ctx.pdns.zone(srv, zone) is None, f"Zone nach Loeschen noch auf {srv}")


# ---------------------------------------------------------------------------------------------
# Upgrade 2.4.1 -> aktueller Stand
# ---------------------------------------------------------------------------------------------
def _upgrade_common(ctx, phase: str) -> dict:
    seed = ctx.seed
    alpha, beta, gamma = seed["zones"]["alpha"], seed["zones"]["beta"], seed["zones"]["gamma"]
    alice = seed["users"]["alice"]
    facts: dict = {}

    with ctx.step(f"[{phase}] Server aus der 2.4.1-Datenbank geladen und erreichbar"):
        _check_servers(ctx)

    with ctx.step(f"[{phase}] Panel-Token des Admins: Audit-Log mit Alt-Eintraegen"):
        entries = ctx.api("GET", "audit-log?limit=500", expect=200).json().get("entries", [])
        ids = {e.get("id") for e in entries}
        missing = [i for i in seed["audit_ids"] if i not in ids]
        ctx.check(not missing, f"Alt-Audit-Zeilen fehlen: {missing}")
        facts["audit_rows"] = ctx.db_value("SELECT COUNT(*) AS n FROM audit_logs")

    with ctx.step(f"[{phase}] Panel-Token des Admins: Record anlegen und loeschen"):
        name = f"{ctx.unique('upg')}.{alpha}"
        ctx.cleanup(lambda: ctx.api("DELETE", f"records/ns1/{_z(alpha)}/delete", json={"name": name, "type": "A"}),
                    "Upgrade-Record entfernen")
        ctx.api("POST", f"records/ns1/{_z(alpha)}", expect=200,
                json={"name": name, "type": "A", "ttl": 300, "records": [{"content": "192.0.2.77"}]})
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, alpha, name, "A"), ["192.0.2.77"], f"Record auf {srv}")
        ctx.api("DELETE", f"records/ns1/{_z(alpha)}/delete", json={"name": name, "type": "A"}, expect=200)

    with ctx.step(f"[{phase}] Inaktiver Alt-Token wird abgelehnt"):
        ctx.api("GET", "auth/me", token=seed["tokens"]["admin_inactive"], expect=401)
        ctx.api("GET", "zones/ns1", token=seed["tokens"]["admin_inactive"], expect=401)

    with ctx.step(f"[{phase}] Token der Benutzerin: Zonenrechte aus 2.4.1"):
        tok = seed["tokens"]["alice"]
        me = ctx.api("GET", "auth/me", token=tok, expect=200).json()
        ctx.eq(me.get("username"), "alice", "auth/me per Alice-Token")
        ctx.api("GET", f"records/ns1/{_z(alpha)}", token=tok, expect=200)
        ctx.api("GET", f"records/ns1/{_z(beta)}", token=tok, expect=200)
        ctx.api("GET", f"records/ns1/{_z(gamma)}", token=tok, expect=403)
        ctx.api("POST", f"records/ns1/{_z(beta)}", token=tok, expect=403,
                json={"name": f"x.{beta}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.5"}]})
        ctx.api("GET", "audit-log", token=tok, expect=403)
        zones = [z.get("name") for z in ctx.api("GET", "zones/ns1", token=tok, expect=200).json().get("zones", [])]
        ctx.check(alpha in zones and beta in zones and gamma not in zones, f"Zonenliste der Benutzerin: {zones}")

    with ctx.step(f"[{phase}] Login mit TOTP (2.4.1-Geheimnis)"):
        resp, sess = ctx.login("alice", alice["password"], alice["totp_secret"])
        me = sess.get("auth/me", expect=200).json()
        ctx.eq(me.get("username"), "alice", "auth/me nach TOTP-Login")
        ctx.check(me.get("totp_enabled") is True, f"totp_enabled nicht gesetzt: {me}")
        bad, _ = ctx.login("alice", alice["password"] + "x", expect_ok=False)
        ctx.eq(bad.status, 401, "Login mit falschem Passwort (TOTP-Nutzerin)")

    with ctx.step(f"[{phase}] Admin-Login und Alt-Webhooks"):
        hooks = ctx.admin_session.get("auth/me/webhooks", expect=200).json().get("webhooks", [])
        names = {h.get("name") for h in hooks}
        ctx.check("seed-admin" in names, f"Alt-Webhook des Admins fehlt: {sorted(names)}")

    with ctx.step(f"[{phase}] Backend-Log ohne Traceback"):
        log = ctx.backend_log()
        ctx.check(log != "", "Backend-Log nicht verfuegbar")
        ctx.check("Traceback" not in log, "Traceback im Backend-Log:\n" + log[log.find("Traceback"):][:1500])
    return facts


def check_upgrade(ctx) -> None:
    facts = _upgrade_common(ctx, "erster Start")
    ctx.state["base"] = facts


def check_upgrade_restart(ctx) -> None:
    before = (ctx.state.get("base") or {}).get("audit_rows")
    _upgrade_common(ctx, "zweiter Start")
    with ctx.step("[zweiter Start] Alt-Audit-Zeilen unveraendert vorhanden"):
        ids = [r["id"] for r in ctx.db("SELECT id FROM audit_logs WHERE id IN %s", (tuple(ctx.seed["audit_ids"]),))]
        ctx.eq(sorted(ids), sorted(ctx.seed["audit_ids"]), "Alt-Audit-IDs")
        if before is not None:
            now = ctx.db_value("SELECT COUNT(*) AS n FROM audit_logs")
            ctx.check(now >= before, f"Audit-Zeilen verschwunden: vorher {before}, jetzt {now}")
