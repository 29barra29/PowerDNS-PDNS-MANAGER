"""E2E-Checks fuer W0-INT-BE1 (Schema 3.0, Migrationen beim Start, Audit-Spalten).

Prueft direkt in der Panel-Datenbank (``ctx.db``), dass der 3.0-Start das Schema vollstaendig angelegt bzw.
migriert hat (Bauplan A.1-A.5, F.2 Punkt 6): Secret-Spalten breit genug, neue Spalten/Indizes/Tabellen,
Marker der einmaligen Datenmigrationen. Im Upgrade-Pfad zusaetzlich die Daten aus ``seed_241.py``:
F14-Token-Migration (inaktiver Alt-Token -> ``revoked_at``, Admin-Token -> ``allow_admin``) und der
Backfill ``audit_logs.zone_name``; nach dem zweiten Start ist alles unveraendert.

Die Verschluesselung der Werte (``enc:v1:``) prueft F5 (``checks/f5.py``), nicht diese Datei.
"""
from __future__ import annotations

SECRET_COLUMN_TYPES = {
    ("server_configs", "api_key"): ("text", "mediumtext", "longtext"),
    ("webhooks", "secret"): ("text", "mediumtext", "longtext"),
    ("webhooks", "url"): ("text", "mediumtext", "longtext"),
}
NEW_COLUMNS = {
    "users": ("must_change_password", "auth_source", "external_issuer", "external_id"),
    "webhooks": ("scope", "updated_at", "last_success_at", "last_failure_at", "consecutive_failures"),
    "audit_logs": ("zone_name", "revert_of_id", "actor_username", "client_ip"),
    "panel_tokens": ("scope_zones", "permission", "expires_at", "allow_admin", "revoked_at"),
    "dyndns_tokens": ("stale_servers",),
    "webhook_deliveries": ("delivery_id", "body", "next_attempt_at"),
}
NEW_INDEXES = (
    ("audit_logs", "ix_audit_logs_timestamp"), ("audit_logs", "ix_audit_logs_zone_ts"),
    ("audit_logs", "ix_audit_logs_user_id"), ("audit_logs", "ix_audit_logs_revert_of_id"),
    ("users", "uq_users_external"), ("webhook_deliveries", "ix_wd_due"),
)
MARKERS = ("migration_f14_panel_token_scope_v1", "audit_zone_backfill_v1")


def _columns(ctx) -> dict:
    rows = ctx.db("SELECT TABLE_NAME AS t, COLUMN_NAME AS c, DATA_TYPE AS dt, CHARACTER_MAXIMUM_LENGTH AS len "
                  "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()")
    return {(r["t"], r["c"]): (str(r["dt"]).lower(), r["len"]) for r in rows}


def _markers(ctx) -> dict:
    rows = ctx.db("SELECT `key` AS k, `value` AS v FROM system_settings WHERE `key` IN %s", (MARKERS,))
    return {r["k"]: r["v"] for r in rows}


def _check_schema(ctx, label: str) -> dict:
    with ctx.step(f"[{label}] Schema 3.0 vollstaendig"):
        cols = _columns(ctx)
        for (table, col), allowed in SECRET_COLUMN_TYPES.items():
            ctx.check(cols.get((table, col), ("fehlt",))[0] in allowed, f"{table}.{col}: {cols.get((table, col))}")
        for col in ("totp_secret", "totp_pending_secret"):
            dt, length = cols.get(("users", col), ("fehlt", None))
            ctx.check(dt in ("text", "mediumtext", "longtext") or (dt == "varchar" and (length or 0) >= 512),
                      f"users.{col}: {dt}({length})")
        for table, names in NEW_COLUMNS.items():
            for col in names:
                ctx.check((table, col) in cols, f"Spalte {table}.{col} fehlt")
        idx = {(r["t"], r["n"]) for r in ctx.db(
            "SELECT DISTINCT TABLE_NAME AS t, INDEX_NAME AS n FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE()")}
        for key in NEW_INDEXES:
            ctx.check(key in idx, f"Index {key[1]} auf {key[0]} fehlt")
    with ctx.step(f"[{label}] Marker der Datenmigrationen"):
        markers = _markers(ctx)
        ctx.eq(sorted(markers), sorted(MARKERS), "Marker in system_settings")
    return markers


def check_fresh(ctx) -> None:
    _check_schema(ctx, "Neuinstallation")
    with ctx.step("Neuinstallation: Datenmigrationen ohne Bestand"):
        ctx.eq(ctx.db_value("SELECT COUNT(*) FROM panel_tokens WHERE revoked_at IS NOT NULL AND is_active = 1"), 0,
               "aktive Tokens mit revoked_at")


def _expected_zone_names(ctx) -> dict:
    """seed_241.py: Zone-CREATE, Record CREATE/UPDATE (alpha), DELETE (beta), Fehler (gamma), LOGIN, PANEL_TOKEN_CREATE."""
    z = ctx.seed["zones"]
    ids = ctx.seed["audit_ids"]
    expected = [z["alpha"], z["alpha"], z["alpha"], z["beta"], z["gamma"], None, None]
    return dict(zip(ids, expected))


def _upgrade_facts(ctx, label: str) -> dict:
    markers = _check_schema(ctx, label)
    facts: dict = {"markers": markers}
    with ctx.step(f"[{label}] F14: Alt-Tokens migriert"):
        ids = ctx.seed["token_ids"]
        rows = {r["id"]: r for r in ctx.db(
            "SELECT id, is_active, allow_admin, revoked_at, last_used_at, created_at, permission, scope_zones, "
            "expires_at FROM panel_tokens WHERE id IN %s", (tuple(ids.values()),))}
        admin, alice, gone = rows[ids["admin"]], rows[ids["alice"]], rows[ids["admin_inactive"]]
        ctx.eq((int(admin["allow_admin"]), admin["revoked_at"]), (1, None), "Admin-Token: allow_admin=1, aktiv")
        ctx.eq((int(alice["allow_admin"]), alice["revoked_at"]), (0, None), "Alice-Token: kein Admin, aktiv")
        ctx.check(gone["revoked_at"] is not None, "inaktiver Alt-Token hat kein revoked_at")
        ctx.eq(gone["revoked_at"], gone["last_used_at"] or gone["created_at"], "revoked_at = last_used_at")
        for r in rows.values():
            ctx.eq((r["permission"], r["scope_zones"], r["expires_at"]), ("manage", None, None),
                   f"Token {r['id']}: Scope-Defaults")
        facts["tokens"] = {str(k): [int(v["allow_admin"]), str(v["revoked_at"])] for k, v in rows.items()}
    with ctx.step(f"[{label}] Backfill audit_logs.zone_name fuer Alt-Eintraege"):
        expected = _expected_zone_names(ctx)
        got = {r["id"]: r["zone_name"] for r in ctx.db(
            "SELECT id, zone_name FROM audit_logs WHERE id IN %s", (tuple(expected),))}
        ctx.eq(got, expected, "zone_name je Alt-Eintrag")
        facts["zones"] = {str(k): v for k, v in got.items()}
    return facts


def check_upgrade(ctx) -> None:
    ctx.state["w0_int_be1"] = _upgrade_facts(ctx, "erster Start")


def check_upgrade_restart(ctx) -> None:
    before = ctx.state.get("w0_int_be1")
    after = _upgrade_facts(ctx, "zweiter Start")
    with ctx.step("[zweiter Start] Migrationen liefen nicht erneut"):
        if before is None:
            ctx.skip("kein Zustand aus dem ersten Start")
        ctx.eq(after, before, "Marker, Tokens und zone_name unveraendert")
