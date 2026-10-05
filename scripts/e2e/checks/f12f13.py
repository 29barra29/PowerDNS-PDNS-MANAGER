"""E2E-Checks fuer F12/F13 (WS-F12F13-BE): Propagations-Check gegen die beiden E2E-PowerDNS und /metrics mit Token.

Neuinstallation:
- Externe DNS-Abfragen per Admin-Session einschalten (Resolver = pdns1), Zone mit Glue-Nameservern ``ns1``/``ns2``
  (A-Records auf die Adressen von pdns1/pdns2 im Compose-Netz) anlegen. Die beiden PowerDNS haben getrennte
  Datenbanken (sqlite je Container) und sind beide schreibbar -> ``separate_backends``; der Check muss trotzdem
  ``in_sync`` melden (Peer per Inhalts-Fingerprint, DNS-Zeilen per Serial eines Panel-Servers) [D9].
- Record-Vergleich ``www A`` ueber API und DNS; danach Drift direkt auf pdns2 (an PDNS Manager vorbei) -> Peer und
  Nameserver von pdns2 ``mismatch``, Inhaltsvergleich nennt ``www A``.
- Rechte: Panel-Token (auch Admin) -> 403 auf den Monitoring-Einstellungen; Benutzer ohne Zonenrecht -> 403.
- Prometheus: Token erzeugen (verschluesselt in der DB), /metrics aktivieren, Scrape mit Bearer (200, enthaelt die
  Propagations-Zaehler), ohne Token 401; danach Token loeschen -> /metrics wieder 404 (wie vorher, fuer w0_int_be2b).
- ``GET /settings/monitoring/status`` liefert Worker-/Migrations-/Geheimnis-Status.
Upgrade: Seed-Zone ``alpha`` (auf ns1 und ns2 aus 2.4.1) ist ohne externe Abfragen ``in_sync``; alice darf alpha
(manage) pruefen, gamma nicht; Default-Einstellungen nach dem Upgrade.
"""
from __future__ import annotations

import socket
from urllib.parse import quote, urlencode

PROP_KEYS = ("enabled", "check_authoritative", "ipv6", "resolvers")


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _prop_url(server: str, zone: str, **params) -> str:
    q = {k: v for k, v in params.items() if v is not None}
    return f"zones/{server}/{_z(zone)}/propagation" + (f"?{urlencode(q)}" if q else "")


def _row(data: dict, source: str, target: str | None = None) -> dict:
    rows = [s for s in data["sources"] if s["source"] == source and (target is None or s.get("target") == target)]
    if len(rows) != 1:
        raise AssertionError(f"Zeile {source}/{target} nicht eindeutig: {data['sources']}")
    return rows[0]


def _check_settings_need_session(ctx) -> None:
    with ctx.step("Monitoring-Einstellungen nur per Admin-Session"):
        for path in ("settings/propagation", "settings/metrics", "settings/monitoring/status"):
            ctx.api("GET", path, expect=403)  # Admin-Panel-Token (allow_admin) reicht nicht
            ctx.api("GET", path, token=None, expect=401)
        ctx.api("PUT", "settings/propagation", json={"enabled": True}, expect=403)
        ctx.api("POST", "settings/metrics/token", json={}, expect=403)


def _check_status(ctx) -> None:
    with ctx.step("GET /settings/monitoring/status"):
        st = ctx.admin_session.get("settings/monitoring/status", expect=200).json()
        for key in ("ok", "background", "migration_errors", "servers_not_loaded", "secrets", "checked_at"):
            ctx.check(key in st, f"Status ohne {key}: {st}")
        ctx.eq(st["migration_errors"], 0, "Migrationsfehler")
        ctx.eq(st["servers_not_loaded"], {}, "nicht geladene Server")
        ctx.eq(st["secrets"]["mode"], "encrypted", "Geheimnis-Modus")


def _check_metrics(ctx) -> None:
    sess = ctx.admin_session
    before = sess.get("settings/metrics", expect=200).json()
    if before.get("env_override"):
        ctx.skip("METRICS_TOKEN ist in der E2E-Umgebung gesetzt – Panel-Token nicht pruefbar")

    def _drop_token():
        if sess.get("settings/metrics").json().get("token_set") or \
                ctx.db_value("SELECT COUNT(*) FROM system_settings WHERE `key` = 'metrics_token'"):
            sess.delete("settings/metrics/token")

    ctx.cleanup(_drop_token, "Metrik-Token loeschen")
    with ctx.step("Scrape-Token erzeugen (verschluesselt gespeichert)"):
        sess.put("settings/metrics", json={"enabled": True}, expect=409)
        created = sess.post("settings/metrics/token", json={}, expect=201).json()
        tok = created["token"]
        ctx.check(tok.startswith("dnsmgr_metrics_"), f"Token-Format: {created['token_hint']}")
        raw = ctx.db_value("SELECT value FROM system_settings WHERE `key` = 'metrics_token'")
        ctx.check(str(raw).startswith("enc:v1:") and tok not in str(raw), "metrics_token nicht verschluesselt")
        audit = ctx.db("SELECT details FROM audit_logs WHERE action = 'METRICS_TOKEN_CREATE' ORDER BY id DESC LIMIT 1")
        ctx.check(audit and tok not in str(audit[0]["details"]), "Token im Audit-Log")
        info = sess.get("settings/metrics", expect=200).json()
        ctx.check(info["token_set"] and not info["effective_enabled"], f"Metrik-Status nach dem Erzeugen: {info}")
        ctx.api("GET", "/metrics", token=tok, expect=404)  # noch nicht aktiviert

    with ctx.step("/metrics aktivieren und mit Bearer abrufen"):
        sess.put("settings/metrics", json={"enabled": True}, expect=200)
        r = ctx.wait_until(lambda: (lambda x: x if x.status == 200 else None)(ctx.api("GET", "/metrics", token=tok)),
                           timeout=20, interval=1, msg="/metrics wird nach dem Aktivieren nicht 200")
        ctx.check(r.headers.get("content-type", "").startswith("text/plain"), f"Content-Type {r.headers}")
        body = r.text
        for needle in ('pdnsmgr_info{version="', "pdnsmgr_http_requests_total", "pdnsmgr_pdns_server_up",
                       'pdnsmgr_propagation_checks_total{result="in_sync"}', "pdnsmgr_database_up 1.0",
                       "pdnsmgr_migration_errors 0.0"):
            ctx.check(needle in body, f"/metrics ohne {needle}")
        for line in body.splitlines():
            if line.startswith('pdnsmgr_propagation_checks_total{result="in_sync"}'):
                ctx.check(float(line.split()[-1]) >= 1, f"keine Propagations-Pruefung gezaehlt: {line}")
        ctx.check(".e2e.test" not in body, "Zonenname taucht in den Metriken auf")
        ctx.api("GET", "/metrics", token=None, expect=401)
        ctx.api("GET", "/metrics", token="falscher-token-mit-genug-zeichen", expect=401)

    with ctx.step("Token loeschen -> /metrics wieder 404"):
        sess.delete("settings/metrics/token", expect=200)
        ctx.wait_until(lambda: ctx.api("GET", "/metrics", token=tok).status == 404, timeout=20, interval=1,
                       msg="/metrics nach dem Loeschen nicht 404")
        ctx.eq(sess.get("settings/metrics", expect=200).json()["enabled"], False, "metrics_enabled nach dem Loeschen")


def check_fresh(ctx) -> None:
    _check_settings_need_session(ctx)
    sess = ctx.admin_session
    try:
        ip1, ip2 = socket.gethostbyname("pdns1"), socket.gethostbyname("pdns2")
    except OSError as exc:
        ctx.fail(f"pdns1/pdns2 im E2E-Netz nicht aufloesbar: {exc}")

    old = sess.get("settings/propagation", expect=200).json()
    ctx.check(old["enabled"] is False, f"Externe Abfragen nach Neuinstallation an: {old}")
    ctx.cleanup(lambda: sess.put("settings/propagation", json={k: old[k] for k in PROP_KEYS}),
                "Propagations-Einstellungen zuruecksetzen")
    with ctx.step("Externe Abfragen erlauben (Resolver = pdns1)"):
        r = sess.put("settings/propagation", json={"resolvers": ["dns.example"]}, expect=422)
        ctx.check("Ungültige Resolver-Adresse" in r.json().get("detail", ""), f"422-Text: {r.text}")
        got = sess.put("settings/propagation", json={"enabled": True, "check_authoritative": True,
                                                     "resolvers": [ip1, ip1]}, expect=200).json()
        ctx.eq(got["resolvers"], [ip1], "Resolver-Liste (dedupliziert)")
        audit = ctx.db_value("SELECT COUNT(*) FROM audit_logs WHERE action = 'PROPAGATION_SETTINGS_UPDATE'")
        ctx.check(audit >= 1, "kein Audit-Eintrag PROPAGATION_SETTINGS_UPDATE")

    zone = ctx.unique_zone("f12")

    def _drop_zone():
        ctx.api("DELETE", f"zones/ns1/{_z(zone)}")
        for srv in ctx.pdns.servers:
            if ctx.pdns.zone(srv, zone) is not None:
                ctx.pdns.delete_zone(srv, zone)

    ctx.cleanup(_drop_zone, "Zone entfernen")
    with ctx.step("Zone mit Glue-Nameservern auf ns1/ns2 anlegen"):
        ctx.api("POST", "zones", json={"name": zone, "kind": "Native",
                                       "nameservers": [f"ns1.{zone}", f"ns2.{zone}"]}, expect=200)
        for host, ip in ((f"ns1.{zone}", ip1), (f"ns2.{zone}", ip2), (f"www.{zone}", "192.0.2.80")):
            ctx.api("POST", f"records/ns1/{_z(zone)}", json={
                "name": host, "type": "A", "ttl": 300, "records": [{"content": ip}]}, expect=200)
        for srv in ctx.pdns.servers:
            ctx.eq(ctx.pdns.contents(srv, zone, f"www.{zone}", "A"), ["192.0.2.80"], f"www auf {srv}")

    with ctx.step("Propagation: Panel-Server, Nameserver und Resolver aktuell"):
        def in_sync():
            d = ctx.api("GET", _prop_url("ns1", zone), expect=200).json()
            return d if d["summary"]["in_sync"] else None

        data = ctx.wait_until(in_sync, timeout=35, interval=4, msg="Propagation wird nicht in_sync")
        ctx.eq(data["zone"], zone, "Zone")
        ctx.check(data["sources"][0]["is_reference"] and data["sources"][0]["source"] == "ns1", "Referenzzeile")
        ctx.eq(data["separate_backends"], True, "getrennte Backends (zwei schreibbare Server)")
        ctx.eq(_row(data, "ns2")["status"], "ok", "Peer ns2")
        for host, ip in ((f"ns1.{zone}", ip1), (f"ns2.{zone}", ip2)):
            row = _row(data, host, ip)
            ctx.eq(row["status"], "ok", f"Nameserver {host}")
            ctx.check("ns_from_glue" in row["notes"] and row["authoritative"] is True, f"Nameserver-Zeile: {row}")
        ctx.eq(_row(data, ip1)["kind"], "resolver", "Resolver-Zeile")
        ctx.eq(data["summary"]["mismatch"] + data["summary"]["failed"], 0, "keine Abweichung")

    with ctx.step("Record-Vergleich www A"):
        data = ctx.api("GET", _prop_url("ns1", zone, name="www", type="A"), expect=200).json()
        ctx.eq(data["record"]["expected_values"], ["192.0.2.80"], "erwartete Werte")
        for s in data["sources"]:
            if s["status"] != "skipped":
                ctx.eq(s["record_match"], True, f"record_match {s['source']}/{s.get('target')}")
        ctx.api("GET", _prop_url("ns1", zone, name="www"), expect=422)
        ctx.api("GET", _prop_url("ns1", zone, name="www.anders.test.", type="A"), expect=422)

    with ctx.step("Drift auf pdns2 (an PDNS Manager vorbei) wird erkannt"):
        ctx.pdns.request("ns2", "PATCH", f"/zones/{zone}", json_body={"rrsets": [{
            "name": f"www.{zone}", "type": "A", "ttl": 300, "changetype": "REPLACE",
            "records": [{"content": "192.0.2.99", "disabled": False}]}]}, expect=(200, 204))

        def drifted():
            d = ctx.api("GET", _prop_url("ns1", zone, name="www", type="A", content="true"), expect=200).json()
            return d if _row(d, "ns2")["status"] == "mismatch" else None

        data = ctx.wait_until(drifted, timeout=30, interval=4, msg="Drift auf ns2 nicht erkannt")
        peer = _row(data, "ns2")
        ctx.check(peer["content_match"] is False and f"www.{zone} A" in peer["content_diff_sample"],
                  f"Inhaltsvergleich ns2: {peer}")
        ctx.eq(peer["record_values"], ["192.0.2.99"], "Record auf ns2")
        ctx.eq(_row(data, f"ns2.{zone}", ip2)["record_match"], False, "DNS-Antwort von pdns2")
        ctx.eq(_row(data, f"ns1.{zone}", ip1)["record_match"], True, "DNS-Antwort von pdns1")
        ctx.eq(data["summary"]["in_sync"], False, "in_sync trotz Drift")

    with ctx.step("Rechte: Benutzer ohne Zonenrecht, unbekannter Server"):
        if ctx.user_token:
            r = ctx.api("GET", _prop_url("ns1", zone), token=ctx.user_token, expect=403)
            ctx.check(zone not in r.text, "403 verraet den Zonennamen")
        r = ctx.api("GET", _prop_url("nsX", zone), expect=404)
        ctx.check("nicht konfiguriert" in r.text, f"404-Text: {r.text}")

    _check_status(ctx)
    _check_metrics(ctx)


def check_upgrade(ctx) -> None:
    _check_settings_need_session(ctx)
    zones = ctx.seed.get("zones", {})
    if "alpha" not in zones or "gamma" not in zones:
        ctx.fail(f"Seed ohne Zonen alpha/gamma: {sorted(zones)}")
    alpha, gamma = zones["alpha"], zones["gamma"]

    with ctx.step("Default-Einstellungen nach dem Upgrade"):
        p = ctx.admin_session.get("settings/propagation", expect=200).json()
        ctx.eq((p["enabled"], p["check_authoritative"], p["ipv6"]), (False, True, False), "Propagations-Defaults")
        ctx.eq(p["resolvers"], p["default_resolvers"], "Resolver-Defaults")
        m = ctx.admin_session.get("settings/metrics", expect=200).json()
        ctx.check(not m["effective_enabled"] and not m["token_set"], f"Metrik-Defaults: {m}")

    with ctx.step("Seed-Zone alpha ohne externe Abfragen in_sync"):
        data = ctx.api("GET", _prop_url("ns1", alpha), expect=200).json()
        ctx.eq({s["kind"] for s in data["sources"]}, {"panel-api"}, "nur Panel-Server")
        ctx.eq(_row(data, "ns2")["status"], "ok", f"Peer ns2: {_row(data, 'ns2')}")
        ctx.eq(data["summary"]["in_sync"], True, "alpha in_sync")
        data = ctx.api("GET", _prop_url("ns1", alpha, name="www", type="A", content="true"), expect=200).json()
        ctx.eq(_row(data, "ns2")["content_match"], True, "Inhalt alpha auf ns2")

    with ctx.step("alice: alpha erlaubt, gamma verboten"):
        if not ctx.user_token:
            ctx.fail("Upgrade-Pfad ohne Token fuer alice")
        ctx.api("GET", _prop_url("ns1", alpha), token=ctx.user_token, expect=200)
        r = ctx.api("GET", _prop_url("ns1", gamma), token=ctx.user_token, expect=403)
        ctx.check(gamma not in r.text, "403 verraet den Zonennamen")

    _check_status(ctx)
