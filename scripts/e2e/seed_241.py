"""Erzeugt eine 2.4.1-Datenbank mit Testdaten fuer den Upgrade-Test (2.4.1 -> 3.0).

Laeuft im Werkzeug-Container (compose-Dienst ``tools``) mit dem Quellstand v2.4.1 auf dem
PYTHONPATH (``upgrade-241-to-30.sh`` legt ihn per ``git archive`` an und setzt
``PYTHONPATH=/legacy/backend``). Dadurch entstehen Schema (``init_db`` von 2.4.1), Passwort-Hashes
und Token-Hashes exakt so, wie 2.4.1 sie schreibt.

Aufruf (im Container):
    python /e2e/seed_241.py --out /state/seed.json [--expect-version 2.4.1]

Umgebung: DATABASE_URL (von 2.4.1 gelesen), E2E_PDNS ("ns1=http://pdns1:8081|key,ns2=..."),
E2E_RECEIVER_URL.

Angelegt wird:
- Benutzer ``admin`` (Admin), ``alice`` (Benutzerin mit aktivem TOTP), ``bob`` (Benutzer ohne Zonen)
- Server-Konfigurationen ns1/ns2 mit Klartext-API-Key (2.4.1-Format)
- Zonen alpha/beta/gamma.e2e-seed.test. auf beiden PowerDNS-Servern (direkt ueber die PowerDNS-API)
- Zonenrechte: alice -> alpha (manage), beta (read); bob -> keine
- Webhooks (Klartext-Secret): admin -> /hook/seed-admin (alle Ereignisse), alice -> /hook/seed-alice (record)
- Panel-Tokens: admin aktiv, alice aktiv, admin inaktiv (is_active=0)
- ACME-Token (alpha) sowie SMTP-Passwort und Captcha-Secret in system_settings (Captcha bleibt aus)
- Audit-Zeilen im v1-Format (wie 2.4.1 sie schreibt) mit Zeitstempeln in der Vergangenheit

Die Ausgabedatei (JSON) enthaelt alle Klartext-Geheimnisse und IDs; der Check-Runner liest sie als
``ctx.seed``. Die Datenbank muss leer sein (sonst Abbruch).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

SEED_DOMAIN = "e2e-seed.test."
ZONES = {
    "alpha": f"alpha.{SEED_DOMAIN}",
    "beta": f"beta.{SEED_DOMAIN}",
    "gamma": f"gamma.{SEED_DOMAIN}",
}


def _log(msg: str) -> None:
    print(f"[seed_241] {msg}", file=sys.stderr, flush=True)


def _parse_pdns(spec: str) -> dict:
    servers = {}
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, rest = part.split("=", 1)
        url, key = rest.split("|", 1)
        servers[name.strip()] = {"url": url.strip().rstrip("/"), "api_key": key.strip()}
    if not servers:
        raise SystemExit("E2E_PDNS ist leer – keine PowerDNS-Server bekannt")
    return servers


def _pdns_request(server: dict, method: str, path: str, body=None) -> tuple[int, str]:
    url = f"{server['url']}/api/v1/servers/localhost{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", server["api_key"])
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _create_pdns_zone(server_name: str, server: dict, zone: str) -> None:
    rrsets = [
        {"name": f"www.{zone}", "type": "A", "ttl": 300, "changetype": "REPLACE",
         "records": [{"content": "192.0.2.1", "disabled": False}]},
        {"name": zone, "type": "TXT", "ttl": 3600, "changetype": "REPLACE",
         "records": [{"content": '"seed-241"', "disabled": False}]},
    ]
    code, text = _pdns_request(server, "POST", "/zones", {
        "name": zone, "kind": "Native",
        "nameservers": [f"ns1.{SEED_DOMAIN}", f"ns2.{SEED_DOMAIN}"],
        "soa_edit_api": "DEFAULT",
        "rrsets": [{k: v for k, v in r.items() if k != "changetype"} for r in rrsets],
    })
    if code == 201:
        return
    if code == 409:
        _log(f"Zone {zone} existiert auf {server_name} bereits – Records werden ueberschrieben")
        code, text = _pdns_request(server, "PATCH", f"/zones/{zone}", {"rrsets": rrsets})
        if code in (200, 204):
            return
    raise SystemExit(f"PowerDNS {server_name}: Zone {zone} konnte nicht angelegt werden ({code}): {text[:300]}")


async def seed(out_path: str, expect_version: str | None) -> dict:
    # 2.4.1-Code (PYTHONPATH) – bewusst erst hier importieren, damit --help ohne DB funktioniert.
    import pyotp
    from sqlalchemy import func, select

    from app.core.auth import PANEL_TOKEN_PREFIX, hash_password
    from app.core.config import settings
    from app.core.database import async_session, engine, init_db
    from app.models.models import (
        AuditLog, PanelToken, ServerConfig, SystemSetting, User, UserZoneAccess, Webhook,
    )
    from app.services import acme as acme_service
    from app.services import panel_token as panel_token_service

    version = settings.APP_VERSION
    _log(f"Quellstand meldet Version {version}")
    if expect_version and version != expect_version:
        raise SystemExit(f"Falscher Quellstand: erwartet {expect_version}, gefunden {version}")

    pdns = _parse_pdns(os.environ.get("E2E_PDNS", ""))
    receiver = os.environ.get("E2E_RECEIVER_URL", "http://receiver:8080").rstrip("/")

    await init_db()
    _log("init_db (2.4.1) ausgefuehrt")

    # PowerDNS-Zonen auf allen Servern
    for zone in ZONES.values():
        for name, srv in pdns.items():
            _create_pdns_zone(name, srv, zone)
    _log(f"Zonen angelegt: {', '.join(ZONES.values())} auf {', '.join(pdns)}")

    now = datetime.utcnow().replace(microsecond=0)
    out: dict = {
        "source_version": version,
        "created_at": now.isoformat() + "Z",
        "zones": dict(ZONES),
        "servers": {n: {"url": s["url"], "api_key": s["api_key"]} for n, s in pdns.items()},
    }

    async with async_session() as db:
        existing = await db.scalar(select(func.count(User.id)))
        if existing:
            raise SystemExit(f"Datenbank ist nicht leer ({existing} Benutzer) – Seed nur auf frischer DB")

        # --- Benutzer ---------------------------------------------------------------
        admin_pw = "Seed-Admin-" + secrets.token_urlsafe(9)
        alice_pw = "Seed-Alice-" + secrets.token_urlsafe(9)
        bob_pw = "Seed-Bob-" + secrets.token_urlsafe(9)
        totp_secret = pyotp.random_base32()
        admin = User(username="admin", email="admin@e2e-seed.test", hashed_password=hash_password(admin_pw),
                     display_name="Seed Admin", role="admin", is_active=True, preferred_language="de")
        alice = User(username="alice", email="alice@e2e-seed.test", hashed_password=hash_password(alice_pw),
                     display_name="Alice TOTP", role="user", is_active=True, preferred_language="en",
                     totp_enabled=True, totp_secret=totp_secret)
        bob = User(username="bob", email="bob@e2e-seed.test", hashed_password=hash_password(bob_pw),
                   display_name="Bob ohne Zonen", role="user", is_active=True)
        db.add_all([admin, alice, bob])
        await db.flush()

        # --- Server (Klartext-Key wie 2.4.1) ---------------------------------------
        for idx, (name, srv) in enumerate(pdns.items()):
            db.add(ServerConfig(name=name, display_name=name.upper(), url=srv["url"], api_key=srv["api_key"],
                                description="Seed 2.4.1", is_active=True, allow_writes=True, sort_order=idx))

        # --- Zonenrechte -----------------------------------------------------------
        db.add_all([
            UserZoneAccess(user_id=alice.id, zone_name=ZONES["alpha"], permission="manage"),
            UserZoneAccess(user_id=alice.id, zone_name=ZONES["beta"], permission="read"),
        ])

        # --- Webhooks (Secret im Klartext wie 2.4.1) -------------------------------
        hooks = []
        for owner, name, path, events in (
            (admin, "seed-admin", "/hook/seed-admin", ["*"]),
            (alice, "seed-alice", "/hook/seed-alice", ["record"]),
        ):
            secret = secrets.token_urlsafe(32)
            row = Webhook(user_id=owner.id, name=name, url=receiver + path, secret=secret,
                          events=events, is_active=True)
            db.add(row)
            await db.flush()
            hooks.append({"id": row.id, "user": owner.username, "name": name, "url": row.url,
                          "path": path, "secret": secret, "events": events})

        # --- Panel-Tokens (Hash/Prefix ueber den 2.4.1-Service) ---------------------
        tokens, token_ids = {}, {}
        for key, owner, name in (("admin", admin, "seed-admin"), ("alice", alice, "seed-alice"),
                                 ("admin_inactive", admin, "seed-admin-revoked")):
            row, plain = await panel_token_service.create_token(db, owner.id, name)
            assert plain.startswith(PANEL_TOKEN_PREFIX)
            if key == "admin_inactive":
                row.is_active = False
                row.last_used_at = now - timedelta(days=30)
            tokens[key] = plain
            token_ids[key] = row.id
        await db.flush()

        # --- ACME-Token --------------------------------------------------------------
        acme_row, acme_plain = await acme_service.create_token(
            db, name="seed-certbot", allowed_zones=[ZONES["alpha"]], created_by_id=admin.id)

        # --- Geheimnisse in system_settings (Captcha bleibt deaktiviert) -----------
        smtp_password = "Seed-Smtp-" + secrets.token_urlsafe(9)
        captcha_secret = "seed-captcha-" + secrets.token_urlsafe(12)
        for k, v in (
            ("smtp_host", "smtp.e2e-seed.test"), ("smtp_port", "587"), ("smtp_username", "mailer"),
            ("smtp_password", smtp_password), ("smtp_from_email", "dns@e2e-seed.test"),
            ("smtp_from_name", "PDNS Seed"), ("smtp_encryption", "starttls"), ("smtp_enabled", "false"),
            ("captcha_provider", "none"), ("captcha_site_key", ""), ("captcha_secret_key", captcha_secret),
        ):
            db.add(SystemSetting(key=k, value=v))

        # --- Audit-Zeilen im v1-Format ---------------------------------------------
        audit_rows = [
            dict(action="CREATE", resource_type="zone", resource_name=ZONES["alpha"], server_name="ns1",
                 details={"kind": "Native", "nameservers": [f"ns1.{SEED_DOMAIN}", f"ns2.{SEED_DOMAIN}"],
                          "dnssec": False}, user_id=admin.id),
            dict(action="CREATE", resource_type="record", resource_name=f"www.{ZONES['alpha']}", server_name="ns1",
                 details={"zone": ZONES["alpha"], "type": "A", "ttl": 300, "records": ["192.0.2.1"],
                          "fanout": {"ns1": "saved", "ns2": "saved"}}, user_id=alice.id),
            dict(action="UPDATE", resource_type="record", resource_name=f"www.{ZONES['alpha']}", server_name="ns1",
                 details={"zone": ZONES["alpha"], "type": "A", "old": "192.0.2.9", "new": "192.0.2.1",
                          "fanout": {"ns1": "saved", "ns2": "saved"}}, user_id=alice.id),
            dict(action="DELETE", resource_type="record", resource_name=f"old.{ZONES['beta']}", server_name="ns1",
                 details={"zone": ZONES["beta"], "type": "TXT", "content": None,
                          "fanout": {"ns1": "deleted", "ns2": "deleted"}}, user_id=admin.id),
            dict(action="CREATE", resource_type="record", resource_name=f"bad.{ZONES['gamma']}", server_name="ns1",
                 details={"zone": ZONES["gamma"], "type": "A", "ttl": 300, "records": []},
                 status="error", error_message="RRset bad.gamma.e2e-seed.test. IN A: Conflicts with pre-existing RRset",
                 user_id=admin.id),
            dict(action="LOGIN", resource_type="user", resource_name="alice", server_name=None,
                 details={"ip": "192.0.2.99"}, user_id=alice.id),
            dict(action="PANEL_TOKEN_CREATE", resource_type="user", resource_name="admin", server_name=None,
                 details={"token_id": token_ids["admin"], "name": "seed-admin"}, user_id=admin.id),
        ]
        audit_ids = []
        for i, spec in enumerate(audit_rows):
            row = AuditLog(timestamp=now - timedelta(days=3, minutes=len(audit_rows) - i),
                           status=spec.pop("status", "success"), **spec)
            db.add(row)
            await db.flush()
            audit_ids.append(row.id)

        await db.commit()

        out.update({
            "admin": {"id": admin.id, "username": "admin", "password": admin_pw},
            "users": {
                "alice": {"id": alice.id, "username": "alice", "password": alice_pw, "totp_secret": totp_secret,
                          "zones": {ZONES["alpha"]: "manage", ZONES["beta"]: "read"}},
                "bob": {"id": bob.id, "username": "bob", "password": bob_pw, "zones": {}},
            },
            "tokens": tokens,
            "token_ids": token_ids,
            "acme_token": {"id": acme_row.id, "plaintext": acme_plain, "allowed_zones": [ZONES["alpha"]]},
            "webhooks": hooks,
            "settings": {"smtp_password": smtp_password, "captcha_secret_key": captcha_secret},
            "audit_ids": audit_ids,
        })

    await engine.dispose()
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=True)
    _log(f"Seed fertig: {len(out['users']) + 1} Benutzer, {len(tokens)} Panel-Tokens, "
         f"{len(hooks)} Webhooks, {len(audit_ids)} Audit-Zeilen -> {out_path}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="Pfad der Ausgabedatei (JSON mit Klartext-Testgeheimnissen)")
    ap.add_argument("--expect-version", default=None, help="Abbruch, wenn der Quellstand eine andere Version meldet")
    args = ap.parse_args()
    asyncio.run(seed(args.out, args.expect_version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
