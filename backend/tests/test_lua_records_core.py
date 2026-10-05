"""Tests fuer services/lua_records.py (F15 9.1 Kernfunktionen + Policy) und
zone_import_diff.split_logical_lines (F15 9.2-1)."""
import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import lua_records as lr

VALID = [
    "A \"ifportup(443, {'192.0.2.1', '192.0.2.2'})\"",
    "AAAA \"ifportup(443, {'2001:db8::1', '2001:db8::2'})\"",
    "A \"pickrandom({'192.0.2.1', '192.0.2.2'})\"",
    "A \"pickwrandom({{100, '192.0.2.1'}, {50, '192.0.2.2'}})\"",
    "A \"pickclosest({'192.0.2.1', '198.51.100.1'})\"",
    "A \"country('DE') and '192.0.2.1' or '198.51.100.1'\"",
    "CNAME \"continent('EU') and 'eu.example.com.' or 'us.example.com.'\"",
    "TXT \"'Hallo ' .. os.date('%Y')\"",
    "A \"ifurlup('https://example.com/health', {{'192.0.2.1'}, {'192.0.2.2'}})\"",
]


@pytest.mark.parametrize("content", VALID)
def test_valid_templates_unchanged(content):
    assert lr.validate_lua_content(content) == content


def test_normalization_and_multi_chunk():
    assert lr.validate_lua_content("a   \"pickrandom({'192.0.2.1'})\"") == "A \"pickrandom({'192.0.2.1'})\""
    assert lr.validate_lua_content("  A \"pick\" \"random({'1.2.3.4'})\"  ") == "A \"pick\" \"random({'1.2.3.4'})\""


def test_long_strings_and_comments():
    assert lr.validate_lua_content("A \"[[ ( ]] .. pickrandom({'1.2.3.4'}) -- ( kommentar\"")
    assert lr.validate_lua_content("A \"--[==[ ( ]==] pickrandom({'1.2.3.4'})\"")
    assert lr.validate_lua_content("A \"[=[ ]] ( ]=] .. 'x'\"")


@pytest.mark.parametrize("content,message", [
    ("", lr.MSG_EMPTY),
    ("   ", lr.MSG_EMPTY),
    ("A", lr.MSG_NO_TYPE),
    ('NS "x()"', "Ziel-Typ NS ist für LUA nicht erlaubt. Erlaubt: A, AAAA, CNAME, TXT, MX, SRV, PTR, CAA, NAPTR, LOC, SPF, HTTPS, SVCB, SSHFP, TLSA"),
    ('LUA "x()"', "Ziel-Typ LUA ist für LUA nicht erlaubt."),
    ("A x()", lr.MSG_NOT_QUOTED),
    ('A "a" b', lr.MSG_QUOTES_UNBALANCED),
    ('A "x(\\"y\\")"', lr.MSG_ESCAPED_QUOTE),
    ('A ""', lr.MSG_CODE_EMPTY),
    ('A "  "', lr.MSG_CODE_EMPTY),
    ('A "f({1)"', lr.MSG_UNBALANCED),
    ("A \"f('x)\"", lr.MSG_UNTERMINATED_STRING),
    ('A "--[[ offen"', lr.MSG_UNTERMINATED_COMMENT),
    ('A "[[ offen"', lr.MSG_UNTERMINATED_STRING),
    ('A "a\nb"', lr.MSG_CONTROL),
    ('A "a\tb"', lr.MSG_CONTROL),
    ('A "' + "x" * 4000 + '"', lr.MSG_TOO_LONG),
    ('A "f())"', lr.MSG_UNBALANCED),
    ('A "f( -- )"', lr.MSG_UNBALANCED),
])
def test_invalid_contents_exact_messages(content, message):
    with pytest.raises(ValueError) as ei:
        lr.validate_lua_content(content)
    assert str(ei.value).startswith(message)


def test_messages_are_german_and_exact():
    assert lr.MSG_TOO_LONG == "LUA-Inhalt ist zu lang (max. 4000 Zeichen)."
    assert lr.MSG_NO_TYPE == "LUA-Inhalt muss mit dem Ziel-Typ beginnen, z. B. A \"ifportup(443, {'192.0.2.1'})\"."


def test_check_lua_brackets_direct():
    assert lr.check_lua_brackets("f({1, 2}, [3])") is None
    assert lr.check_lua_brackets("f('a\\'b')") is None
    assert lr.check_lua_brackets("f(]") == "unbalanced"
    assert lr.check_lua_brackets("f('x") == "unterminated_string"
    assert lr.check_lua_brackets("--[[ x") == "unterminated_comment"
    assert lr.check_lua_brackets("x -- (") is None


def test_split_and_chunks():
    assert lr.split_lua_content(' a  "x()"') == ("A", '"x()"')
    with pytest.raises(ValueError):
        lr.split_lua_content("A")
    assert lr.lua_code_chunks('"a" "b"') == ["a", "b"]
    with pytest.raises(ValueError):
        lr.lua_code_chunks('"a" b')


def test_normalize_lua_content_never_raises():
    assert lr.normalize_lua_content('a   "x()"  ') == 'A "x()"'
    assert lr.normalize_lua_content("   kaputt") == "kaputt"
    assert lr.normalize_lua_content(None) == ""
    assert lr.normalize_lua_content("") == ""


def test_uses_geo_functions():
    assert lr.uses_geo_functions("A \"pickclosest({'1.2.3.4'})\"")
    assert lr.uses_geo_functions("A \"country ('DE')\"")
    assert not lr.uses_geo_functions("A \"ifportup(443, {'1.2.3.4'})\"")
    assert not lr.uses_geo_functions("A \"mycountry('DE')\"")


def test_validate_lua_items_objects_and_dicts():
    items = [SimpleNamespace(content='a  "x()"'), {"content": 'aaaa "y()"'}]
    out = lr.validate_lua_items("lua", items)
    assert out[0].content == 'A "x()"' and out[1]["content"] == 'AAAA "y()"'
    untouched = [SimpleNamespace(content="kein lua")]
    assert lr.validate_lua_items("A", untouched)[0].content == "kein lua"
    with pytest.raises(ValueError):
        lr.validate_lua_items("LUA", [{"content": "A x"}])


# ------------------------------------------------------------------ Policy
@pytest.mark.parametrize("policy,expected", [("disabled", False), ("manage", True)])
def test_policy_without_admin_check(policy, expected):
    user = SimpleNamespace(role="admin")
    assert lr.lua_policy_allows(policy, user) is expected
    assert lr.lua_denied_message("disabled") == lr.MSG_DENIED_DISABLED
    assert lr.lua_denied_message("admin") == lr.MSG_DENIED_ADMIN


def test_policy_admin_delegates_to_is_effective_admin(monkeypatch):
    """Policy "admin" fragt core.auth.is_effective_admin (kommt mit W0-INT-BE2a; hier gepatcht,
    raising=False, damit der Test auch vor Welle 0b laeuft)."""
    import app.core.auth as core_auth

    seen = []

    def fake_is_effective_admin(user):
        seen.append(user)
        return getattr(user, "role", None) == "admin" and not getattr(user, "token_without_admin", False)

    monkeypatch.setattr(core_auth, "is_effective_admin", fake_is_effective_admin, raising=False)
    admin = SimpleNamespace(role="admin")
    assert lr.lua_policy_allows("admin", admin) is True
    assert lr.lua_policy_allows("admin", SimpleNamespace(role="user")) is False
    assert lr.lua_policy_allows("admin", SimpleNamespace(role="admin", token_without_admin=True)) is False
    assert lr.lua_policy_allows("manage", SimpleNamespace(role="user")) is True
    assert len(seen) == 3  # manage/disabled fragen nicht


@pytest.mark.wave_integration
@pytest.mark.parametrize("raw,expected", [(None, "admin"), (" Manage ", "manage"), ("bogus", "admin"), ("DISABLED", "disabled")])
async def test_get_lua_policy(monkeypatch, raw, expected):
    """Braucht services.system_settings (W0-SECRETS, Welle 0a parallel)."""
    from app.services import system_settings

    async def fake_get_setting(db, key, default=None):
        assert key == lr.LUA_POLICY_KEY
        return raw

    monkeypatch.setattr(system_settings, "get_setting", fake_get_setting)
    assert await lr.get_lua_policy(object()) == expected


@pytest.mark.wave_integration
async def test_assert_lua_write_allowed_messages(monkeypatch):
    from app.services import system_settings

    state = {"v": "disabled"}

    async def fake_get_setting(db, key, default=None):
        return state["v"]

    monkeypatch.setattr(system_settings, "get_setting", fake_get_setting)
    with pytest.raises(HTTPException) as ei:
        await lr.assert_lua_write_allowed(object(), SimpleNamespace(role="admin"))
    assert ei.value.status_code == 403 and ei.value.detail == lr.MSG_DENIED_DISABLED
    state["v"] = "manage"
    await lr.assert_lua_write_allowed(object(), SimpleNamespace(role="user"))
    assert await lr.lua_allowed_for(object(), SimpleNamespace(role="user")) is True


async def test_policy_functions_with_patched_policy(monkeypatch):
    """Policy-Pfad ohne parallele Module: get_lua_policy gepatcht."""
    async def pol(db):
        return "disabled"

    monkeypatch.setattr(lr, "get_lua_policy", pol)
    assert await lr.lua_allowed_for(None, SimpleNamespace(role="admin")) is False
    with pytest.raises(HTTPException) as ei:
        await lr.assert_lua_write_allowed(None, SimpleNamespace(role="admin"))
    assert ei.value.detail == lr.MSG_DENIED_DISABLED


# ------------------------------------------------------------------ split_logical_lines (F15 9.2-1)
from app.services.zone_import_diff import LogicalLine, split_logical_lines  # noqa: E402


def test_split_logical_lines_parentheses_comments_quotes():
    content = (
        "$ORIGIN example.com.\n"
        "; nur Kommentar\n"
        "\n"
        "www 60 IN TXT ( \"a;b\" ; Kommentar\n"
        "   \"c\"\n"
        "   ) ; Ende\n"
        "  IN A 192.0.2.1\n"
    )
    lines = split_logical_lines(content)
    assert [(ll.start, ll.end) for ll in lines] == [(1, 1), (4, 6), (7, 7)]
    assert lines[0].text == "$ORIGIN example.com."
    assert '"a;b"' in lines[1].text and '"c"' in lines[1].text
    assert "Kommentar" not in lines[1].text and "(" not in lines[1].text and ")" not in lines[1].text
    assert lines[1].leading_ws is False and lines[2].leading_ws is True
    assert lines[2].text == "IN A 192.0.2.1"
    assert all(ll.error is None for ll in lines)
    assert all(isinstance(ll, LogicalLine) for ll in lines)


def test_split_logical_lines_parens_inside_quotes_and_escapes():
    lines = split_logical_lines('a TXT "(x" "y\\"(" \\; b\n')
    assert len(lines) == 1
    assert lines[0].text == 'a TXT "(x" "y\\"(" \\; b'


def test_split_logical_lines_unclosed_parenthesis():
    lines = split_logical_lines("a 60 IN TXT ( \"x\"\n \"y\"\n")
    assert len(lines) == 1
    ll = lines[0]
    assert (ll.start, ll.end) == (1, 2)
    assert ll.error == "Klammer nicht geschlossen"
    assert '"x"' in ll.text and '"y"' in ll.text


def test_split_logical_lines_lua_multiline():
    content = 'multi IN LUA ( A ";if x" ) ; Kommentar\ngeo IN LUA A "pickclosest({\'1.2.3.4\'})"\n'
    lines = split_logical_lines(content)
    assert [ll.text for ll in lines] == ['multi IN LUA   A ";if x"', 'geo IN LUA A "pickclosest({\'1.2.3.4\'})"']


def test_split_logical_lines_disabled_marker_option():
    content = ";@disabled old 60 IN A 192.0.2.9\n; normaler Kommentar\nnew 60 IN A 192.0.2.10\n"
    plain = split_logical_lines(content)
    assert [ll.text for ll in plain] == ["new 60 IN A 192.0.2.10"]
    marked = split_logical_lines(content, disabled_marker=True)
    assert [(ll.text, ll.disabled, ll.leading_ws, ll.start) for ll in marked] == [
        ("old 60 IN A 192.0.2.9", True, False, 1),
        ("new 60 IN A 192.0.2.10", False, False, 3),
    ]


def test_split_logical_lines_crlf_and_empty():
    assert split_logical_lines("") == []
    lines = split_logical_lines("a A 192.0.2.1\r\nb A 192.0.2.2\r\n")
    assert [ll.text for ll in lines] == ["a A 192.0.2.1", "b A 192.0.2.2"]
