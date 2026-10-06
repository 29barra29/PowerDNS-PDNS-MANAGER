"""Tests fuer core/log_redaction.py (F9 9)."""
import logging

import pytest

from app.core import log_redaction as lr


def _record(args):
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                             '%s - "%s %s HTTP/%s" %d', args, None)


def test_password_is_masked():
    rec = _record(("1.2.3.4:5", "GET", "/nic/update?hostname=a&password=geheim", "1.1", 401))
    assert lr.RedactQueryFilter().filter(rec) is True
    msg = rec.getMessage()
    assert "password=***" in msg
    assert "geheim" not in msg
    assert "hostname=a" in msg


@pytest.mark.parametrize("param", ["token", "key", "secret", "apikey", "api_key", "pass", "pwd", "pw", "auth", "PASSWORD"])
def test_other_params_masked(param):
    rec = _record(("1.2.3.4:5", "GET", f"/x?a=1&{param}=s3cr3t&b=2", "1.1", 200))
    lr.RedactQueryFilter().filter(rec)
    msg = rec.getMessage()
    assert "s3cr3t" not in msg
    assert f"{param}=***" in msg
    assert "b=2" in msg


def test_first_param_masked():
    rec = _record(("1.2.3.4:5", "GET", "/x?token=abc#frag", "1.1", 200))
    lr.RedactQueryFilter().filter(rec)
    assert "abc" not in rec.getMessage()


def test_path_without_query_unchanged():
    args = ("1.2.3.4:5", "GET", "/api/v1/zones", "1.1", 200)
    rec = _record(args)
    lr.RedactQueryFilter().filter(rec)
    assert rec.args == args


def test_non_sensitive_params_unchanged():
    rec = _record(("1.2.3.4:5", "GET", "/nic/update?hostname=h.example.&myip=192.0.2.1", "1.1", 200))
    lr.RedactQueryFilter().filter(rec)
    assert "myip=192.0.2.1" in rec.getMessage()


@pytest.mark.parametrize("args", [None, (), ("a",), ("a", "b", 3), {"x": 1}])
def test_unexpected_args_do_not_raise(args):
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "msg", None, None)
    rec.args = args
    assert lr.RedactQueryFilter().filter(rec) is True


def test_install_is_idempotent():
    lg = logging.getLogger("uvicorn.access")
    before = [f for f in lg.filters if isinstance(f, lr.RedactQueryFilter)]
    for f in before:
        lg.removeFilter(f)
    try:
        lr.install_access_log_redaction()
        lr.install_access_log_redaction()
        assert len([f for f in lg.filters if isinstance(f, lr.RedactQueryFilter)]) == 1
    finally:
        for f in [f for f in lg.filters if isinstance(f, lr.RedactQueryFilter)]:
            lg.removeFilter(f)


def test_redact_query_helper():
    assert lr.redact_query("/a?key=1&x=2") == "/a?key=***&x=2"
    assert lr.redact_query("/a") == "/a"


# ---------------------------------------------------------------------------------------------
# A3 (WS-W3-NACHARBEIT): URL-kodierte Namen, dnsmgr_-Werte unter beliebigen Namen
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "%70assword",          # p kodiert
    "PASS%57ORD",          # W kodiert, Grossschreibung
    "%2570assword",        # doppelt kodiert
    "pass%77ord",
    "%74oken",
    "api%5Fkey",           # _ kodiert
    "api-key",
    "x-api-key",
    "new_password",
    "access_token",
    "client_secret",
    "+password",           # + = Leerzeichen
    "%20token%20",
])
def test_encoded_and_suffixed_names_are_masked(name):
    out = lr.redact_query(f"/nic/update?hostname=h.example.&{name}=s3cr3t-Wert&myip=192.0.2.1")
    assert "s3cr3t" not in out
    assert f"{name}=***" in out
    assert "hostname=h.example." in out and "myip=192.0.2.1" in out


@pytest.mark.parametrize("value", [
    "dnsmgr_usr_abcDEF123456",
    "DNSMGR_dyn_xyz",
    "%64nsmgr_usr_abc",           # d kodiert
    "user%3Adnsmgr_dyn_abc",      # user:dnsmgr_... (Basic-Schreibweise im Query)
    "Bearer+dnsmgr_usr_abc",
])
def test_dnsmgr_values_masked_under_any_name(value):
    out = lr.redact_query(f"/x?hostname=a&irgendwas={value}&b=2")
    assert out == "/x?hostname=a&irgendwas=***&b=2"


def test_dnsmgr_without_equals_and_in_path_masked():
    assert lr.redact_query("/x?dnsmgr_usr_abc&b=2") == "/x?***&b=2"
    assert lr.redact_query("/nic/update/dnsmgr_dyn_abc123?x=1") == "/nic/update/dnsmgr_***?x=1"
    assert lr.redact_query("/a/DNSMGR_usr_q") == "/a/DNSMGR_***"


def test_filter_masks_token_in_path_without_query():
    rec = _record(("1.2.3.4:5", "GET", "/api/v1/dnsmgr_usr_geheim", "1.1", 404))
    lr.RedactQueryFilter().filter(rec)
    assert "geheim" not in rec.getMessage()


def test_harmless_names_and_values_unchanged():
    path = "/api/v1/dnssec/ns1/example.com./dnskey-check?key_tag=12345&keyword=x&monkey=1&hostname=pass.example."
    assert lr.redact_query(path) == path
    assert lr.redact_query("/x?a=1&&b=&=c#frag") == "/x?a=1&&b=&=c#frag"


def test_fragment_kept_and_query_only_redacted():
    assert lr.redact_query("/x?token=abc#frag") == "/x?token=***#frag"


def test_broken_encoding_does_not_raise():
    out = lr.redact_query("/x?%E0%A4%A=1&pass%ZZword=2&password=%FF%FE")
    assert "password=***" in out
