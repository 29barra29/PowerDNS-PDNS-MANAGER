"""E2E-Checks fuer WS-F14-APP (Panel-Token-Verwaltung: Bearbeiten, Admin-Sicht, Zaehler).

Neuinstallation: ein eigener Nicht-Admin (nicht der Bootstrap-Benutzer) legt ueber seine Session einen Token fuer eine eigene Zone an (fremde Zone und
Admin-Freigabe werden abgelehnt), pausiert ihn (401), aktiviert ihn wieder, aendert Berechtigung und Ablauf per
``PUT`` (Wirkung am Token pruefbar); ein Token kann die Verwaltung selbst nicht aufrufen. Der Admin sieht die
Tokens des Benutzers (``GET /auth/users/{id}/panel-tokens``), ``panel_token_count`` in der Benutzerliste und
widerruft einzeln und gesammelt (Audit ``PANEL_TOKEN_ADMIN_REVOKE``).
Upgrade: Der migrierte Admin-Alt-Token hat ``allow_admin = 1`` und erscheint als "weitreichend" (alle Zonen, kein
Ablauf); der inaktive Alt-Token ist widerrufen (``revoked_at``), taucht in keiner Liste auf und laesst sich per
``PUT`` nicht reaktivieren. Neue Tokens lassen sich auf der migrierten Datenbank anlegen, bearbeiten, widerrufen.

Die Bootstrap-/Alt-Tokens anderer Module (``ctx.admin_token``, ``seed.tokens``) werden nie veraendert.
"""
from __future__ import annotations

from urllib.parse import quote

SESSION_ONLY = "Diese Aktion ist mit einem API-Token nicht erlaubt – bitte im Browser anmelden"
READ_ONLY = "Dieser API-Token hat nur Leserechte"
PAUSED = "API-Token ist deaktiviert"
INVALID = "Ungültiger API-Token"


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


def _token_count(ctx, user_id: int) -> int:
    users = ctx.admin_session.get("auth/users", expect=200).json()["users"]
    return next(int(u.get("panel_token_count", -1)) for u in users if u["id"] == user_id)


def _create_user(ctx) -> tuple[int, str, object]:
    """Eigener Nicht-Admin fuer dieses Modul (der Bootstrap-Benutzer e2e-user und sein Token bleiben unberuehrt)."""
    import secrets

    name = ctx.unique("f14-user")
    pw = "F14-" + secrets.token_urlsafe(12)
    created = ctx.admin_session.post("auth/users", expect=(200, 201), json={
        "username": name, "password": pw, "role": "user", "must_change_password": False}).json()
    uid = created.get("id") or (created.get("user") or {}).get("id")
    ctx.check(uid, f"Benutzer-ID fehlt: {created}")
    ctx.cleanup(lambda: ctx.admin_session.delete(f"auth/users/{uid}"), f"Benutzer {name} loeschen")
    return uid, name, ctx.session(name, pw)


def check_fresh(ctx) -> None:
    zone = ctx.unique_zone("f14")
    other = ctx.unique_zone("f14-fremd")
    ns = ["ns1.e2e.test.", "ns2.e2e.test."]
    for z in (zone, other):
        ctx.cleanup(lambda z=z: _delete_zone_everywhere(ctx, z), f"Zone {z} entfernen")

    with ctx.step("Zonen und eigenen Nicht-Admin anlegen, Benutzer bekommt nur eine Zone"):
        for z in (zone, other):
            ctx.admin_session.post("zones", json={"name": z, "kind": "Native", "nameservers": ns}, expect=200)
        uid, uname, user = _create_user(ctx)
        ctx.set_user_zones(uid, {zone: "manage"})

    with ctx.step("Nicht-Admin: fremde Zone und Admin-Freigabe werden abgelehnt"):
        r = user.post("auth/me/panel-tokens", json={"name": "x", "scope_zones": [other]}, expect=403)
        ctx.check(other in _detail(r), f"Fehlertext nennt die eigene Eingabe: {_detail(r)}")
        user.post("auth/me/panel-tokens", json={"name": "x", "allow_admin": True}, expect=403)

    with ctx.step("Nicht-Admin: Lese-Token fuer die eigene Zone (30 Tage)"):
        created = user.post("auth/me/panel-tokens", expect=201, json={
            "name": ctx.unique("f14-ci"), "scope_zones": [zone.rstrip(".").upper()], "permission": "read",
            "expires_in_days": 30}).json()
        tid, plain = created["token"]["id"], created["plaintext_token"]
        tok = created["token"]
        ctx.eq((tok["scope_zones"], tok["permission"], tok["status"], tok["allow_admin"]),
               ([zone], "read", "active", False), "PanelTokenOut nach Anlage")
        ctx.check(tok["expires_at"] is not None, "expires_at gesetzt")
        ctx.api("GET", f"records/ns1/{_z(zone)}", token=plain, expect=200)
        r = ctx.api("POST", f"zones/ns1/{_z(zone)}/notify", token=plain, json={}, expect=403)
        ctx.eq(_detail(r), READ_ONLY, "Lese-Token blockiert Schreibzugriff")

    with ctx.step("Token kann die Token-Verwaltung nicht aufrufen"):
        r = ctx.api("GET", "auth/me/panel-tokens", token=plain, expect=403)
        ctx.eq(_detail(r), SESSION_ONLY, "Liste per Token")
        # Lese-Token: die Methodenregel greift schon vor der Session-Pflicht
        r = ctx.api("PUT", f"auth/me/panel-tokens/{tid}", token=plain, json={"permission": "manage"}, expect=403)
        ctx.eq(_detail(r), READ_ONLY, "PUT per Lese-Token")

    with ctx.step("Pausieren -> 401, Aktivieren -> wieder gueltig"):
        user.put(f"auth/me/panel-tokens/{tid}", json={"is_active": False}, expect=200)
        r = ctx.api("GET", "auth/me", token=plain, expect=401)
        ctx.eq(_detail(r), PAUSED, "pausierter Token")
        listed = user.get("auth/me/panel-tokens", expect=200).json()["tokens"]
        ctx.eq([t["status"] for t in listed if t["id"] == tid], ["paused"], "Status in der Liste")
        user.put(f"auth/me/panel-tokens/{tid}", json={"is_active": True}, expect=200)
        ctx.api("GET", "auth/me", token=plain, expect=200)

    with ctx.step("Berechtigung und Ablauf per PUT aendern (Klartext bleibt gueltig)"):
        res = user.put(f"auth/me/panel-tokens/{tid}", json={"permission": "manage", "expires_in_days": None},
                       expect=200).json()
        ctx.eq((res["token"]["permission"], res["token"]["expires_at"]), ("manage", None), "PUT-Ergebnis")
        me = ctx.api("GET", "auth/me", token=plain, expect=200).json()
        ctx.eq(me["auth"]["token"]["permission"], "manage", "Berechtigung wirkt am Token")
        r = ctx.api("PUT", f"auth/me/panel-tokens/{tid}", token=plain, json={"name": "x"}, expect=403)
        ctx.eq(_detail(r), SESSION_ONLY, "PUT per Schreib-Token")
        res = user.put(f"auth/me/panel-tokens/{tid}", json={"permission": "manage"}, expect=200).json()
        ctx.eq(res.get("message"), "Keine Änderungen", "PUT ohne Aenderung")
        n = ctx.db_value("SELECT COUNT(*) FROM audit_logs WHERE action = 'PANEL_TOKEN_UPDATE' AND user_id = %s",
                         (uid,))
        ctx.eq(int(n), 3, "PANEL_TOKEN_UPDATE-Audits (pausieren, aktivieren, Berechtigung+Ablauf)")

    with ctx.step("Admin-Sicht: Liste, Zaehler, Einzel-Widerruf"):
        second = user.post("auth/me/panel-tokens", json={"name": ctx.unique("f14-zwei")}, expect=201).json()
        ctx.eq(_token_count(ctx, uid), 2, "panel_token_count")
        r = ctx.api("GET", f"auth/users/{uid}/panel-tokens", expect=403)
        ctx.eq(_detail(r), SESSION_ONLY, "Admin-Sicht per Bootstrap-Token")
        body = ctx.admin_session.get(f"auth/users/{uid}/panel-tokens", expect=200).json()
        ids = [t["id"] for t in body["tokens"]]
        ctx.check(tid in ids and second["token"]["id"] in ids, f"Admin sieht beide Tokens: {ids}")
        ctx.check(all("token_hash" not in t for t in body["tokens"]), "kein Hash in der Admin-Sicht")
        user.get(f"auth/users/{uid}/panel-tokens", expect=403)  # Nicht-Admin
        ctx.admin_session.delete(f"auth/users/{uid}/panel-tokens/{tid}", expect=200)
        r = ctx.api("GET", "auth/me", token=plain, expect=401)
        ctx.eq(_detail(r), INVALID, "widerrufener Token")
        user.put(f"auth/me/panel-tokens/{tid}", json={"is_active": True}, expect=404)

    with ctx.step("Admin: alle widerrufen"):
        res = ctx.admin_session.delete(f"auth/users/{uid}/panel-tokens", expect=200).json()
        ctx.eq(res.get("revoked"), 1, "Anzahl widerrufen")
        ctx.eq(_token_count(ctx, uid), 0, "panel_token_count nach Widerruf")
        ctx.api("GET", "auth/me", token=second["plaintext_token"], expect=401)
        audits = ctx.db("SELECT details FROM audit_logs WHERE action = 'PANEL_TOKEN_ADMIN_REVOKE' "
                        "AND resource_name = %s ORDER BY id", (uname,))
        ctx.eq(len(audits), 2, "PANEL_TOKEN_ADMIN_REVOKE-Audits")
        ctx.check(plain not in str(audits), "kein Klartext im Audit")


def check_upgrade(ctx) -> None:
    seed = ctx.seed
    ids = seed.get("token_ids") or {}
    if not ids.get("admin") or not ids.get("admin_inactive"):
        ctx.fail("Seed ohne token_ids admin/admin_inactive")

    with ctx.step("Migrierter Admin-Alt-Token: allow_admin = 1, weitreichend"):
        ctx.eq(int(ctx.db_value("SELECT allow_admin FROM panel_tokens WHERE id = %s", (ids["admin"],))), 1,
               "allow_admin des Admin-Alt-Tokens")
        listed = ctx.admin_session.get("auth/me/panel-tokens", expect=200).json()["tokens"]
        tok = next((t for t in listed if t["id"] == ids["admin"]), None)
        ctx.check(tok is not None, "Admin-Alt-Token fehlt in der eigenen Liste")
        ctx.eq((tok["scope_zones"], tok["expires_at"], tok["permission"], tok["status"], tok["admin_effective"]),
               (None, None, "manage", "active", True), "Admin-Alt-Token (weitreichend)")

    with ctx.step("Inaktiver Alt-Token ist widerrufen und nicht reaktivierbar"):
        ctx.check(ctx.db_value("SELECT revoked_at FROM panel_tokens WHERE id = %s", (ids["admin_inactive"],))
                  is not None, "revoked_at des inaktiven Alt-Tokens")
        ctx.check(all(t["id"] != ids["admin_inactive"] for t in listed), "widerrufener Alt-Token in der Liste")
        r = ctx.admin_session.put(f"auth/me/panel-tokens/{ids['admin_inactive']}", json={"is_active": True},
                                  expect=404)
        ctx.eq(_detail(r), "Token nicht gefunden", "PUT auf widerrufenen Alt-Token")

    with ctx.step("Admin-Sicht auf alice: Alt-Token ohne Admin-Freigabe"):
        alice = seed["users"]["alice"]["id"]
        body = ctx.admin_session.get(f"auth/users/{alice}/panel-tokens", expect=200).json()
        tok = next((t for t in body["tokens"] if t["id"] == ids.get("alice")), None)
        ctx.check(tok is not None, "alice-Alt-Token fehlt in der Admin-Sicht")
        ctx.eq((tok["allow_admin"], tok["scope_zones"], tok["status"]), (False, None, "active"), "alice-Alt-Token")
        ctx.check(_token_count(ctx, alice) >= 1, "panel_token_count von alice")

    with ctx.step("Neuer Token auf der migrierten Datenbank: anlegen, bearbeiten, widerrufen"):
        zone = seed["zones"]["alpha"]
        created = ctx.admin_session.post("auth/me/panel-tokens", expect=201, json={
            "name": ctx.unique("f14-upg"), "scope_zones": [zone], "permission": "read", "expires_in_days": 7}).json()
        tid, plain = created["token"]["id"], created["plaintext_token"]
        ctx.cleanup(lambda: ctx.admin_session.delete(f"auth/me/panel-tokens/{tid}"), "Upgrade-Token widerrufen")
        ctx.api("GET", f"records/ns1/{_z(zone)}", token=plain, expect=200)
        ctx.admin_session.put(f"auth/me/panel-tokens/{tid}", json={"scope_zones": None, "allow_admin": True},
                              expect=200)
        ctx.api("GET", "audit-log?limit=1", token=plain, expect=200)
        ctx.admin_session.delete(f"auth/me/panel-tokens/{tid}", expect=200)
        ctx.api("GET", "auth/me", token=plain, expect=401)
