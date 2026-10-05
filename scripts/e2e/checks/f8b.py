"""E2E-Checks fuer WS-F8b (Frontend-Funde aus F8, Vorlagen-TTL).

Neuinstallation und Upgrade pruefen dasselbe:
- Vorlagen: TTL ausserhalb 60..604800 wird beim Anlegen/Aendern mit 422 abgelehnt (F8 3.8), gueltige
  Grenzwerte werden gespeichert; bestehende Vorlagen bleiben lesbar.
- Ausgelieferte Oberflaeche: index.html laedt keine Google Fonts mehr, die Schrift kommt als eigene
  woff2-Datei vom Panel selbst (F8-B04, passend zum CSP-Default ohne Fremd-Font-Hosts).
"""
from __future__ import annotations

import re

TTL_MIN = 60
TTL_MAX = 604800
FOREIGN_FONT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


def _check_template_ttl(ctx) -> None:
    name = ctx.unique("f8b-tpl")

    with ctx.step("Vorlagen sind lesbar"):
        data = ctx.api("GET", "templates", expect=200).json()
        ctx.check(isinstance(data.get("templates"), list), f"GET templates: {data}")

    with ctx.step("Vorlage mit TTL ausserhalb der Grenzen wird abgelehnt (422)"):
        ctx.api("POST", "templates", json={"name": name, "default_ttl": TTL_MIN - 1}, expect=422)
        ctx.api("POST", "templates", json={"name": name, "default_ttl": TTL_MAX + 1}, expect=422)
        ctx.api("POST", "templates", json={
            "name": name,
            "records": [{"name": "@", "type": "A", "content": "192.0.2.10", "ttl": 30}],
        }, expect=422)
        listed = [t["name"] for t in ctx.api("GET", "templates", expect=200).json()["templates"]]
        ctx.check(name not in listed, "abgelehnte Vorlage wurde trotzdem gespeichert")

    with ctx.step("Vorlage mit Grenzwerten wird gespeichert"):
        res = ctx.api("POST", "templates", json={
            "name": name,
            "default_ttl": TTL_MIN,
            "records": [{"name": "www", "type": "A", "content": "192.0.2.10", "ttl": TTL_MAX}],
        }, expect=200).json()
        tpl_id = res.get("id")
        ctx.check(isinstance(tpl_id, int), f"Antwort ohne id: {res}")
        ctx.cleanup(lambda: ctx.api("DELETE", f"templates/{tpl_id}"), "Vorlage loeschen")
        stored = next((t for t in ctx.api("GET", "templates", expect=200).json()["templates"] if t["id"] == tpl_id), None)
        ctx.check(stored is not None, "gespeicherte Vorlage fehlt in der Liste")
        ctx.eq(stored.get("default_ttl"), TTL_MIN, "default_ttl")
        ctx.eq([r.get("ttl") for r in stored.get("records", [])], [TTL_MAX], "Record-TTL")

    with ctx.step("Aendern mit ungueltiger TTL wird abgelehnt, Wert bleibt"):
        ctx.api("PUT", f"templates/{tpl_id}", json={"default_ttl": TTL_MAX + 1}, expect=422)
        stored = next(t for t in ctx.api("GET", "templates", expect=200).json()["templates"] if t["id"] == tpl_id)
        ctx.eq(stored.get("default_ttl"), TTL_MIN, "default_ttl nach abgelehntem Update")


def _check_local_font(ctx) -> None:
    with ctx.step("index.html ohne Google Fonts"):
        r = ctx.api("GET", "/", token=None, expect=200)
        ctx.check("text/html" in r.headers.get("content-type", ""), f"/ liefert kein HTML: {r.headers}")
        html = r.text
        for host in FOREIGN_FONT_HOSTS:
            ctx.check(host not in html, f"index.html verweist noch auf {host}")
        css_paths = re.findall(r'href="(/assets/[^"]+\.css)"', html)
        ctx.check(bool(css_paths), "index.html ohne Stylesheet unter /assets/")

    with ctx.step("Schrift wird lokal ausgeliefert"):
        css = "".join(ctx.api("GET", p, token=None, expect=200).text for p in css_paths)
        for host in FOREIGN_FONT_HOSTS:
            ctx.check(host not in css, f"Stylesheet verweist auf {host}")
        ctx.check("Inter Variable" in css, "@font-face 'Inter Variable' fehlt im Stylesheet")
        fonts = re.findall(r"url\((/assets/[^)\"']+\.woff2)\)", css)
        ctx.check(bool(fonts), "keine lokale woff2-Datei im Stylesheet")
        font = ctx.api("GET", fonts[0], token=None, expect=200)
        ctx.check(len(font.body) > 1000, f"Schriftdatei {fonts[0]} ist leer/zu klein")


def check_fresh(ctx) -> None:
    _check_template_ttl(ctx)
    _check_local_font(ctx)


def check_upgrade(ctx) -> None:
    _check_template_ttl(ctx)
    _check_local_font(ctx)
