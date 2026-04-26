import pytest

from app.services.dnssec_parse import parse_ds_line, parse_dnskey_rdata


def test_parse_ds_line_valid_recommended():
    s = "2371 13 2 6E19790D5392D455B23D0619C3B08190A9BF73EBF81DD61EAF317A3B619400D5"
    d = parse_ds_line(s)
    assert "error" not in d
    assert d["key_tag"] == 2371
    assert d["digest_type"] == 2
    assert d["recommended"] is True
    assert d["algorithm"] == 13


def test_parse_ds_line_empty():
    d = parse_ds_line("   ")
    assert d.get("error") == "empty"


def test_parse_ds_line_invalid():
    d = parse_ds_line("1 2")
    assert d.get("error") == "format"


def test_parse_dnskey_rfc():
    s = "256 3 13 oJMRESz5E4gYzS/q6XDrvU1qMPYIjCWzJaOau8XNEZeqCYKD5ar0IRd8KqXXFJkqmVfRvMGPmM1x8fGAa2XhSA=="
    p = parse_dnskey_rdata(s)
    assert p and not p.get("error")
    assert p.get("flags") == 256
