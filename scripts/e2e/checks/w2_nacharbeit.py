"""E2E-Checks fuer WS-W2-NACHARBEIT (Restpunkte aus Welle 2).

- L3: "Alle Zugaenge widerrufen" beendet bestehende Browser-Sitzungen (``users.sessions_revoked_at``); eine neue
  Anmeldung danach funktioniert. Im Upgrade-Pfad zusaetzlich: die Spalte existiert und ist fuer Altkonten leer.
- L1: URL-Wechsel eines PowerDNS-Servers ohne neuen API-Key -> 400 ``secret_reentry_required``; nichts geaendert.
- L2 (Reveal ohne Audit -> 503) laesst sich gegen eine gesunde Instanz nicht ausloesen; Unit-Test
  ``backend/tests/test_w2_nacharbeit.py``.
Keine neue API; Neuinstallation und Upgrade pruefen dasselbe mit eigenem Benutzer.
"""
from __future__ import annotations

import secrets
import time


def _create_user(ctx, prefix: str) -> tuple[int, str, str]:
    name = ctx.unique(prefix)
    pw = "W2n-" + secrets.token_urlsafe(12)
    created = ctx.admin_session.post("auth/users", expect=(200, 201), json={
        "username": name, "password": pw, "role": "user", "must_change_password": False}).json()
    uid = created.get("id") or (created.get("user") or {}).get("id")
    ctx.check(uid, f"Benutzer-ID fehlt: {created}")
    ctx.cleanup(lambda: ctx.admin_session.delete(f"auth/users/{uid}"), f"Benutzer {name} loeschen")
    return uid, name, pw


def _session_revocation(ctx, prefix: str) -> None:
    uid, name, pw = _create_user(ctx, prefix)
    with ctx.step("Sitzung des Benutzers ist gueltig"):
        user = ctx.session(name, pw)
        ctx.eq(user.get("auth/me", expect=200).json()["username"], name, "auth/me")
        ctx.check(ctx.db_value("SELECT sessions_revoked_at FROM users WHERE id = %s", (uid,)) is None,
                  "sessions_revoked_at vor dem Widerruf gesetzt")

    with ctx.step("Alle Zugaenge widerrufen beendet die Sitzung (401)"):
        ctx.admin_session.post(f"auth/users/{uid}/revoke-access", json={}, expect=200)
        ctx.check(ctx.db_value("SELECT sessions_revoked_at FROM users WHERE id = %s", (uid,)) is not None,
                  "sessions_revoked_at nicht gesetzt")
        r = user.get("auth/me", expect=401)
        ctx.check("Sitzung abgelaufen" in r.text, f"unerwarteter Text: {r.text}")
        # der Admin bleibt angemeldet
        ctx.admin_session.get("auth/me", expect=200)

    with ctx.step("Neue Anmeldung nach dem Widerruf funktioniert"):
        time.sleep(1.2)   # Widerruf gilt bis zum Ende der Sekunde, in der er ausgesprochen wurde
        fresh = ctx.session(name, pw)
        ctx.eq(fresh.get("auth/me", expect=200).json()["username"], name, "auth/me nach neuer Anmeldung")
        user.get("auth/me", expect=401)


def _server_retarget(ctx) -> None:
    with ctx.step("Server-URL aendern ohne API-Key -> 400 secret_reentry_required"):
        servers = ctx.admin_session.get("settings/servers", expect=200).json()["servers"]
        srv = next((s for s in servers if s["name"] == "ns1"), None)
        ctx.check(srv is not None, f"Server ns1 fehlt: {[s['name'] for s in servers]}")
        r = ctx.admin_session.put(f"settings/servers/{srv['id']}", expect=400,
                                  json={"url": "http://w2n-boese.invalid:8081"})
        ctx.eq(r.json().get("code"), "secret_reentry_required", "code")
        ctx.eq(r.json().get("fields"), ["url"], "fields")
        after = next(s for s in ctx.admin_session.get("settings/servers", expect=200).json()["servers"]
                     if s["id"] == srv["id"])
        ctx.eq(after["url"], srv["url"], "URL unveraendert")


def check_fresh(ctx) -> None:
    _session_revocation(ctx, "w2n")
    _server_retarget(ctx)


def check_upgrade(ctx) -> None:
    with ctx.step("Spalte users.sessions_revoked_at nach dem Upgrade vorhanden, Altkonten ohne Widerruf"):
        n = ctx.db_value("SELECT COUNT(*) FROM users WHERE sessions_revoked_at IS NOT NULL "
                         "AND username IN ('admin', 'alice', 'bob')")
        ctx.eq(int(n), 0, "Altkonten mit sessions_revoked_at")
    _session_revocation(ctx, "w2nu")
    _server_retarget(ctx)
