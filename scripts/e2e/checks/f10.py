"""E2E-Checks fuer F10 (SSO-Router, SSO-Einstellungen, Step-up; WS-F10-APP-BE) – ohne echten IdP/Verzeichnis.

Neuinstallation (``check_fresh``):
- ``GET /auth/sso/providers`` anonym: lokale Anmeldung an, keine Anbieter, ``Cache-Control: no-store``.
- ``GET /auth/oidc/start`` (OIDC aus) -> 303 ``/login?sso_error=disabled``; Callback ohne State-Cookie bei OIDC aus
  -> ebenfalls ``disabled`` und ohne neuen ``LOGIN_FAILED``-Audit (Review Welle 2: kein anonymer Audit-Spam).
- ``/settings/sso`` nur per Admin-Session (Admin-Token 403, anonym 401), liefert ``general.session_max_age``
  (Warnung zur Sitzungsdauer, Plan E-F10-2); Roundtrip einer unkritischen Aenderung
  ohne Step-up; sensible Aenderung (LDAP-Server) ohne Bestaetigung -> 403 ``stepup_required``, mit falschem Passwort
  -> 403 ``stepup_failed``, mit Passwort -> 200 + Audit ``SSO_SETTINGS_UPDATE`` (``step_up: "password"``).
  Alles wird am Ende zurueckgesetzt (LDAP bleibt aus, andere Module merken nichts).
- ``POST /settings/sso/test`` gegen einen nicht erreichbaren LDAP-Server: 200 mit ``success=false`` und klarer
  Fehlermeldung (kein 500, kein Haengen), Audit ``SSO_SETTINGS_TEST`` ohne Werte.
- ``GET /auth/users`` enthaelt den ``sso``-Block und je Benutzer ``external_issuer``/``external_id``.
Upgrade (``check_upgrade``): alle Altkonten sind lokal (``auth_source='local'``, keine externe ID), Anbieterliste und
SSO-Einstellungen liefern die sicheren Defaults (JIT aus, lokale Anmeldung an), ``alice`` meldet sich weiter an.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

UNREACHABLE_LDAP = "ldaps://ldap-unreachable.e2e.test"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - Redirect nicht folgen
        return None


def _raw_get(ctx, path: str) -> tuple[int, dict]:
    """GET ohne Redirect-Verfolgung (Status, Header klein)."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(ctx.base_url.rstrip("/") + path, method="GET")
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}


def _detail_code(resp) -> str | None:
    try:
        detail = resp.json().get("detail")
    except Exception:  # noqa: BLE001
        return None
    return detail.get("code") if isinstance(detail, dict) else None


def _login_failed_count(ctx) -> int:
    rows = ctx.db("SELECT COUNT(*) AS n FROM audit_logs WHERE action='LOGIN_FAILED'")
    return int(rows[0]["n"]) if rows else 0


def _last_audit(ctx, action: str) -> dict | None:
    rows = ctx.db("SELECT status, details FROM audit_logs WHERE action=%s ORDER BY id DESC LIMIT 1", (action,))
    if not rows:
        return None
    details = rows[0]["details"]
    if isinstance(details, (str, bytes)):
        details = json.loads(details)
    return {"status": rows[0]["status"], "details": details or {}}


def _public_flows(ctx) -> None:
    with ctx.step("Anbieterliste anonym"):
        r = ctx.api("GET", "auth/sso/providers", token=None, expect=200)
        body = r.json()
        ctx.eq(body.get("local_login_enabled"), True, "lokale Anmeldung an")
        ctx.eq(body.get("providers"), [], "keine Anbieter ohne Konfiguration")
        ctx.eq(body.get("ldap", {}).get("enabled"), False, "LDAP aus")
        ctx.eq(r.headers.get("cache-control"), "no-store", "Cache-Control")
    with ctx.step("OIDC-Start und Callback ohne Konfiguration"):
        status, headers = _raw_get(ctx, "/api/v1/auth/oidc/start")
        ctx.eq(status, 303, "Start-Status")
        ctx.eq(headers.get("location"), "/login?sso_error=disabled", "Start-Redirect")
        before = _login_failed_count(ctx)
        for i in range(3):
            status, headers = _raw_get(ctx, f"/api/v1/auth/oidc/callback?code=x&state=y{i}")
            ctx.eq(status, 303, "Callback-Status")
            ctx.eq(headers.get("location"), "/login?sso_error=disabled", "Callback ohne State-Cookie bei OIDC aus")
        ctx.eq(_login_failed_count(ctx), before, "kein LOGIN_FAILED-Audit fuer anonyme Callbacks bei OIDC aus")


def _settings_roundtrip(ctx) -> None:
    sess = ctx.admin_session
    with ctx.step("Zugriff nur per Admin-Session"):
        ctx.api("GET", "settings/sso", expect=403)              # Admin-Token
        ctx.api("GET", "settings/sso", token=None, expect=401)
        before = sess.get("settings/sso", expect=200).json()
        ctx.eq(before["ldap"]["enabled"], False, "LDAP aus")
        ctx.eq(before["oidc"]["jit_enabled"], False, "OIDC-JIT per Default aus [S5]")
        ctx.check(isinstance(before["general"].get("session_max_age"), int)
                  and before["general"]["session_max_age"] > 0, "general.session_max_age (Plan E-F10-2)")
    old_name = before["oidc"]["display_name"]
    old_ldap = {"server_urls": before["ldap"]["server_urls"], "user_base_dn": before["ldap"]["user_base_dn"]}

    def restore():
        sess.put("settings/sso", json={"oidc": {"display_name": old_name}, "ldap": old_ldap,
                                       "step_up": {"current_password": ctx.admin_password}})

    ctx.cleanup(restore, "SSO-Einstellungen zuruecksetzen")
    with ctx.step("Unkritische Aenderung ohne Step-up"):
        name = ctx.unique("E2E-SSO")
        r = sess.put("settings/sso", json={"oidc": {"display_name": name}}, expect=200)
        ctx.eq(r.json()["settings"]["oidc"]["display_name"], name, "Anzeigename gespeichert")
        ctx.eq(sess.get("settings/sso", expect=200).json()["oidc"]["display_name"], name, "Roundtrip")
    change = {"ldap": {"server_urls": [UNREACHABLE_LDAP], "user_base_dn": "DC=e2e,DC=test"}}
    with ctx.step("Sensible Aenderung verlangt Step-up [S8]"):
        r = sess.put("settings/sso", json=change, expect=403)
        ctx.eq(_detail_code(r), "stepup_required", "Code ohne Bestaetigung")
        ctx.eq(r.headers.get("x-step-up-required"), "stepup_required", "Header X-Step-Up-Required")
        ctx.eq(sess.get("settings/sso", expect=200).json()["ldap"]["server_urls"], old_ldap["server_urls"],
               "ohne Step-up nichts gespeichert")
        r = sess.put("settings/sso", json={**change, "step_up": {"current_password": "falsch-" + ctx.unique("x")}},
                     expect=403)
        ctx.eq(_detail_code(r), "stepup_failed", "Code bei falschem Passwort")
        r = sess.put("settings/sso", json={**change, "step_up": {"current_password": ctx.admin_password}},
                     expect=200)
        ctx.eq(r.json()["settings"]["ldap"]["server_urls"], [UNREACHABLE_LDAP], "Server gespeichert")
        ctx.eq(r.json()["settings"]["ldap"]["enabled"], False, "LDAP bleibt aus")
        audit = _last_audit(ctx, "SSO_SETTINGS_UPDATE")
        ctx.check(audit is not None, "Audit SSO_SETTINGS_UPDATE fehlt")
        ctx.eq(audit["details"].get("step_up"), "password", "Audit step_up")
        ctx.check("ldap_server_urls" in audit["details"].get("changed", {}), f"Audit changed: {audit['details']}")
    with ctx.step("Admin-Token darf SSO nicht aendern"):
        ctx.api("PUT", "settings/sso", json={"oidc": {"display_name": "token"}}, expect=403)


def _ldap_test_unreachable(ctx) -> None:
    with ctx.step("LDAP-Test gegen nicht erreichbaren Server"):
        r = ctx.admin_session.post("settings/sso/test", json={
            "target": "ldap",
            "ldap": {"server_urls": [UNREACHABLE_LDAP], "user_base_dn": "DC=e2e,DC=test", "timeout": 2},
            "test_username": "e2e", "test_password": "e2e-test-passwort",
        }, expect=200, timeout=60)
        body = r.json()
        ctx.eq(body.get("success"), False, "success")
        err = body.get("error") or ""
        ctx.check("ldap-unreachable.e2e.test" in err or "nicht erreichbar" in err, f"Fehlertext: {err!r}")
        audit = _last_audit(ctx, "SSO_SETTINGS_TEST")
        ctx.check(audit is not None, "Audit SSO_SETTINGS_TEST fehlt")
        ctx.eq(audit["status"], "error", "Audit-Status")
        ctx.eq(audit["details"].get("target"), "ldap", "Audit-Ziel")
        ctx.check("e2e-test-passwort" not in json.dumps(audit["details"]), "Testpasswort im Audit")


def _users_list(ctx) -> None:
    with ctx.step("Benutzerliste mit SSO-Block"):
        body = ctx.api("GET", "auth/users", expect=200).json()
        sso = body.get("sso") or {}
        ctx.eq(sorted(sso), ["ldap_jit", "ldap_role_mode", "oidc_jit", "oidc_role_mode"], "sso-Block")
        ctx.check(all("external_issuer" in u and "auth_source" in u for u in body["users"]),
                  "external_issuer/auth_source je Benutzer")


def check_fresh(ctx) -> None:
    _public_flows(ctx)
    _settings_roundtrip(ctx)
    _ldap_test_unreachable(ctx)
    _users_list(ctx)


def check_upgrade(ctx) -> None:
    with ctx.step("Altkonten sind lokal"):
        rows = ctx.db("SELECT username, auth_source, external_issuer, external_id FROM users")
        ctx.check(rows, "keine Benutzer")
        bad = [r["username"] for r in rows
               if r["auth_source"] != "local" or r["external_issuer"] is not None or r["external_id"] is not None]
        ctx.eq(bad, [], "Konten mit auth_source != local nach dem Upgrade")
    _public_flows(ctx)
    with ctx.step("SSO-Einstellungen mit sicheren Defaults"):
        s = ctx.admin_session.get("settings/sso", expect=200).json()
        ctx.eq(s["general"]["local_login_enabled"], True, "lokale Anmeldung an")
        ctx.eq((s["oidc"]["enabled"], s["ldap"]["enabled"]), (False, False), "OIDC/LDAP aus")
        ctx.eq((s["oidc"]["jit_enabled"], s["ldap"]["jit_enabled"]), (False, False), "JIT aus [S5]")
        ctx.check("(objectCategory=person)" in s["ldap"]["user_filter"], "neuer LDAP-Filter-Default [S5]")
    with ctx.step("alice meldet sich weiter an (lokal, TOTP)"):
        resp, _ = ctx.login(ctx.user_name, ctx.user_password, ctx.user_totp_secret)
        ctx.eq(resp.json()["user"]["auth_source"], "local", "auth_source alice")
    _users_list(ctx)
