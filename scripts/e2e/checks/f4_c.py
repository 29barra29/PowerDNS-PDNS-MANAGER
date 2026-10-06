"""E2E-Checks fuer WS-F4-C (DNSSEC Teil B: Elternzonen-DS, DNSKEY auf den Nameservern, 409 parent_ds_present).

Neuinstallation (eigene Zonen nur auf ns1, die Seed-/Fremdzonen bleiben unberuehrt):
- DNS-Pruefungen per Admin-Session einschalten (Resolver = pdns1, autoritative NS an); Einstellungen danach zurueck.
- Elternzone ``f4c-….e2e.test.`` und Kindzone ``sub.f4c-….e2e.test.`` (NS ``ns1.<kind>`` mit Glue auf pdns1) auf ns1,
  DNSSEC fuer die Kindzone aktivieren -> ``capabilities.parent_ds_check``/``dnskey_check``.
- ``dnskey-check``: pdns1 liefert den aktiven Schluessel; unbekannter Tag -> ``missing_tags``; nach Pre-Publish
  eines neuen Schluessels liefert pdns1 auch ihn.
- ``parent-ds``: ohne DS in der Elternzone kein Treffer; nach dem Eintragen des DS (direkt in PowerDNS) sichtbar,
  der neue Schluessel fehlt noch. Deaktivieren -> 409 ``parent_ds_present``; mit ``force`` -> 200, Audit
  ``parent_ds = present_forced``.
- Rechte: Benutzer ohne Zonenrecht -> 403 ohne Zonennamen.
Upgrade: Pruefungen nach dem Upgrade aus (``enabled=false``, keine Abfragen), alice darf alpha pruefen, gamma nicht.
"""
from __future__ import annotations

import json
import socket
import time
from urllib.parse import quote

SRV = "ns1"
PROP_KEYS = ("enabled", "check_authoritative", "ipv6", "resolvers")


def _z(zone: str) -> str:
    return quote(zone, safe=".")


def _call(ctx, method: str, path: str, expect: int, **kw):
    """Aufruf mit einmaligem Warten bei 429 (das Rate-Limit teilen sich alle DNS-Pruefungen eines Benutzers, auch
    die des Propagations-Checks aus anderen Modulen)."""
    r = ctx.api(method, path, **kw)
    if r.status == 429 and expect != 429:
        wait = min(61, int(r.headers.get("retry-after", "30") or 30))
        ctx.log(f"Rate-Limit, warte {wait} s")
        time.sleep(wait)
        r = ctx.api(method, path, **kw)
    ctx.check(r.status == expect, f"{method} {path}: HTTP {r.status} statt {expect}: {r.text[:300]}")
    return r


def _get(ctx, path: str, expect: int = 200, **kw):
    return _call(ctx, "GET", path, expect, **kw)


def _flush(ctx, *zones: str) -> None:
    """Paket-Cache von pdns1 leeren, damit neue Schluessel/DS sofort ausgeliefert werden."""
    for zone in zones:
        ctx.pdns.request(SRV, "PUT", f"/cache/flush?domain={_z(zone)}", expect=200)


def _drop(ctx, zone: str) -> None:
    ctx.api("DELETE", f"zones/{SRV}/{_z(zone)}")
    for srv in ctx.pdns.servers:
        if ctx.pdns.zone(srv, zone) is not None:
            ctx.pdns.delete_zone(srv, zone)


def check_fresh(ctx) -> None:
    sess = ctx.admin_session
    try:
        ip1 = socket.gethostbyname("pdns1")
    except OSError as exc:
        ctx.fail(f"pdns1 im E2E-Netz nicht aufloesbar: {exc}")

    old = sess.get("settings/propagation", expect=200).json()
    ctx.cleanup(lambda: sess.put("settings/propagation", json={k: old[k] for k in PROP_KEYS}),
                "Propagations-Einstellungen zuruecksetzen")
    parent = ctx.unique_zone("f4c")
    child = f"sub.{parent}"
    ns_child = f"ns1.{child}"
    ctx.cleanup(lambda: _drop(ctx, parent), "Elternzone entfernen")
    ctx.cleanup(lambda: _drop(ctx, child), "Kindzone entfernen")
    base = f"dnssec/{SRV}/{_z(child)}"

    with ctx.step("Ohne Freigabe: keine DNS-Abfragen"):
        sess.put("settings/propagation", json={"enabled": False}, expect=200)
        ctx.api("POST", "zones", json={"name": child, "kind": "Native", "nameservers": [ns_child], "servers": [SRV]},
                expect=200)
        ctx.api("POST", f"records/{SRV}/{_z(child)}", json={
            "name": ns_child, "type": "A", "ttl": 300, "records": [{"content": ip1}]}, expect=200)
        caps = ctx.api("GET", f"{base}/status", expect=200).json()["capabilities"]
        ctx.eq((caps["parent_ds_check"], caps["dnskey_check"]), (False, False), "capabilities ohne Freigabe")
        p = _get(ctx, f"{base}/parent-ds", expect=200).json()
        ctx.eq((p["enabled"], p["resolvers"]), (False, []), "parent-ds ohne Freigabe")
        d = _get(ctx, f"{base}/dnskey-check", expect=200).json()
        ctx.eq((d["enabled"], d["nameservers"]), (False, {}), "dnskey-check ohne Freigabe")

    with ctx.step("DNS-Pruefungen freigeben (Resolver = pdns1), Elternzone anlegen, DNSSEC aktivieren"):
        sess.put("settings/propagation", json={"enabled": True, "check_authoritative": True, "resolvers": [ip1]},
                 expect=200)
        ctx.api("POST", "zones", json={"name": parent, "kind": "Native", "nameservers": ["ns1.e2e.test."],
                                       "servers": [SRV]}, expect=200)
        ctx.pdns.request(SRV, "PATCH", f"/zones/{_z(parent)}", json_body={"rrsets": [{
            "name": child, "type": "NS", "ttl": 300, "changetype": "REPLACE",
            "records": [{"content": ns_child, "disabled": False}]}]}, expect=(200, 204))
        ctx.api("POST", f"{base}/enable", json={}, expect=200)
        st = ctx.api("GET", f"{base}/status", expect=200).json()
        ctx.eq((st["capabilities"]["parent_ds_check"], st["capabilities"]["dnskey_check"]), (True, True),
               "capabilities mit Freigabe")
        ctx.eq(len(st["keys"]), 1, "ein Schluessel nach dem Aktivieren")
        old_key = st["keys"][0]
        old_tag = old_key["key_tag"]
        ds_line = next(x["ds"] for x in old_key["ds"] if x.get("digest_type") == 2)
        _flush(ctx, parent, child)

    with ctx.step("dnskey-check: pdns1 liefert den aktiven Schluessel"):
        d = _get(ctx, f"{base}/dnskey-check", expect=200).json()
        ctx.eq(d["enabled"], True, "enabled")
        ctx.eq(d["expected_tags"], [old_tag], "erwartet: veroeffentlichte Schluessel")
        ns = d["nameservers"].get(ns_child)
        ctx.check(ns is not None, f"Nameserver {ns_child} fehlt: {d['nameservers']}")
        ctx.check(ns["ok"] is True and old_tag in ns["serves_key_tags"], f"Nameserver-Zeile: {ns}")
        ctx.eq(ns["addresses"][0]["ip"], ip1, "Glue-Adresse")
        ctx.eq(d["all_ok"], True, "all_ok")
        bogus = 1 if old_tag != 1 else 2
        d = _get(ctx, f"{base}/dnskey-check?key_tag={bogus}", expect=200).json()
        ctx.eq((d["all_ok"], d["nameservers"][ns_child]["missing_tags"]), (False, [bogus]), "unbekannter Tag fehlt")

    with ctx.step("Pre-Publish: neuer Schluessel wird ausgeliefert"):
        created = ctx.api("POST", f"{base}/keys", json={"keytype": "csk", "active": False, "published": True},
                          expect=200).json()["details"]["key"]
        new_tag = created["key_tag"]
        _flush(ctx, child)
        d = _get(ctx, f"{base}/dnskey-check?key_tag={new_tag}", expect=200).json()
        ctx.check(d["all_ok"] is True and new_tag in d["nameservers"][ns_child]["serves_key_tags"],
                  f"neuer Schluessel {new_tag} nicht ausgeliefert: {d}")

    with ctx.step("parent-ds: ohne DS in der Elternzone kein Treffer"):
        p = _get(ctx, f"{base}/parent-ds", expect=200).json()
        ctx.eq(p["enabled"], True, "enabled")
        row = p["resolvers"][0]
        ctx.check(row["resolver"] == ip1 and row["status"] in ("nodata", "nxdomain"), f"Resolver-Zeile: {row}")
        ctx.eq(p["any_visible"], False, "any_visible")
        ctx.eq(p["keys"][str(old_key["id"])]["missing_on"], [ip1], "alter Schluessel fehlt bei pdns1")

    with ctx.step("parent-ds: DS in der Elternzone wird erkannt"):
        ctx.pdns.request(SRV, "PATCH", f"/zones/{_z(parent)}", json_body={"rrsets": [{
            "name": child, "type": "DS", "ttl": 300, "changetype": "REPLACE",
            "records": [{"content": ds_line, "disabled": False}]}]}, expect=(200, 204))
        _flush(ctx, parent, child)
        p = _get(ctx, f"{base}/parent-ds", expect=200).json()
        row = p["resolvers"][0]
        ctx.check(row["status"] == "ok" and row["key_tags"] == [old_tag], f"Resolver-Zeile: {row}")
        ctx.eq(p["keys"][str(old_key["id"])]["visible_on"], [ip1], "alter Schluessel sichtbar")
        ctx.eq(p["keys"][str(created["id"])]["missing_on"], [ip1], "neuer Schluessel noch nicht bei den Eltern")
        ctx.eq((p["any_visible"], p["unknown_tags"]), (True, []), "any_visible/unknown_tags")

    with ctx.step("Deaktivieren: 409 parent_ds_present, mit force erlaubt"):
        r = _call(ctx, "POST", f"{base}/disable", 409, json={})
        detail = r.json()["detail"]
        ctx.eq((detail["code"], detail["force_possible"]), ("parent_ds_present", True), "Schutzregel")
        ctx.check(ip1 in detail["message"], f"409-Text nennt den Resolver nicht: {detail}")
        keys = ctx.pdns.request(SRV, "GET", f"/zones/{_z(child)}/cryptokeys", expect=200).json()
        ctx.eq(len(keys), 2, "Schluessel nach 409 unveraendert")
        r = _call(ctx, "POST", f"{base}/disable", 200, json={"force": True})
        ctx.eq(r.json()["details"]["already_disabled"], False, "deaktiviert")
        rows = ctx.db("SELECT details FROM audit_logs WHERE zone_name = %s AND action = 'DNSSEC_DISABLE' "
                      "AND status = 'success' ORDER BY id DESC LIMIT 1", (child,))
        ctx.check(rows, "Audit DNSSEC_DISABLE fehlt")
        details = rows[0]["details"]
        details = json.loads(details) if isinstance(details, (str, bytes)) else details
        ctx.eq((details.get("parent_ds"), details.get("force")), ("present_forced", True), "Audit parent_ds")

    with ctx.step("Rechte: Benutzer ohne Zonenrecht"):
        if ctx.user_token:
            for suffix in ("parent-ds", "dnskey-check"):
                r = ctx.api("GET", f"{base}/{suffix}", token=ctx.user_token, expect=403)
                ctx.check(child not in r.text, f"403 verraet den Zonennamen ({suffix})")


def check_upgrade(ctx) -> None:
    zones = ctx.seed.get("zones", {})
    if "alpha" not in zones or "gamma" not in zones:
        ctx.fail(f"Seed ohne Zonen alpha/gamma: {sorted(zones)}")
    alpha, gamma = zones["alpha"], zones["gamma"]
    with ctx.step("Nach dem Upgrade: Pruefungen aus, keine Abfragen"):
        st = ctx.api("GET", f"dnssec/{SRV}/{_z(alpha)}/status", expect=200).json()
        ctx.eq((st["capabilities"]["parent_ds_check"], st["capabilities"]["dnskey_check"]), (False, False),
               "capabilities nach dem Upgrade")
        p = _get(ctx, f"dnssec/{SRV}/{_z(alpha)}/parent-ds", expect=200).json()
        ctx.eq((p["enabled"], p["resolvers"], p["keys"]), (False, [], {}), "parent-ds")
        d = _get(ctx, f"dnssec/{SRV}/{_z(alpha)}/dnskey-check", expect=200).json()
        ctx.eq((d["enabled"], d["nameservers"]), (False, {}), "dnskey-check")
    with ctx.step("Rechte: alice darf alpha pruefen, gamma nicht"):
        if not ctx.user_token:
            ctx.fail("upgrade ohne user_token (alice)")
        _get(ctx, f"dnssec/{SRV}/{_z(alpha)}/parent-ds", token=ctx.user_token, expect=200)
        r = ctx.api("GET", f"dnssec/{SRV}/{_z(gamma)}/dnskey-check", token=ctx.user_token, expect=403)
        ctx.check(gamma not in r.text, "403 verraet den Zonennamen")
