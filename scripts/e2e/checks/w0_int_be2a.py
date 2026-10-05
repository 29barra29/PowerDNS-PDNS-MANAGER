"""E2E-Checks fuer W0-INT-BE2a (Auth-Durchsetzung F14, Passwortwechsel-Gate F3, umgezogene Router).

Neuinstallation: eingeschraenkter Lese-Token (Zonen-Scope, Ablauf) ueber die Session anlegen und pruefen,
dass er nur lesen darf, nur seine Zone sieht, keine Session-Endpunkte erreicht und nach dem Widerruf 401
bekommt; Admin-Funktionen nur mit ``allow_admin``; Panel-Token im Cookie wird abgelehnt; erzwungener
Passwortwechsel blockiert die Session bis ``PUT /auth/me/password``.
Upgrade: Alt-Tokens aus 2.4.1 behalten ihre Wirkung fuer DNS (Admin-Token mit ``allow_admin``), verlieren
aber den Zugriff auf Session-Pflicht-Endpunkte (Settings, Token-Verwaltung).

Alle Verwaltungsaufrufe laufen ueber die Admin-Session (``ctx.admin_session``), damit die Checks nicht
von den Rechten des Bootstrap-Tokens abhaengen.
"""
from __future__ import annotations

import secrets
from urllib.parse import quote

READ_ONLY = "Dieser API-Token hat nur Leserechte"
SESSION_ONLY = "Diese Aktion ist mit einem API-Token nicht erlaubt – bitte im Browser anmelden"
NO_ADMIN = "Dieser API-Token hat keine Admin-Rechte"


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _detail(resp) -> str:
    try:
        return str(resp.json().get("detail", ""))
    except Exception:  # noqa: BLE001
        return resp.text


def _create_token(ctx, **body) -> dict:
    return ctx.admin_session.post("auth/me/panel-tokens", json=body, expect=201).json()


def _revoke(ctx, token_id: int) -> None:
    ctx.admin_session.delete(f"auth/me/panel-tokens/{token_id}")


def _delete_zone_everywhere(ctx, zone: str) -> None:
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.pdns.delete_zone(srv, zone)


def check_fresh(ctx) -> None:
    zone = ctx.unique_zone("be2a")
    other = ctx.unique_zone("be2a-other")
    ns = ["ns1.e2e.test.", "ns2.e2e.test."]
    for z in (zone, other):
        ctx.cleanup(lambda z=z: _delete_zone_everywhere(ctx, z), f"Zone {z} entfernen")

    with ctx.step("Zonen ueber die Admin-Session anlegen"):
        for z in (zone, other):
            ctx.admin_session.post("zones", json={"name": z, "kind": "Native", "nameservers": ns}, expect=200)

    with ctx.step("Lese-Token mit Zonen-Scope anlegen (Session)"):
        created = _create_token(ctx, name=ctx.unique("be2a-read"), scope_zones=[zone.rstrip(".").upper()],
                                permission="read", expires_in_days=1)
        tok = created["plaintext_token"]
        info = created["token"]
        ctx.cleanup(lambda: _revoke(ctx, info["id"]), "Lese-Token widerrufen")
        ctx.eq(info["scope_zones"], [zone], "Scope wird normalisiert gespeichert")
        ctx.eq((info["permission"], info["status"], info["allow_admin"]), ("read", "active", False), "Token-Felder")
        ctx.check(info["expires_at"], "Ablaufdatum fehlt")
        ctx.check(tok not in str(info), "Klartext steht im Token-Objekt")

    with ctx.step("GET /auth/me zeigt den Token-Scope"):
        me = ctx.api("GET", "auth/me", token=tok, expect=200).json()
        auth = me.get("auth") or {}
        ctx.eq(auth.get("via"), "panel_token", "auth.via")
        ctx.eq((auth.get("token") or {}).get("scope_zones"), [zone], "auth.token.scope_zones")
        ctx.check(tok not in str(me), "Klartext in /auth/me")

    with ctx.step("Lese-Token: lesen ja, schreiben nein, fremde Zone nein"):
        ctx.api("GET", f"records/ns1/{_z(zone)}", token=tok, expect=200)
        r = ctx.api("POST", f"records/ns1/{_z(zone)}", token=tok, expect=403,
                    json={"name": f"x.{zone}", "type": "A", "ttl": 300, "records": [{"content": "192.0.2.9"}]})
        ctx.eq(_detail(r), READ_ONLY, "Methodenregel fuer Lese-Token")
        ctx.eq(ctx.pdns.contents("ns1", zone, f"x.{zone}", "A"), [], "Lese-Token hat geschrieben")
        r = ctx.api("GET", f"records/ns1/{_z(other)}", token=tok, expect=403)
        ctx.check("nicht freigegeben" in _detail(r), f"Scope-Fehlertext: {_detail(r)}")

    with ctx.step("Server-Liste: Zonenzahl gefiltert, keine interne URL"):
        servers = {s["name"]: s for s in ctx.api("GET", "servers", token=tok, expect=200).json()["servers"]}
        ctx.eq(servers["ns1"].get("zone_count"), 1, "zone_count fuer Zonen-Token")
        ctx.check("url" not in servers["ns1"], "url wird an Nicht-Admins ausgegeben")
        r = ctx.api("GET", "servers/ns1/statistics", token=tok, expect=403)
        ctx.check("Statistiken" in _detail(r), f"Statistik-Fehlertext: {_detail(r)}")

    with ctx.step("Suche nur in der eigenen Zone"):
        hits = ctx.api("GET", "search/ns1?q=be2a", token=tok, expect=200).json()
        zones_hit = {str(h.get("zone_id") or h.get("zone") or "").lower() for h in hits.get("results", [])}
        ctx.check(zones_hit <= {zone}, f"Suche liefert fremde Zonen: {zones_hit}")
        ctx.check("truncated" in hits, "Suchantwort ohne truncated")

    with ctx.step("Admin-Funktionen nur mit allow_admin"):
        plain = _create_token(ctx, name=ctx.unique("be2a-noadm"))
        manage_tok = plain["plaintext_token"]
        ctx.cleanup(lambda: _revoke(ctx, plain["token"]["id"]), "Token ohne Admin widerrufen")
        r = ctx.api("GET", "audit-log", token=manage_tok, expect=403)
        ctx.check(_detail(r).startswith(NO_ADMIN), f"Text ohne allow_admin: {_detail(r)}")
        ctx.api("GET", f"records/ns1/{_z(zone)}", token=manage_tok, expect=200)
        adm = _create_token(ctx, name=ctx.unique("be2a-adm"), allow_admin=True)
        ctx.cleanup(lambda: _revoke(ctx, adm["token"]["id"]), "Admin-Token widerrufen")
        ctx.api("GET", "audit-log?limit=5", token=adm["plaintext_token"], expect=200)
        ctx.admin_session.post("auth/me/panel-tokens", expect=400,
                               json={"name": "x", "allow_admin": True, "scope_zones": [zone]})

    with ctx.step("Session-Pflicht-Endpunkte lehnen Tokens ab (auch mit allow_admin)"):
        for method, path, body in (("GET", "auth/me/panel-tokens", None), ("GET", "settings/servers", None),
                                   ("GET", "auth/me/totp/status", None),
                                   ("POST", "auth/me/webhooks", {"name": "x", "url": "https://example.com/x"}),
                                   ("POST", "auth/me/panel-tokens", {"name": "x"})):
            kw = {"json": body} if body is not None else {}
            r = ctx.api(method, path, token=adm["plaintext_token"], expect=403, **kw)
            ctx.eq(_detail(r), SESSION_ONLY, f"{method} {path}")
        # Lese-Token: die Methodenregel greift vor der Session-Pruefung
        r = ctx.api("POST", "auth/me/panel-tokens", token=tok, json={"name": "x"}, expect=403)
        ctx.eq(_detail(r), READ_ONLY, "Lese-Token an Session-Endpunkt")

    with ctx.step("Panel-Token im Cookie wird abgelehnt"):
        r = ctx.http(label="cookie").request("GET", "auth/me", headers={"Cookie": f"dns_manager_token={tok}"})
        ctx.eq(r.status, 401, "Panel-Token aus dem Cookie")

    with ctx.step("Widerruf: Token sofort ungueltig und nicht mehr gelistet"):
        _revoke(ctx, info["id"])
        r = ctx.api("GET", "auth/me", token=tok, expect=401)
        ctx.eq(_detail(r), "Ungültiger API-Token", "Widerrufener Token")
        listed = ctx.admin_session.get("auth/me/panel-tokens", expect=200).json()["tokens"]
        ctx.check(all(t["id"] != info["id"] for t in listed), "Widerrufener Token wird noch gelistet")
        row = ctx.db("SELECT revoked_at, is_active FROM panel_tokens WHERE id = %s", (info["id"],))
        ctx.check(row and row[0]["revoked_at"] is not None and not row[0]["is_active"], f"DB-Zustand: {row}")

    with ctx.step("Erzwungener Passwortwechsel (F3-Gate)"):
        name = ctx.unique("be2a-gate")
        pw = "Gate-" + secrets.token_urlsafe(12)
        created_user = ctx.admin_session.post("auth/users", expect=(200, 201), json={
            "username": name, "password": pw, "role": "user"}).json()
        uid = created_user.get("id") or (created_user.get("user") or {}).get("id")
        ctx.cleanup(lambda: ctx.admin_session.delete(f"auth/users/{uid}"), "Gate-Benutzer loeschen")
        ctx.db("UPDATE users SET must_change_password = 1 WHERE id = %s", (uid,))
        sess = ctx.session(name, pw)
        r = sess.get("servers", expect=403)
        ctx.eq(r.headers.get("x-password-change-required"), "1", "Header X-Password-Change-Required")
        me = sess.get("auth/me", expect=200).json()
        ctx.check(me.get("must_change_password") is True, "must_change_password fehlt in /auth/me")
        new_pw = pw + "-neu"
        sess.put("auth/me/password", json={"current_password": pw, "new_password": new_pw}, expect=200)
        sess.get("servers", expect=200)
        ctx.eq(ctx.db_value("SELECT must_change_password FROM users WHERE id = %s", (uid,)), 0, "Flag geloescht")


def check_upgrade(ctx) -> None:
    seed = ctx.seed
    with ctx.step("Alt-Token des Admins: allow_admin, alle Zonen, Lesen & Schreiben"):
        me = ctx.api("GET", "auth/me", expect=200).json()
        token = (me.get("auth") or {}).get("token") or {}
        ctx.eq((me["auth"]["via"], token.get("allow_admin"), token.get("scope_zones"), token.get("permission")),
               ("panel_token", True, None, "manage"), "Scope des migrierten Admin-Tokens")
        ctx.api("GET", "audit-log?limit=5", expect=200)
    with ctx.step("Alt-Token von alice: keine Admin-Freigabe"):
        me = ctx.api("GET", "auth/me", token=seed["tokens"]["alice"], expect=200).json()
        ctx.eq(((me.get("auth") or {}).get("token") or {}).get("allow_admin"), False, "alice allow_admin")
    with ctx.step("Session-Pflicht gilt auch fuer Alt-Tokens (Breaking Change 3.0)"):
        for path in ("settings/servers", "auth/me/panel-tokens"):
            r = ctx.api("GET", path, expect=403)
            ctx.eq(_detail(r), SESSION_ONLY, f"GET {path} mit Alt-Admin-Token")


def check_upgrade_restart(ctx) -> None:
    with ctx.step("Nach dem Neustart: Admin-Alt-Token unveraendert nutzbar"):
        me = ctx.api("GET", "auth/me", expect=200).json()
        ctx.eq(((me.get("auth") or {}).get("token") or {}).get("allow_admin"), True, "allow_admin nach Neustart")
