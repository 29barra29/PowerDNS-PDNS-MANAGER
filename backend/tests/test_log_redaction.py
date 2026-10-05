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
