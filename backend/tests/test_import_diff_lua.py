"""F15 9.2 – Import-Vorschau mit LUA/ALIAS (``zone_import_diff``) und das Import-Gate in ``routers/zones.py``.

Ohne DB: Routen ueber ``authfakes.build_app`` mit ``FakeSession``; PowerDNS ueber ``fakes.pdns.FakePowerDNSClient``.
Hinweis: ``tests/test_bind_fragment_parser.py`` (F1) nutzt denselben Scanner ``split_logical_lines`` und laeuft
bei Aenderungen hier mit.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from authfakes import FakeSession, build_app, make_user
from fakes.pdns import FakePowerDNSClient, make_zone, rr
from app.core.auth import create_access_token
from app.models.models import ServerConfig, SystemSetting
from app.routers import zones
from app.services import audit as audit_service
from app.services import zone_import_diff as zid
from app.services.lua_records import normalize_lua_content
from app.services.pdns_client import pdns_manager

LUA_A = "A \"ifportup(443, {'192.0.2.1', '192.0.2.2'})\""
# dnspython verlangt einen SOA am Ursprung (wie bisher in der Vorschau): kleiner Kopf mit 4 Zeilen
HEADER = "$ORIGIN example.com.\n$TTL 60\n@ IN SOA ns1 hostmaster 1 2 3 4 5\n@ IN NS ns1\n"

ZONE_FILE = """$ORIGIN example.com.
$TTL 3600
@       IN SOA ns1.example.com. hostmaster.example.com. ( 2026100601 10800 3600
                604800 3600 )
        IN NS  ns1.example.com.
www 60  IN LUA A "ifportup(443, {'192.0.2.1', '192.0.2.2'})"
multi   IN LUA ( A ";if country({'DE'}) then return '192.0.2.10' end return '198.51.100.10'" ) ; Kommentar
        IN LUA AAAA "ifportup(443, {'2001:db8::1'})"
txt     IN LUA TXT "latlon()"
geo     IN ALIAS target
"""


# --------------------------------------------------------------------------- 1: split_logical_lines
def test_split_logical_lines_parens_quotes_comments():
    text = 'a IN SOA ( ns1 host\n  1 2 3\n  4 5 ) ; kommentar\nb IN TXT "x;y" ; rest\nc IN TXT ( "offen"\n'
    lines = zid.split_logical_lines(text)
    assert [(l.start, l.end) for l in lines] == [(1, 3), (4, 4), (5, 5)]
    assert lines[0].text.split() == ["a", "IN", "SOA", "ns1", "host", "1", "2", "3", "4", "5"]
    assert lines[1].text == 'b IN TXT "x;y"'
    assert lines[2].error == "Klammer nicht geschlossen"
    assert lines[0].error is None and lines[1].error is None


# --------------------------------------------------------------------------- 2: rewrite_passthrough_records
def test_rewrite_passthrough_records_keeps_line_count():
    out, found = zid.rewrite_passthrough_records(ZONE_FILE, "example.com")
    assert len(out.splitlines()) == len(ZONE_FILE.splitlines())
    assert [p.type for p in found] == ["LUA", "LUA", "LUA", "LUA", "ALIAS"]
    assert [p.line for p in found] == [6, 7, 8, 9, 10]
    assert found[0].content == LUA_A
    assert found[1].content == "A \";if country({'DE'}) then return '192.0.2.10' end return '198.51.100.10'\""
    assert found[4].content == "target.example.com."
    assert "TYPE65402 \\# " in out and "TYPE65401 \\# " in out
    assert " LUA " not in out and "ALIAS" not in out
    # fuehrender Leerraum (Owner vom Vorgaenger) bleibt erhalten
    assert out.splitlines()[7].startswith("\t")


def test_rewrite_handles_origin_and_absolute_alias():
    text = "$ORIGIN sub.example.com.\nx IN ALIAS y\nz IN ALIAS other.example.net.\n"
    _, found = zid.rewrite_passthrough_records(text, "example.com.")
    assert [p.content for p in found] == ["y.sub.example.com.", "other.example.net."]


# --------------------------------------------------------------------------- 3: neue Zone
def test_build_import_diff_new_zone():
    res = zid.build_import_diff("example.com", ZONE_FILE, None)
    assert res["parse_error"] is None
    assert res["lua_count"] == 4 and res["lua_issues"] == []
    assert {"name": "www.example.com.", "type": "LUA", "content": LUA_A} in res["would_add"]
    assert {"name": "geo.example.com.", "type": "ALIAS", "content": "target.example.com."} in res["would_add"]
    lua = [a for a in res["would_add"] if a["type"] == "LUA"]
    assert len(lua) == 4
    assert {"name": "multi.example.com.", "type": "LUA", "content": "AAAA \"ifportup(443, {'2001:db8::1'})\""} in lua


# --------------------------------------------------------------------------- 4: bestehende Zone, anderer Leerraum
def _existing(lua_content: str = "A  \"ifportup(443, {'192.0.2.1', '192.0.2.2'})\"") -> dict:
    return make_zone("example.com.", [
        rr("example.com.", "SOA", "ns1.example.com. hostmaster.example.com. 2026100601 10800 3600 604800 3600"),
        rr("example.com.", "NS", "ns1.example.com."),
        rr("www.example.com.", "LUA", lua_content, ttl=60),
        rr("multi.example.com.", "LUA",
           "A \";if country({'DE'}) then return '192.0.2.10' end return '198.51.100.10'\"",
           "AAAA \"ifportup(443, {'2001:db8::1'})\""),
        rr("txt.example.com.", "LUA", 'TXT "latlon()"'),
        rr("geo.example.com.", "ALIAS", "Target.Example.com."),
    ])


def test_build_import_diff_existing_zone_whitespace_insensitive():
    res = zid.build_import_diff("example.com", ZONE_FILE, _existing())
    assert res["parse_error"] is None
    assert res["would_add_total"] == 0, res["would_add"]
    assert res["would_remove_total"] == 0, res["would_remove"]
    assert res["unchanged_count"] == 7


# --------------------------------------------------------------------------- 5: ungueltige LUA-Zeile
def test_invalid_lua_line_reported_not_fatal():
    text = HEADER + 'www IN LUA A "x(\\"y\\")"\nok IN LUA A "f()"\nbad IN LUA A "f({1)"\n'
    res = zid.build_import_diff("example.com", text, None)
    assert res["parse_error"] is None
    assert res["lua_count"] == 3
    assert [i["line"] for i in res["lua_issues"]] == [5, 7]
    assert res["lua_issues"][0]["message"].startswith("Doppelte Anführungszeichen im LUA-Code")
    assert res["lua_issues"][1]["message"].startswith("Klammern im LUA-Code")


def test_lua_issues_capped_at_50():
    text = HEADER + "".join(f'h{i} IN LUA A "f(("\n' for i in range(60))
    res = zid.build_import_diff("example.com", text, None)
    assert res["lua_count"] == 60 and len(res["lua_issues"]) == 50


# --------------------------------------------------------------------------- 6: Export-Roundtrip
def test_export_roundtrip_has_no_diff():
    export = (
        "example.com.\t3600\tIN\tSOA\tns1.example.com. hostmaster.example.com. 2026100601 10800 3600 604800 3600\n"
        "example.com.\t3600\tIN\tNS\tns1.example.com.\n"
        "example.com.\t3600\tIN\tTXT\t\"v=spf1 -all\"\n"
        "www.example.com.\t60\tIN\tA\t192.0.2.5\n"
        f"lua.example.com.\t60\tIN\tLUA\t{LUA_A}\n"
    )
    existing = make_zone("example.com.", [
        rr("example.com.", "SOA", "ns1.example.com. hostmaster.example.com. 2026100601 10800 3600 604800 3600"),
        rr("example.com.", "NS", "ns1.example.com."),
        rr("example.com.", "TXT", '"v=spf1 -all"'),
        rr("www.example.com.", "A", "192.0.2.5"),
        rr("lua.example.com.", "LUA", LUA_A, ttl=60),
    ])
    res = zid.build_import_diff("example.com.", export, existing)
    assert res["parse_error"] is None
    assert res["would_add_total"] == 0 and res["would_remove_total"] == 0
    assert res["lua_count"] == 1


# --------------------------------------------------------------------------- 7: Zeilennummern
def test_parse_error_line_number_unchanged_by_rewrite():
    block = 'multi IN LUA ( A "f()"\n   )\n'
    tail = "broken IN A not-an-ip\n"
    with_lua = HEADER + block + tail
    blank = HEADER + "\n" * block.count("\n") + tail
    a = zid.build_import_diff("example.com", with_lua, None)["parse_error"]
    b = zid.build_import_diff("example.com", blank, None)["parse_error"]
    assert a and b and a.startswith("<string>:")
    assert a == b


def test_parse_error_branch_has_lua_keys():
    res = zid.build_import_diff("example.com", HEADER + 'www IN LUA A "f()"\nx IN A kaputt\n', None)
    assert res["parse_error"]
    assert res["lua_count"] == 1 and res["lua_issues"] == []


# --------------------------------------------------------------------------- 8: count_passthrough_records
def test_count_passthrough_never_raises_and_is_conservative():
    assert zid.count_passthrough_records('www IN LUA ( A "f()"\n') == {"LUA": 1, "ALIAS": 0}
    assert zid.count_passthrough_records(None) == {"LUA": 0, "ALIAS": 0}
    assert zid.count_passthrough_records("\x00\x01((((") == {"LUA": 0, "ALIAS": 0}
    # Generic-Schreibweise und $GENERATE zaehlen ebenfalls (kein Umweg am Gate vorbei)
    data = 'A "f()"'.encode()
    generic = f"g IN TYPE65402 \\# {len(data)} {data.hex()}\n"
    assert zid.count_passthrough_records(generic)["LUA"] == 1
    assert zid.count_passthrough_records("$GENERATE 1-5 host$ LUA A \"f()\"\n")["LUA"] == 1
    assert zid.count_passthrough_records(ZONE_FILE) == {"LUA": 4, "ALIAS": 1}
    # Name "lua" ist kein LUA-Record
    assert zid.count_passthrough_records("lua IN A 192.0.2.1\n")["LUA"] == 0


# --------------------------------------------------------------------------- 8b: Import-Gate aus PowerDNS-Sicht
# Review Welle 3: PowerDNS (ZoneParserTNG) liest den Record-Kopf anders als der Panel-Scanner. Jede Zeile der
# Liste HITS hat PowerDNS 4.9 (pdnsutil load-zone, enable-lua-records=yes) als LUA-Record angelegt; die Kopfzeile
# ist die letzte Zeile der Eintraege (HEADER hat 4 Zeilen, der erste Eintrag beginnt in Zeile 5).
GATE_HITS = [
    "www 300 IN IN LUA A \"ifportup(443, {'192.0.2.1'})\"",
    'www IN 300 IN LUA A "x"',
    'b 3600 IN TXT ( "y" ( "z" )\nwww 300 IN LUA A "x"',  # Klammer: PowerDNS verbindet nur bis zur ersten ")"
    'www 300 in lua A "x"',
    'www 300 IN TYPE065402 A "x"',
    'www 300 IN TYPE65402x A "x"',
    'www 300 IN TYPE+65402 A "x"',
    'www 300 IN type65402 A "x"',
    'www 300 IN TYPE\v65402 A "x"',
    'www\t300\tIN\tLUA\tA "x"',
    'www 1H IN LUA A "x"',
    '"x 300 IN LUA A "y"',  # Quote im Owner: PowerDNS trennt den Kopf nicht quote-bewusst
    'w\vw IN LUA A "x"',  # \v trennt bei PowerDNS keine Tokens
    '\u00a0www 300 IN LUA A "x"',  # NBSP gehoert bei PowerDNS zum Owner
    ' \tIN LUA A "x"',  # Owner vom Vorgaenger
    '$GENERATE 1-1 h$ IN LU${9,1,X} A "x"',  # Template ergibt LUA
    '$GENERATE 65402-65402 h IN TYPE$ A "x"',
    '$GENERATE 1-2 h$ IN LUA A "x$"',
    '$GENERATE 1-1 h $ IN LUA A "x"',  # $ als TTL
    "$INCLUDE /etc/hostname",  # Inhalt nicht pruefbar
    'x ; kommentar\rwww 300 IN LUA A "x"',  # die API trennt Zeilen auch an \r
]


@pytest.mark.parametrize("line", GATE_HITS)
def test_lua_gate_lines_finds_pdns_lua_heads(line):
    lines = zid.lua_gate_lines(HEADER + line + "\n")
    assert lines == [5 + line.count("\n")], lines


GATE_MISSES = [
    "lua 300 IN A 192.0.2.1",  # Owner "lua"
    "lua.example.com. IN A 192.0.2.1",
    'www 300 IN TXT "LUA" "a LUA b"',
    "; www 300 IN LUA A \"x\"",
    ' ; www 300 IN LUA A "x"',
    "www 300 IN A 192.0.2.1 ; LUA",
    "$ORIGIN lua.example.com.",
    'www 300 IN TXT "TYPE65402"',
    "$GENERATE 1-100 host$ A 10.0.0.$",
    "$GENERATE 1-100 host$ 300 IN CNAME lua$.example.com.",
    "www 300 IN TYPE654021 \\# 0",  # ausserhalb von uint16
    "@ 3600 IN MX 10 mail.example.com.",
    "_sip._tcp 300 IN SRV 0 5 5060 sip.example.com.",
    '@ IN CAA 0 issue "letsencrypt.org"',
    '@ 300 IN TXT "v=spf1 include:_spf.lua.example -all"',
    "@ IN SOA ns1 hostmaster ( 1 2\n 3 4 5 ) ; LUA im Kommentar",
]


@pytest.mark.parametrize("line", GATE_MISSES)
def test_lua_gate_lines_no_false_positive_on_common_lines(line):
    assert zid.lua_gate_lines(HEADER + line + "\n") == []


def test_lua_gate_lines_covers_panel_scanner_and_real_zone():
    assert zid.lua_gate_lines(ZONE_FILE) == [6, 7, 8, 9]
    # Panel-Scanner (verschachtelte Klammern ueber Zeilen) bleibt Teil der Pruefung
    assert zid.lua_gate_lines('www 300 (\n IN LUA A "x" )\n') == [1, 2]
    assert zid.lua_gate_lines("") == [] and zid.lua_gate_lines(None) == []


def test_count_passthrough_skips_any_number_of_ttl_and_class_tokens():
    assert zid.count_passthrough_records('www 300 IN IN LUA A "x"\n')["LUA"] == 1
    assert zid.count_passthrough_records('www IN 300 IN LUA A "x"\n')["LUA"] == 1
    assert zid.build_import_diff("example.com", HEADER + 'www 300 IN IN LUA A "x"\n', None)["lua_count"] == 1


def test_lua_gate_lines_linear_time():
    """Laufzeit waechst linear mit der Eingabe (Schutz gegen ReDoS/quadratische Pfade).

    Gemessen wird das Verhaeltnis zweier Eingaben (Faktor 4), nicht die absolute Zeit: Unter Coverage
    (CI) laeuft reiner Python-Code rund fuenfmal langsamer, und die Runner sind unterschiedlich schnell.
    Linear ergibt ein Verhaeltnis um 4; ein quadratischer Pfad (z. B. Backtracking auf den langen
    Sonderzeilen) liegt bei 16 und mehr.
    """
    import time

    def build(n: int) -> str:
        text = HEADER + ("www 300 IN TXT " + '"' + "a" * 200 + '" ' + "( " * 20 + "\n") * n
        k = 5 * n
        text += ("x" + " " * k + "IN\n") + ('"' * k) + "\n" + ("\\" * k) + "\n"
        return text

    def measure(n: int) -> float:
        text = build(n)
        t = time.perf_counter()
        zid.lua_gate_lines(text)
        return time.perf_counter() - t

    measure(500)  # Aufwaermen (Imports, Caches)
    small = measure(2_000)
    large = measure(8_000)
    # Faktor 8 statt 4 wegen Messrauschen; Untergrenze 0,5 s, damit Timer-Jitter bei winzigen Zeiten nicht zaehlt
    assert large < max(8 * small, 0.5), (small, large)
    assert large < 60.0, large


def test_generic_type65402_in_preview_counts_as_lua():
    data = LUA_A.encode()
    text = HEADER + f"g IN TYPE65402 \\# {len(data)} {data.hex()}\n"
    res = zid.build_import_diff("example.com", text, None)
    assert res["parse_error"] is None and res["lua_count"] == 1
    assert {"name": "g.example.com.", "type": "LUA", "content": LUA_A} in res["would_add"]


def test_normalize_passthrough_content():
    assert zid.normalize_passthrough_content("lua", ' a  "x()" ') == normalize_lua_content('A "x()"')
    assert zid.normalize_passthrough_content("ALIAS", " Target.Example.COM ") == "target.example.com."
    assert zid.normalize_passthrough_content("TXT", ' "x" ') == '"x"'


# --------------------------------------------------------------------------- 9: Routen
@pytest.fixture
def pdns(monkeypatch):
    fake = FakePowerDNSClient("ns1")
    calls: list[str] = []
    real_get_client = pdns_manager.get_client

    def spy_get_client(name):
        calls.append(name)
        return real_get_client(name)

    monkeypatch.setattr(pdns_manager, "clients", {"ns1": fake})
    monkeypatch.setattr(pdns_manager, "unloaded", {})
    monkeypatch.setattr(pdns_manager, "get_client", spy_get_client)
    audits: list[dict] = []

    async def detached(action, resource_type, resource_name=None, **kw):
        audits.append({"action": action, "resource_name": resource_name, **kw})
        return None

    async def enqueue(*a, **k):
        return 0

    monkeypatch.setattr(audit_service, "write_audit_detached", detached)
    from app.services import webhook_outbox

    monkeypatch.setattr(webhook_outbox, "enqueue_event", enqueue)
    fake.get_client_calls = calls
    fake.audits = audits
    return fake


def _admin_client(policy: str | None) -> TestClient:
    admin = make_user()
    extra = {ServerConfig: [("ns1",)]}  # ns1 ist aktiv und schreibbar
    if policy:
        extra[SystemSetting] = [("lua_records_policy", policy)]
    c = TestClient(build_app(FakeSession(user_row=admin, extra=extra), zones), raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {create_access_token(data={'sub': str(admin.id)}, user=admin)}"
    return c


IMPORT_BODY = {"name": "imp.example.", "nameservers": ["ns1.example."],
               "content": '@ 60 IN SOA ns1 hostmaster 1 2 3 4 5\n@ 60 IN NS ns1\nwww 60 IN LUA A "f()"\n'}


def test_import_blocked_when_policy_disabled(pdns):
    r = _admin_client("disabled").post("/api/v1/zones/import", json=IMPORT_BODY)
    assert r.status_code == 403
    assert r.json()["detail"] == ("Die Zonendatei enthält LUA-Records, LUA-Records sind in diesem Panel deaktiviert. "
                                  "Betroffene Zeilen: 3.")
    assert pdns.get_client_calls == [] and pdns.calls == []
    assert pdns.audits and pdns.audits[-1]["action"] == "IMPORT" and pdns.audits[-1]["status"] == "error"
    assert pdns.audits[-1]["details"] == {"lua_count": 1, "lines": [3]}
    assert pdns.audits[-1]["zone_name"] == "imp.example."
    # ohne LUA in der Datei greift das Gate nicht
    body = dict(IMPORT_BODY, content=IMPORT_BODY["content"].replace('LUA A "f()"', "A 192.0.2.1"))
    assert _admin_client("disabled").post("/api/v1/zones/import", json=body).status_code == 200
    assert pdns.get_client_calls == ["ns1"]


@pytest.mark.parametrize("variant", ['www 60 IN IN LUA A "f()"', 'www IN 60 IN LUA A "f()"',
                                     'b 60 IN TXT ( "y" ( "z" )\nwww 60 IN LUA A "f()"'])
def test_import_gate_blocks_pdns_only_lua_variants(pdns, variant):
    """Review Welle 3: Varianten, die PowerDNS als LUA anlegt, der fruehere Panel-Zaehler aber nicht sah."""
    body = dict(IMPORT_BODY, content=IMPORT_BODY["content"].replace('www 60 IN LUA A "f()"', variant))
    line = 3 + variant.count("\n")
    client = _admin_client("disabled")
    p = client.post("/api/v1/zones/import/preview", json=body)
    assert p.status_code == 200, p.text
    assert p.json()["lua_blocked"] is True and line in p.json()["lua_blocked_lines"]
    calls_after_preview = list(pdns.calls)
    r = client.post("/api/v1/zones/import", json=body)
    assert r.status_code == 403 and f"Zeilen: {', '.join(map(str, p.json()['lua_blocked_lines']))}." in r.json()["detail"]
    assert pdns.calls == calls_after_preview  # Import hat PowerDNS nicht erreicht


def test_import_allowed_with_policy_admin(pdns):
    r = _admin_client("admin").post("/api/v1/zones/import", json=IMPORT_BODY)
    assert r.status_code == 200, r.text
    assert pdns.get_client_calls == ["ns1"]
    assert r.json()["details"] == {"ns1": "imported"}


@pytest.mark.parametrize("policy,blocked", [("disabled", True), ("admin", False), (None, False)])
def test_import_preview_reports_lua_fields(pdns, policy, blocked):
    r = _admin_client(policy).post("/api/v1/zones/import/preview", json=IMPORT_BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parse_error"] is None
    assert body["lua_count"] == 1 and body["lua_issues"] == []
    assert body["lua_policy"] == (policy or "admin")
    assert body["lua_blocked"] is blocked
    assert body["lua_blocked_lines"] == ([3] if blocked else [])


# --------------------------------------------------------------------------- WS-W3-NACHARBEIT: W3-L4, W3-L5
def test_lone_surrogate_line_and_rewrite_never_raises():
    """W3-L4: einzelne UTF-16-Surrogate (JSON-Escape) -> Zeile bekannt; der Scanner wirft nicht mehr."""
    text = HEADER + 'www 60 IN LUA A "f(\ud800)"\n'
    assert zid.lone_surrogate_line(text) == 5
    assert zid.lone_surrogate_line(HEADER) is None and zid.lone_surrogate_line(None) is None
    assert zid.lone_surrogate_line("a\nb\n\udfff") == 3
    out, found = zid.rewrite_passthrough_records(text, "example.com")   # vorher UnicodeEncodeError
    assert found and found[0].type == "LUA" and "TYPE65402 \\# " in out
    res = zid.build_import_diff("example.com", text, None)
    assert res["lua_count"] == 1


@pytest.mark.parametrize("path", ["/api/v1/zones/import/preview", "/api/v1/zones/import"])
def test_import_routes_reject_lone_surrogate_with_400(pdns, path):
    import json

    body = dict(IMPORT_BODY, content=IMPORT_BODY["content"] + "txt 60 IN TXT \"\udc80\"\n")
    # wie ein Browser/Skript: JSON-Escape "\\udc80" (json.dumps mit ensure_ascii)
    r = _admin_client("admin").post(path, content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Die Zonendatei enthält ein ungültiges Zeichen (einzelnes UTF-16-Surrogat) in Zeile 4."
    assert pdns.get_client_calls == [] and pdns.calls == []


def _lua_wire(rtype_code: int, code: str) -> bytes:
    raw = code.encode()
    out = rtype_code.to_bytes(2, "big")
    for i in range(0, len(raw), 255):
        chunk = raw[i:i + 255]
        out += bytes([len(chunk)]) + chunk
    return out


def test_generic_wire_format_read_like_powerdns():
    """W3-L5: TYPE65402/TYPE65401 im RFC-3597-Wire-Format (z. B. PowerDNS-Export) -> gleicher Text wie PowerDNS."""
    code = "ifportup(443, {'192.0.2.1', '192.0.2.2'})"
    lua = _lua_wire(1, code)                                   # Ziel-Typ A
    long_code = "x" * 300                                      # zwei Zeichenketten (255 + 45)
    lua_long = _lua_wire(16, long_code)                        # TXT
    alias = b"\x03www\x07example\x03net\x00"
    text = (HEADER
            + f"g IN TYPE65402 \\# {len(lua)} {lua.hex()}\n"
            + f"h IN TYPE65402 \\# {len(lua_long)} {lua_long.hex()}\n"
            + f"@ IN TYPE65401 \\# {len(alias)} {alias.hex()}\n")
    res = zid.build_import_diff("example.com", text, None)
    assert res["parse_error"] is None and res["lua_count"] == 2
    adds = {(a["name"], a["type"]): a["content"] for a in res["would_add"]}
    assert adds[("g.example.com.", "LUA")] == normalize_lua_content(f'A "{code}"')
    assert adds[("h.example.com.", "LUA")] == normalize_lua_content(f'TXT "{long_code}"')
    assert adds[("example.com.", "ALIAS")] == "www.example.net."
    # Panel-Schreibweise (Praesentationstext als Hex) bleibt wie bisher
    data = LUA_A.encode()
    res = zid.build_import_diff("example.com", HEADER + f"g IN TYPE65402 \\# {len(data)} {data.hex()}\n", None)
    assert {"name": "g.example.com.", "type": "LUA", "content": LUA_A} in res["would_add"]


@pytest.mark.parametrize("data", [
    b"\x00\x01\x05ab",           # Laengenbyte zu gross
    b"\xff\xfe\x01a",            # unbekannter Typ
    b"\x00\x01",                 # kein Code
    b"\x00\x01\x02\xff\xfe",     # kein UTF-8
])
def test_generic_wire_detection_falls_back(data):
    assert zid._wire_text("LUA", data) is None
    assert zid._wire_text("ALIAS", b"\x03www\x00extra") is None
    assert zid._wire_text("ALIAS", b"\x03www\x00") == "www."
