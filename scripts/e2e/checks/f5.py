"""E2E-Checks fuer F5 (Geheimnisse verschluesselt, WS-F5-BE).

Neuinstallation (``check_fresh``):
- ``GET /settings/secrets/status`` nur per Admin-Session (Token 403, anonym 401), Modus ``encrypted``,
  Schluessel erzeugt, keine unlesbaren Werte; alle gespeicherten Geheimnisse liegen als ``enc:v1:`` in der DB.
- SMTP: Passwort verschluesselt, Maske behaelt es, Zielaenderung ohne Passwort -> 400 ``secret_reentry_required``.
- Server mit unlesbarem API-Key: ``api_key_status=unreadable``, Reveal 409, Status listet ihn.

Upgrade (``check_upgrade``/``check_upgrade_restart``) – Basis-Pruefungen aus Bauplan F.2/6:
- Schluessel erzeugt (Audit ``SECRETS_KEY_GENERATED`` genau einmal), alle Secret-Spalten ``enc:v1:``
  (server_configs.api_key, users.totp_*, webhooks.secret/url, Secret-Settings), Chiffretexte lesbar
  (Status ohne unlesbare Werte, Reveal = Seed-Key, SMTP/Captcha gesetzt), ``MIGRATION_ERRORS`` leer
  (lokales /health, vom Skript als ``/state/health-local-<modus>.json`` abgelegt).
- Schluesselkopie [S7]: ``update.sh --backup-key-only`` (vom Skript im Stack-Ordner ``/state/stack`` mit
  ``PDNSMGR_KEY_BACKUP_DIR=/state/keys`` ausgefuehrt) legt genau eine Kopie ``<stack>-<fingerprint>.key``
  (0600, Ordner 0700) ausserhalb des Stack-Ordners ab; im Stack-Ordner liegt keine Schluesseldatei.
- Zweiter Start: kein neuer Schluessel, keine erneute Migration, Chiffretexte byte-gleich.

Downgrade-Test (``upgrade-241-to-30.sh --with-downgrade``, Bauplan A.8 [D6]) ueber ``downgrade_main``:
``prepare`` (Tokens anlegen) -> Skript: ``prepare-downgrade --yes`` + 2.4.1-Code starten -> ``legacy`` ->
Skript: 3.0 erneut starten -> ``reupgrade``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import sys

PREFIX = "enc:v1:"
MASK = "••••••••"
KEY_SALT = b"pdns-manager:key-fingerprint:"
SECRET_SETTING_KEYS = ("smtp_password", "captcha_secret_key", "oidc_client_secret", "ldap_bind_password",
                       "metrics_token")
F14_MARKER = "migration_f14_panel_token_scope_v1"
STATE_DIR = os.environ.get("E2E_STATE_DIR", "/state")
DOWNGRADE_STATE = "f5-downgrade.json"

# Feld -> SQL fuer (Zeilen-ID, Rohwert) – Tabellen-/Spaltennamen fest, keine Parameter
_SECRET_SQL = {
    "server_configs.api_key": "SELECT id AS k, api_key AS v FROM server_configs",
    "webhooks.secret": "SELECT id AS k, secret AS v FROM webhooks",
    "webhooks.url": "SELECT id AS k, url AS v FROM webhooks",
    "users.totp_secret": "SELECT id AS k, totp_secret AS v FROM users",
    "users.totp_pending_secret": "SELECT id AS k, totp_pending_secret AS v FROM users",
    "system_settings": ("SELECT `key` AS k, `value` AS v FROM system_settings WHERE `key` IN ("
                        + ", ".join(f"'{k}'" for k in SECRET_SETTING_KEYS) + ")"),
}


# ---------------------------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------------------------


def _raw_rows(ctx) -> dict[str, dict[str, str]]:
    """Feld -> {Zeilen-ID/Key: Rohwert} fuer alle nicht leeren Secret-Werte."""
    out: dict[str, dict[str, str]] = {}
    for field, sql in _SECRET_SQL.items():
        out[field] = {str(r["k"]): str(r["v"]) for r in ctx.db(sql) if r["v"] not in (None, "")}
    return out


def _check_all_encrypted(ctx, required: tuple[str, ...] = ()) -> dict[str, dict[str, str]]:
    raw = _raw_rows(ctx)
    for field, values in raw.items():
        plain = [k for k, v in values.items() if not v.startswith(PREFIX)]
        ctx.check(not plain, f"{field}: {len(plain)} von {len(values)} Werten unverschluesselt (IDs {plain})")
    for field in required:
        ctx.check(raw.get(field), f"{field}: keine Werte (Seed erwartet)")
    return raw


def _check_none_encrypted(ctx) -> None:
    for field, values in _raw_rows(ctx).items():
        enc = [k for k, v in values.items() if v.startswith(PREFIX)]
        ctx.check(not enc, f"{field}: nach prepare-downgrade noch {len(enc)} verschluesselte Werte (IDs {enc})")


def _row_digests(raw: dict[str, dict[str, str]]) -> dict[str, str]:
    """Je Zeile ein Hash des Rohwerts (keine Chiffretexte im Zustand ablegen)."""
    return {f"{field}:{key}": hashlib.sha256(v.encode()).hexdigest()
            for field, rows in raw.items() for key, v in rows.items()}


def _fingerprint(key_text: str) -> str:
    material = base64.urlsafe_b64decode(key_text.strip().encode("ascii"))
    return hashlib.sha256(KEY_SALT + material).hexdigest()[:12]


def _status(ctx) -> dict:
    return ctx.admin_session.get("settings/secrets/status", expect=200).json()


def _audit_count(ctx, action: str) -> int:
    return int(ctx.db_value("SELECT COUNT(*) FROM audit_logs WHERE action = %s", (action,)) or 0)


def _local_health(ctx) -> dict | None:
    path = os.path.join(STATE_DIR, f"health-local-{ctx.mode}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _check_local_health(ctx) -> None:
    health = _local_health(ctx)
    ctx.check(health is not None, f"lokales /health fehlt ({STATE_DIR}/health-local-{ctx.mode}.json)")
    ctx.eq(health.get("migration_errors"), [], "MIGRATION_ERRORS")
    ctx.eq((health.get("secrets") or {}).get("mode"), "encrypted", "Geheimnis-Modus (lokales /health)")
    ctx.eq((health.get("secrets") or {}).get("unreadable_values"), 0, "unlesbare Werte beim Start")
    ctx.eq(health.get("servers_not_loaded"), {}, "nicht geladene Server")


def _check_status_access(ctx) -> None:
    ctx.api("GET", "settings/secrets/status", expect=403)              # Panel-Token (auch mit allow_admin)
    ctx.api("GET", "settings/secrets/status", token=None, expect=401)  # anonym
    if ctx.user_name:
        ctx.user_session.get("settings/secrets/status", expect=403)     # Nicht-Admin


def _server_ids(ctx) -> dict[str, dict]:
    data = ctx.admin_session.get("settings/servers", expect=200).json()
    return {s["name"]: s for s in data.get("servers", [])}


# ---------------------------------------------------------------------------------------------
# Neuinstallation
# ---------------------------------------------------------------------------------------------


def check_fresh(ctx) -> None:
    with ctx.step("Status nur fuer Admins mit Browser-Session"):
        _check_status_access(ctx)
        st = _status(ctx)
        ctx.eq(st["mode"], "encrypted", "Modus")
        ctx.check(st["key_source"] in ("generated", "file", "env"), f"Schluesselquelle: {st['key_source']}")
        ctx.check(len(st.get("key_fingerprint") or "") == 12, f"Fingerprint: {st.get('key_fingerprint')}")
        ctx.eq(st["unreadable"], [], "unlesbare Werte")
        ctx.check(st["health"] in ("ok", "warning"), f"Status-Zustand: {st['health']} {st['issues']}")
        ids = {c["id"] for c in st["columns"]}
        ctx.check({"webhooks.url", "system_settings.metrics_token"} <= ids, f"Spalten im Status: {sorted(ids)}")

    with ctx.step("Gespeicherte Geheimnisse liegen verschluesselt in der DB"):
        _check_all_encrypted(ctx, required=("server_configs.api_key",))

    with ctx.step("Server: api_key_status und Reveal"):
        servers = _server_ids(ctx)
        for name in ctx.pdns.servers:
            ctx.eq(servers[name]["api_key_status"], "set", f"api_key_status {name}")
        key = ctx.admin_session.get(f"settings/servers/{servers['ns1']['id']}/api-key", expect=200).json()["api_key"]
        ctx.eq(key, ctx.pdns.api_key("ns1"), "Reveal ns1")

    _check_smtp(ctx)
    _check_captcha(ctx)
    _check_unreadable_server(ctx)


def _check_smtp(ctx) -> None:
    before = ctx.admin_session.get("settings/smtp", expect=200).json()

    def _restore():
        body = {k: before.get(k) for k in ("host", "port", "username", "from_email", "from_name", "encryption",
                                           "enabled")}
        body["password"] = ""
        ctx.admin_session.put("settings/smtp", json=body, expect=200)

    ctx.cleanup(_restore, "SMTP zuruecksetzen")
    pw = ctx.unique("smtp-pw")
    body = {"host": "smtp.e2e.test", "port": 587, "username": "relay", "password": pw,
            "from_email": "dns@e2e.test", "from_name": "E2E", "encryption": "starttls", "enabled": False}
    with ctx.step("SMTP-Passwort verschluesselt, Maske behaelt es"):
        ctx.admin_session.put("settings/smtp", json=body, expect=200)
        raw = ctx.db_value("SELECT `value` FROM system_settings WHERE `key` = 'smtp_password'")
        ctx.check(str(raw).startswith(PREFIX) and pw not in str(raw), "smtp_password nicht verschluesselt")
        got = ctx.admin_session.get("settings/smtp", expect=200).json()
        ctx.check(got["password"] == MASK and got["password_set"] and not got["password_unreadable"], f"GET smtp: {got}")
        ctx.admin_session.put("settings/smtp", json={**body, "password": MASK, "from_name": "E2E 2"}, expect=200)
        ctx.eq(ctx.db_value("SELECT `value` FROM system_settings WHERE `key` = 'smtp_password'"), raw,
               "Maske schreibt das Passwort nicht neu")
    with ctx.step("SMTP: Zielaenderung ohne Passwort -> 400"):
        r = ctx.admin_session.put("settings/smtp", json={**body, "password": MASK, "host": "evil.e2e.test"}, expect=400)
        ctx.eq(r.json().get("code"), "secret_reentry_required", "Fehlercode")
        r = ctx.admin_session.post("settings/smtp/test", json={"host": "evil.e2e.test"}, expect=400)
        ctx.eq(r.json().get("fields"), ["host"], "Felder im Test-Endpunkt")
        ctx.eq(ctx.db_value("SELECT `value` FROM system_settings WHERE `key` = 'smtp_host'"), "smtp.e2e.test",
               "Host unveraendert")
        ctx.check(_audit_count(ctx, "SMTP_UPDATE") >= 2, "SMTP_UPDATE-Audit fehlt")


def _check_captcha(ctx) -> None:
    # Provider bleibt "none": sonst verlangten alle folgenden Logins ein Captcha.
    ctx.cleanup(lambda: ctx.admin_session.put("settings/captcha", json={"provider": "none", "site_key": "",
                                                                        "secret_key": ""}, expect=200),
                "Captcha zuruecksetzen")
    with ctx.step("Captcha-Secret verschluesselt, Audit CAPTCHA_UPDATE"):
        n = _audit_count(ctx, "CAPTCHA_UPDATE")
        ctx.admin_session.put("settings/captcha", json={"provider": "none", "site_key": "", "secret_key": "cap-e2e"},
                              expect=200)
        raw = ctx.db_value("SELECT `value` FROM system_settings WHERE `key` = 'captcha_secret_key'")
        ctx.check(str(raw).startswith(PREFIX), "captcha_secret_key nicht verschluesselt")
        got = ctx.admin_session.get("settings/captcha", expect=200).json()
        ctx.check(got["secret_key_set"] and not got["secret_key_unreadable"], f"GET captcha: {got}")
        ctx.eq(_audit_count(ctx, "CAPTCHA_UPDATE"), n + 1, "CAPTCHA_UPDATE")


def _check_unreadable_server(ctx) -> None:
    name = ctx.unique("f5-srv")
    created = ctx.admin_session.post("settings/servers", json={
        "name": name, "url": ctx.pdns.url("ns1"), "api_key": "f5-temp-key", "allow_writes": False}, expect=201).json()
    sid = created["id"]
    ctx.cleanup(lambda: ctx.admin_session.delete(f"settings/servers/{sid}"), "Testserver loeschen")
    with ctx.step("Server mit unlesbarem API-Key"):
        ctx.db("UPDATE server_configs SET api_key = %s WHERE id = %s", (PREFIX + "gAAAAAB" + "A" * 120, sid))
        servers = _server_ids(ctx)
        ctx.eq(servers[name]["api_key_status"], "unreadable", "api_key_status")
        ctx.check(servers[name]["has_api_key"] is False and servers[name]["api_key"] == "", f"Maske: {servers[name]}")
        r = ctx.admin_session.get(f"settings/servers/{sid}/api-key", expect=409)
        ctx.check("nicht entschlüsselt" in r.json().get("detail", ""), f"Reveal-Text: {r.text}")
        st = _status(ctx)
        ctx.check(any(i["field"] == "server_configs.api_key" and i["name"] == name for i in st["unreadable"]),
                  f"Status listet den Server nicht: {st['unreadable']}")
        ctx.eq(st["health"], "error", "Status-Zustand mit unlesbarem Wert")
        ctx.admin_session.put(f"settings/servers/{sid}", json={"description": "f5"}, expect=200)
        ctx.admin_session.put(f"settings/servers/{sid}", json={"is_active": False}, expect=200)


# ---------------------------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------------------------


def _check_key_copy(ctx, fingerprint: str) -> None:
    keys_dir = os.path.join(STATE_DIR, "keys")
    stack_dir = os.path.join(STATE_DIR, "stack")
    ctx.check(os.path.isdir(stack_dir), f"Stack-Ordner der Schluesselsicherung fehlt ({stack_dir})")
    files = sorted(os.listdir(keys_dir)) if os.path.isdir(keys_dir) else []
    ctx.check(len(files) == 1, f"erwartet genau eine Schluesselkopie in {keys_dir}, gefunden: {files}")
    ctx.check(files[0].endswith(f"-{fingerprint}.key"), f"Dateiname ohne Fingerprint {fingerprint}: {files[0]}")
    path = os.path.join(keys_dir, files[0])
    ctx.eq(stat.S_IMODE(os.stat(keys_dir).st_mode), 0o700, "Rechte des Schluesselordners")
    ctx.eq(stat.S_IMODE(os.stat(path).st_mode), 0o600, "Rechte der Schluesselkopie")
    with open(path, encoding="ascii") as fh:
        ctx.eq(_fingerprint(fh.read()), fingerprint, "Fingerprint der Kopie")
    suspicious = []
    for root, _dirs, names in os.walk(stack_dir):
        for n in names:
            low = n.lower()
            if low.endswith(".key") or "secret_key" in low or low.startswith("backup_"):
                suspicious.append(os.path.relpath(os.path.join(root, n), stack_dir))
    ctx.eq(suspicious, [], "Schluessel-/Backup-Dateien im Stack-Ordner")


def check_upgrade(ctx) -> None:
    with ctx.step("Schluessel beim ersten Start erzeugt"):
        ctx.eq(_audit_count(ctx, "SECRETS_KEY_GENERATED"), 1, "Audit SECRETS_KEY_GENERATED")
        ctx.eq(_audit_count(ctx, "SECRETS_MIGRATE"), 1, "Audit SECRETS_MIGRATE")
        st = _status(ctx)
        ctx.eq(st["mode"], "encrypted", "Modus")
        ctx.check(st["key_source"] in ("generated", "file"), f"Schluesselquelle: {st['key_source']}")
        ctx.check(st["key_file_exists"] is True, f"Schluesseldatei fehlt: {st['key_file']}")
        fingerprint = st["key_fingerprint"] or ""
        ctx.check(len(fingerprint) == 12, f"Fingerprint: {fingerprint}")
        ctx.state["f5_fingerprint"] = fingerprint

    with ctx.step("Alle Secret-Spalten verschluesselt (inkl. webhooks.url)"):
        raw = _check_all_encrypted(ctx, required=("server_configs.api_key", "webhooks.secret", "webhooks.url",
                                                  "users.totp_secret", "system_settings"))
        ctx.state["f5_secret_rows"] = _row_digests(raw)

    with ctx.step("Chiffretexte lesbar"):
        _check_status_access(ctx)
        st = _status(ctx)
        ctx.eq(st["unreadable"], [], "unlesbare Werte")
        ctx.check(st["health"] in ("ok", "warning"), f"Status: {st['health']} {st['issues']}")
        servers = _server_ids(ctx)
        for name, srv in (ctx.seed.get("servers") or {}).items():
            ctx.eq(servers[name]["api_key_status"], "set", f"api_key_status {name}")
            key = ctx.admin_session.get(f"settings/servers/{servers[name]['id']}/api-key", expect=200).json()["api_key"]
            ctx.eq(key, srv["api_key"], f"Reveal {name} = Seed-Key")
        smtp = ctx.admin_session.get("settings/smtp", expect=200).json()
        ctx.check(smtp["password_set"] and not smtp["password_unreadable"], f"SMTP: {smtp}")
        cap = ctx.admin_session.get("settings/captcha", expect=200).json()
        ctx.check(cap["secret_key_set"] and not cap["secret_key_unreadable"], f"Captcha: {cap}")

    with ctx.step("MIGRATION_ERRORS leer (lokales /health)"):
        _check_local_health(ctx)

    with ctx.step("Schluesselkopie ausserhalb des Stack-Ordners [S7]"):
        _check_key_copy(ctx, ctx.state["f5_fingerprint"])


def check_upgrade_restart(ctx) -> None:
    with ctx.step("[zweiter Start] kein neuer Schluessel, keine erneute Migration"):
        if "f5_fingerprint" not in ctx.state:
            ctx.skip("kein Zustand aus dem ersten Start")
        ctx.eq(_audit_count(ctx, "SECRETS_KEY_GENERATED"), 1, "Audit SECRETS_KEY_GENERATED")
        ctx.eq(_audit_count(ctx, "SECRETS_MIGRATE"), 1, "Audit SECRETS_MIGRATE")
        ctx.eq(_status(ctx)["key_fingerprint"], ctx.state["f5_fingerprint"], "Fingerprint")
        # Nur Zeilen vergleichen, die es beim ersten Start schon gab (andere Checks legen eigene an)
        now = _row_digests(_raw_rows(ctx))
        changed = sorted(k for k, h in ctx.state["f5_secret_rows"].items() if k in now and now[k] != h)
        ctx.eq(changed, [], "Chiffretexte des ersten Starts byte-gleich")
    with ctx.step("[zweiter Start] MIGRATION_ERRORS leer"):
        _check_local_health(ctx)


# ---------------------------------------------------------------------------------------------
# Downgrade-Test (upgrade-241-to-30.sh --with-downgrade)
# ---------------------------------------------------------------------------------------------


def _downgrade_ctx():
    import checks

    ctx = checks.Ctx("upgrade")
    checks._bootstrap_upgrade(ctx)  # noqa: SLF001 - Seed-Daten wie im Upgrade-Lauf
    ctx.current_check = "f5-downgrade"
    return ctx, checks


def _dg_state_path() -> str:
    return os.path.join(STATE_DIR, DOWNGRADE_STATE)


def _dg_load() -> dict:
    with open(_dg_state_path(), encoding="utf-8") as fh:
        return json.load(fh)


def _dg_save(data: dict) -> None:
    fd = os.open(_dg_state_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)


def _token_row(ctx, token_id: int) -> dict:
    rows = ctx.db("SELECT is_active, revoked_at, expires_at FROM panel_tokens WHERE id = %s", (token_id,))
    ctx.check(rows, f"Token {token_id} fehlt")
    return rows[0]


def downgrade_prepare(ctx, checks) -> None:
    """3.0 laeuft: Tokens fuer den Downgrade-Test anlegen (abgelaufen / unter 2.4.1 zu loeschen)."""
    with ctx.step("Token mit Ablaufdatum in der Vergangenheit anlegen"):
        exp = ctx.admin_session.post("auth/me/panel-tokens", json={
            "name": ctx.unique("f5-dg-expired"), "allow_admin": True, "expires_in_days": 1}, expect=(200, 201)).json()
        exp_id = exp["token"]["id"] if isinstance(exp.get("token"), dict) else exp.get("id")
        ctx.db("UPDATE panel_tokens SET expires_at = UTC_TIMESTAMP() - INTERVAL 1 DAY WHERE id = %s", (exp_id,))
        ctx.api("GET", "auth/me", token=exp["plaintext_token"], expect=401)  # 3.0: abgelaufen
    with ctx.step("Token anlegen, das unter 2.4.1 geloescht wird"):
        dele = ctx.admin_session.post("auth/me/panel-tokens", json={
            "name": ctx.unique("f5-dg-delete"), "allow_admin": True}, expect=(200, 201)).json()
        del_id = dele["token"]["id"] if isinstance(dele.get("token"), dict) else dele.get("id")
        ctx.api("GET", "auth/me", token=dele["plaintext_token"], expect=200)
    _dg_save({
        "expired": {"id": exp_id, "token": exp["plaintext_token"]},
        "delete": {"id": del_id, "token": dele["plaintext_token"]},
        "fingerprint": _status(ctx)["key_fingerprint"],
        "migrate_audits": _audit_count(ctx, "SECRETS_MIGRATE"),
    })


def downgrade_legacy(ctx, checks) -> None:
    """2.4.1-Code laeuft gegen die vorbereitete DB."""
    data = _dg_load()
    legacy = os.environ.get("E2E_LEGACY_URL", "http://pdnsmgr-e2e-legacy:8000")
    with ctx.step("prepare-downgrade: Klartext, Token-Haertung, Marker, Audit"):
        _check_none_encrypted(ctx)
        row = _token_row(ctx, data["expired"]["id"])
        ctx.eq(int(row["is_active"]), 0, "abgelaufener Token deaktiviert")
        ctx.eq(row["revoked_at"], None, "revoked_at (setzt erst die Migration beim Re-Upgrade)")
        ctx.eq(int(_token_row(ctx, data["delete"]["id"])["is_active"]), 1, "uneingeschraenkter Token bleibt aktiv")
        ctx.eq(int(ctx.db_value("SELECT COUNT(*) FROM system_settings WHERE `key` = %s", (F14_MARKER,))), 0,
               "Marker migration_f14_panel_token_scope_v1")
        ctx.eq(_audit_count(ctx, "DOWNGRADE_PREPARED"), 1, "Audit DOWNGRADE_PREPARED")
    with ctx.step("2.4.1 sieht Klartext: Server erreichbar"):
        health = checks.HttpSession(legacy, label="legacy").get("/health", expect=200).json()
        ctx.eq(health.get("status"), "healthy", f"2.4.1-/health {health}")
        for name in ctx.pdns.servers:
            ctx.eq((health.get("servers") or {}).get(name), "healthy", f"2.4.1-Server {name}")
    with ctx.step("2.4.1: Tokens"):
        checks.HttpSession(legacy, token=data["expired"]["token"]).get("auth/me", expect=401)
        checks.HttpSession(legacy, token=ctx.admin_token).get("auth/me", expect=200)
        checks.HttpSession(legacy, token=ctx.admin_token).get("zones/ns1", expect=200)
    with ctx.step("2.4.1: Login mit TOTP (Klartext-Geheimnis)"):
        sess = checks.HttpSession(legacy, label="legacy:alice")
        r = sess.post("auth/login", form={"username": ctx.user_name, "password": ctx.user_password}, expect=200).json()
        ctx.check(r.get("need_two_factor"), f"2.4.1 verlangt kein TOTP: {r}")
        sess.post("auth/login/2fa", json={"two_factor_token": r["two_factor_token"],
                                          "totp_code": checks.totp_code(ctx.user_totp_secret)}, expect=200)
        sess.get("auth/me", expect=200)
    with ctx.step("2.4.1: Token loeschen (is_active=0 ohne revoked_at)"):
        admin = checks.HttpSession(legacy, label="legacy:admin")
        admin.post("auth/login", form={"username": ctx.admin_username, "password": ctx.admin_password}, expect=200)
        admin.delete(f"auth/me/panel-tokens/{data['delete']['id']}", expect=200)
        row = _token_row(ctx, data["delete"]["id"])
        ctx.eq((int(row["is_active"]), row["revoked_at"]), (0, None), "unter 2.4.1 geloeschter Token")


def downgrade_reupgrade(ctx, checks) -> None:
    """3.0 erneut gestartet: Migrationen liefen wieder (fail-closed), Geheimnisse wieder verschluesselt."""
    data = _dg_load()
    with ctx.step("Re-Upgrade: Tokens als widerrufen migriert, Marker wieder da"):
        for kind in ("expired", "delete"):
            row = _token_row(ctx, data[kind]["id"])
            ctx.check(int(row["is_active"]) == 0 and row["revoked_at"] is not None, f"Token {kind}: {row}")
            ctx.api("GET", "auth/me", token=data[kind]["token"], expect=401)
        ctx.eq(int(ctx.db_value("SELECT COUNT(*) FROM system_settings WHERE `key` = %s", (F14_MARKER,))), 1, "Marker")
        ctx.api("GET", "audit-log", expect=200)  # Alt-Admin-Token (allow_admin) funktioniert weiter
    with ctx.step("Re-Upgrade: Geheimnisse wieder verschluesselt, gleicher Schluessel"):
        _check_all_encrypted(ctx, required=("server_configs.api_key", "webhooks.secret", "webhooks.url",
                                            "users.totp_secret", "system_settings"))
        ctx.eq(_audit_count(ctx, "SECRETS_KEY_GENERATED"), 1, "kein neuer Schluessel")
        ctx.eq(_audit_count(ctx, "SECRETS_MIGRATE"), data["migrate_audits"] + 1, "Migration beim Re-Upgrade")
        st = _status(ctx)
        ctx.eq(st["key_fingerprint"], data["fingerprint"], "Fingerprint")
        ctx.eq(st["unreadable"], [], "unlesbare Werte")
    with ctx.step("Re-Upgrade: Login mit TOTP"):
        _resp, sess = ctx.login(ctx.user_name, ctx.user_password, ctx.user_totp_secret)
        sess.get("auth/me", expect=200)
    with ctx.step("Re-Upgrade: MIGRATION_ERRORS leer"):
        ctx.mode = "reupgrade"
        _check_local_health(ctx)


_DOWNGRADE_STEPS = {"prepare": downgrade_prepare, "legacy": downgrade_legacy, "reupgrade": downgrade_reupgrade}


def downgrade_main(argv: list[str]) -> int:
    """Einstieg fuer upgrade-241-to-30.sh --with-downgrade: ``python -c 'import checks.f5 as f5; ...' <schritt>``."""
    if len(argv) != 1 or argv[0] not in _DOWNGRADE_STEPS:
        print(f"Aufruf: downgrade_main <{'|'.join(_DOWNGRADE_STEPS)}>", file=sys.stderr)
        return 2
    step = argv[0]
    print(f"== F5-Downgrade-Test: {step}", flush=True)
    try:
        ctx, checks = _downgrade_ctx()
    except Exception as exc:  # noqa: BLE001
        print(f"   ERROR Bootstrap: {type(exc).__name__}: {exc}", flush=True)
        return 2
    status, detail = "PASS", ""
    try:
        _DOWNGRADE_STEPS[step](ctx, checks)
    except checks.CheckFailed as exc:
        status, detail = "FAIL", str(exc)
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        status, detail = "ERROR", f"{type(exc).__name__}: {exc}"
    problems = ctx._run_cleanups()  # noqa: SLF001
    if problems:
        detail = (detail + "; " if detail else "") + "Aufraeumen: " + " | ".join(problems)
    print(f"   {status:5} f5-downgrade {step}{' – ' + detail if detail else ''}", flush=True)
    return 0 if status == "PASS" else 1
