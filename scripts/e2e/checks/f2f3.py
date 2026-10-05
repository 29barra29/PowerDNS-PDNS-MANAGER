"""E2E-Checks fuer WS-F2F3 (F2 NOTIFY/Export, F3 Admin-Benutzersicherheit, F0-Zonenfixes, Zugangs-Widerruf [S9]).

Neuinstallation:
- Zone mit zwei Nameservern anlegen: SOA-rname ist ``hostmaster.<zone>`` (nicht der zweite NS), Serial != 0;
  ``POST /zones`` mit explizitem ``servers`` respektiert "Speichern: Nein" (403 bzw. ``skipped (read-only)``).
- Export (Dateiname, Inhalt, Audit ``ZONE_EXPORT``), NOTIFY (Native -> 422 mit Erklaerung, Master -> 200,
  Audit ``ZONE_NOTIFY``), Leserecht: Export ja, NOTIFY nein.
- Benutzer mit erzwungenem Passwortwechsel anlegen, Gate (403 + Header), Wechsel, Admin-Zufallspasswort,
  Reset-Link ohne SMTP (400), 2FA-Reset ohne 2FA, ``access-summary`` und ``revoke-access`` (Token -> 401).
Upgrade:
- ``users.must_change_password`` existiert und ist fuer alle Altkonten 0; Altkonten erscheinen mit Zaehlern;
  Export/NOTIFY mit den Rechten von ``alice`` (alpha=manage, beta=read); Widerruf an einem Wegwerf-Konto.
"""
from __future__ import annotations

import secrets
from urllib.parse import quote

NOTIFY_FAILED = "NOTIFY fehlgeschlagen:"
GATE_HEADER = "x-password-change-required"


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _detail(resp) -> str:
    try:
        return str(resp.json().get("detail", ""))
    except Exception:  # noqa: BLE001
        return resp.text


def _delete_zone_everywhere(ctx, zone: str) -> None:
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.pdns.delete_zone(srv, zone)


def _audit_count(ctx, action: str, resource_name: str) -> int:
    return int(ctx.db_value("SELECT COUNT(*) FROM audit_logs WHERE action=%s AND resource_name=%s",
                            (action, resource_name)) or 0)


def _delete_user(ctx, user_id) -> None:
    ctx.admin_session.delete(f"auth/users/{user_id}")


def _temp_user(ctx, prefix: str, *, must_change: bool) -> tuple[int, str, str]:
    name = ctx.unique(prefix)
    password = "E2e-F3-" + secrets.token_urlsafe(9)
    created = ctx.admin_session.post("auth/users", json={
        "username": name, "password": password, "email": f"{name}@e2e.test", "role": "user",
        "must_change_password": must_change,
    }, expect=201).json()
    ctx.cleanup(lambda: _delete_user(ctx, created["id"]), f"Benutzer {name} loeschen")
    ctx.eq(created.get("must_change_password"), must_change, "must_change_password in der Antwort")
    return created["id"], name, password


def _password_flow(ctx, prefix: str) -> tuple[int, str, str]:
    """Benutzer mit Zwangswechsel anlegen, Gate pruefen, Passwort wechseln. Liefert (id, name, neues Passwort)."""
    uid, name, password = _temp_user(ctx, prefix, must_change=True)
    resp, sess = ctx.login(name, password)
    ctx.eq(resp.json()["user"]["must_change_password"], True, "Login meldet must_change_password")
    blocked = sess.get("servers")
    ctx.eq(blocked.status, 403, "Gate blockiert Seiten-Requests")
    ctx.eq(blocked.headers.get(GATE_HEADER), "1", "Header X-Password-Change-Required")
    sess.get("auth/me", expect=200)
    same = sess.put("auth/me/password", json={"current_password": password, "new_password": password}, expect=400)
    ctx.eq(same.json().get("code"), "password_unchanged", "gleiches Passwort wird abgelehnt (code)")
    new_pw = "E2e-F3-neu-" + secrets.token_urlsafe(9)
    sess.put("auth/me/password", json={"current_password": password, "new_password": new_pw}, expect=200)
    sess.get("servers", expect=200)
    return uid, name, new_pw


def _revoke_flow(ctx, uid: int, name: str, password: str) -> None:
    sess = ctx.session(name, password)
    tok = sess.post("auth/me/panel-tokens", json={"name": ctx.unique("f2f3")}, expect=201).json()["plaintext_token"]
    ctx.api("GET", "auth/me", token=tok, expect=200)
    summary = ctx.api("GET", f"auth/users/{uid}/access-summary", expect=200).json()  # Admin-Token (allow_admin)
    ctx.eq(summary.get("panel_tokens"), 1, "access-summary zaehlt den Token")
    # Mutation nur per Browser-Session, nicht per Admin-Token
    r = ctx.api("POST", f"auth/users/{uid}/revoke-access", json={})
    ctx.eq(r.status, 403, "revoke-access per API-Token verboten")
    res = ctx.admin_session.post(f"auth/users/{uid}/revoke-access", json={}, expect=200).json()
    ctx.eq(res["revoked"]["panel_tokens"], 1, "ein Panel-Token widerrufen")
    ctx.eq(res["summary"]["panel_tokens"], 0, "Zaehler danach 0")
    ctx.api("GET", "auth/me", token=tok, expect=401)
    revoked_at = ctx.db_value("SELECT COUNT(*) FROM panel_tokens WHERE user_id=%s AND revoked_at IS NOT NULL", (uid,))
    ctx.eq(int(revoked_at or 0), 1, "revoked_at gesetzt (nicht nur is_active=0)")
    ctx.check(int(ctx.db_value("SELECT COUNT(*) FROM audit_logs WHERE action='USER_ACCESS_REVOKE' AND resource_name=%s",
                               (name,)) or 0) >= 1, "Audit USER_ACCESS_REVOKE fehlt")


def _admin_tools(ctx, uid: int, name: str) -> None:
    res = ctx.admin_session.put(f"auth/users/{uid}/reset-password", expect=200).json()
    ctx.eq(len(res["new_password"]), 16, "Zufallspasswort mit 16 Zeichen")
    ctx.eq(res["must_change_password"], True, "ohne Body wird der Wechsel erzwungen")
    resp, _ = ctx.login(name, res["new_password"])
    ctx.eq(resp.json()["user"]["must_change_password"], True, "Flag nach Admin-Reset gesetzt")
    r = ctx.admin_session.post(f"auth/users/{uid}/reset-2fa", json={}, expect=200).json()
    ctx.eq(r["changed"], False, "2FA war nicht aktiv")
    # Ohne SMTP/Basis-URL: 400 mit erklaerendem Text (welcher Teil fehlt, haengt von der Umgebung ab)
    link = ctx.admin_session.post(f"auth/users/{uid}/send-reset-link", json={})
    ctx.eq(link.status, 400, "Reset-Link ohne SMTP")
    ctx.check(any(s in _detail(link) for s in ("SMTP", "Basis-URL")), f"Text Reset-Link: {_detail(link)}")
    ctx.check(_audit_count(ctx, "USER_PASSWORD_RESET", name) >= 1, "Audit USER_PASSWORD_RESET fehlt")
    # Selbstschutz des Admins
    me = ctx.admin_session.get("auth/me", expect=200).json()
    r = ctx.admin_session.put(f"auth/users/{me['id']}", json={"is_active": False}, expect=400)
    ctx.eq(_detail(r), "Du kannst dich nicht selbst deaktivieren", "Selbst-Deaktivierung")


def check_fresh(ctx) -> None:
    zone = ctx.unique_zone("f2f3")
    master = ctx.unique_zone("f2f3-m")
    ro_zone = ctx.unique_zone("f2f3-ro")
    for z in (zone, master, ro_zone):
        ctx.cleanup(lambda z=z: _delete_zone_everywhere(ctx, z), f"Zone {z} entfernen")
    ns = ["ns1.e2e.test.", "ns2.e2e.test."]

    with ctx.step("Zone mit zwei Nameservern: SOA-rname hostmaster, Serial != 0 (F0)"):
        ctx.admin_session.post("zones", json={"name": zone, "kind": "Native", "nameservers": ns}, expect=200)
        for srv in ctx.pdns.servers:
            soa = ctx.pdns.contents(srv, zone, zone, "SOA")
            ctx.check(soa, f"{srv}: kein SOA")
            parts = soa[0].split()
            ctx.eq(parts[0], "ns1.e2e.test.", f"{srv}: mname")
            ctx.eq(parts[1], f"hostmaster.{zone}", f"{srv}: rname")
            ctx.check(int(parts[2]) > 0, f"{srv}: Serial ist 0")

    with ctx.step("POST /zones mit explizitem servers respektiert 'Speichern: Nein' (F0)"):
        servers = ctx.admin_session.get("settings/servers", expect=200).json()
        rows = servers.get("servers", servers) if isinstance(servers, dict) else servers
        ns2 = next((s for s in rows if s.get("name") == "ns2"), None)
        if ns2 is None:
            ctx.log("Server ns2 nicht in /settings/servers – Teilpruefung uebersprungen")
        else:
            def restore():
                ctx.admin_session.put(f"settings/servers/{ns2['id']}", json={"allow_writes": True})
            ctx.cleanup(restore, "ns2 wieder schreibbar")
            ctx.admin_session.put(f"settings/servers/{ns2['id']}", json={"allow_writes": False}, expect=200)
            try:
                r = ctx.admin_session.post("zones", json={"name": ro_zone, "kind": "Native", "nameservers": ns,
                                                          "servers": ["ns2"]}, expect=403)
                ctx.check("Speichern" in _detail(r), f"403-Text: {_detail(r)}")
                ctx.check(ctx.pdns.zone("ns2", ro_zone) is None, "Zone trotz 'Speichern: Nein' auf ns2 angelegt")
                res = ctx.admin_session.post("zones", json={"name": ro_zone, "kind": "Native", "nameservers": ns,
                                                            "servers": ["ns1", "ns2"]}, expect=200).json()
                ctx.eq(res["details"].get("ns2"), "skipped (read-only)", "ns2 uebersprungen")
                ctx.eq(res["details"].get("ns1"), "created", "ns1 angelegt")
            finally:
                restore()

    with ctx.step("Export: Dateiname, Inhalt, Audit ZONE_EXPORT"):
        res = ctx.api("GET", f"zones/ns1/{_z(zone)}/export", expect=200).json()
        ctx.eq(res["filename"], zone.rstrip(".") + ".txt", "filename")
        ctx.eq(res["format"], "bind", "format")
        ctx.check("SOA" in res["content"], "Export ohne SOA")
        ctx.check(_audit_count(ctx, "ZONE_EXPORT", zone) >= 1, "Audit ZONE_EXPORT fehlt")

    with ctx.step("NOTIFY: Native -> 422 mit Erklaerung, Master -> 200, Audit"):
        r = ctx.api("POST", f"zones/ns1/{_z(zone)}/notify", json={}, expect=422)
        ctx.check(_detail(r).startswith(NOTIFY_FAILED), f"422-Text: {_detail(r)}")
        ctx.check(int(ctx.db_value(
            "SELECT COUNT(*) FROM audit_logs WHERE action='ZONE_NOTIFY' AND resource_name=%s AND status='error'",
            (zone,)) or 0) >= 1, "Fehler-Audit ZONE_NOTIFY fehlt")
        ctx.admin_session.post("zones", json={"name": master, "kind": "Master", "nameservers": ns}, expect=200)
        r = ctx.api("POST", f"zones/ns1/{_z(master)}/notify", json={}, expect=200)
        ctx.check("NOTIFY" in r.json().get("message", ""), "Erfolgsmeldung")
        ctx.check(_audit_count(ctx, "ZONE_NOTIFY", master) >= 1, "Audit ZONE_NOTIFY fehlt")

    with ctx.step("Leserecht: Export ja, NOTIFY nein; ohne Recht beides 403"):
        ctx.api("GET", f"zones/ns1/{_z(master)}/export", token=ctx.user_token, expect=403)
        ctx.set_user_zones(ctx.user_id, {master: "read"})
        ctx.cleanup(lambda: ctx.set_user_zones(ctx.user_id, {}), "Zonenrechte e2e-user zuruecksetzen")
        ctx.api("GET", f"zones/ns1/{_z(master)}/export", token=ctx.user_token, expect=200)
        r = ctx.api("POST", f"zones/ns1/{_z(master)}/notify", json={}, token=ctx.user_token, expect=403)
        ctx.eq(_detail(r), "Nur Lese-Zugriff auf diese Zone", "403-Text")

    with ctx.step("Benutzer mit erzwungenem Passwortwechsel"):
        uid, name, password = _password_flow(ctx, "f3-fresh")

    with ctx.step("E-Mail-Duplikat -> 409"):
        r = ctx.admin_session.post("auth/users", json={
            "username": ctx.unique("f3-dup"), "password": "E2e-dup-123456", "email": f"{name}@e2e.test"}, expect=409)
        ctx.eq(_detail(r), "E-Mail wird bereits verwendet", "409-Text")

    with ctx.step("Zugaenge widerrufen ([S9])"):
        _revoke_flow(ctx, uid, name, password)

    with ctx.step("Admin-Werkzeuge: Zufallspasswort, 2FA-Reset, Reset-Link ohne SMTP, Selbstschutz"):
        _admin_tools(ctx, uid, name)

    with ctx.step("Benutzerliste: Zaehler und Mail-Flag"):
        listed = ctx.api("GET", "auth/users", expect=200).json()
        ctx.check(isinstance(listed.get("password_reset_mail_available"), bool), "password_reset_mail_available")
        row = next((u for u in listed["users"] if u["id"] == uid), None)
        ctx.check(row is not None, "Benutzer fehlt in der Liste")
        for key in ("passkey_count", "panel_token_count", "must_change_password"):
            ctx.check(key in row, f"Feld {key} fehlt")


def check_upgrade(ctx) -> None:
    alpha = ctx.seed["zones"]["alpha"]
    beta = ctx.seed["zones"]["beta"]

    with ctx.step("Spalte users.must_change_password: Altkonten 0"):
        cols = ctx.db("SHOW COLUMNS FROM users LIKE 'must_change_password'")
        ctx.check(cols, "Spalte must_change_password fehlt")
        ctx.eq(int(ctx.db_value("SELECT COUNT(*) FROM users WHERE must_change_password <> 0") or 0), 0,
               "kein Altkonto muss sein Passwort wechseln")

    with ctx.step("Benutzerliste mit Zaehlern (Altkonten)"):
        listed = ctx.api("GET", "auth/users", expect=200).json()
        alice = next((u for u in listed["users"] if u["id"] == ctx.user_id), None)
        ctx.check(alice is not None, "alice fehlt")
        ctx.eq(alice["must_change_password"], False, "alice ohne Zwangswechsel")
        ctx.check(alice["panel_token_count"] >= 1, "alice: aktiver Panel-Token wird gezaehlt")
        summary = ctx.api("GET", f"auth/users/{ctx.user_id}/access-summary", expect=200).json()
        ctx.check(summary["panel_tokens"] >= 1, "access-summary alice")

    with ctx.step("Export/NOTIFY mit den Rechten von alice (alpha=manage, beta=read)"):
        res = ctx.api("GET", f"zones/ns1/{_z(beta)}/export", token=ctx.user_token, expect=200).json()
        ctx.eq(res["filename"], beta.rstrip(".") + ".txt", "filename beta")
        r = ctx.api("POST", f"zones/ns1/{_z(beta)}/notify", json={}, token=ctx.user_token, expect=403)
        ctx.eq(_detail(r), "Nur Lese-Zugriff auf diese Zone", "NOTIFY mit Leserecht")
        # alpha ist eine Native-Zone aus 2.4.1: NOTIFY wird mit erklaerendem 422 abgelehnt
        r = ctx.api("POST", f"zones/ns1/{_z(alpha)}/notify", json={}, token=ctx.user_token)
        ctx.check(r.status in (200, 422), f"NOTIFY alpha: HTTP {r.status}")
        if r.status == 422:
            ctx.check(_detail(r).startswith(NOTIFY_FAILED), f"422-Text: {_detail(r)}")

    with ctx.step("Neues Konto: Zwangswechsel und Widerruf nach dem Upgrade"):
        uid, name, password = _password_flow(ctx, "f3-upg")
        _revoke_flow(ctx, uid, name, password)
