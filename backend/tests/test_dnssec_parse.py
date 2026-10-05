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


# --- F4 5.2: Key-Tag und DNSKEY-Normalisierung ----------------------------------------------------
from app.services.dnssec_parse import compute_key_tag, normalize_dnskey  # noqa: E402

# RFC 6605 Abschnitt 6.1 (ECDSA P-256): DS "55648 13 2 b4c8c1fe…" -> Key-Tag 55648 (mit dnspython gegen den
# DS im RFC verifiziert: make_ds(example.net., key, SHA256) ergibt genau diesen Digest).
RFC6605_KSK = ("257 3 13 GojIhhXUN/u4v54ZQqGSnyhWJwaubCvTmeexv7bR6edbkrSqQpF64cYbcB7wNcP+e+MAnLr+Wi9xMWyQLc8NAA==")


def test_compute_key_tag_rfc6605_vector():
    assert compute_key_tag(RFC6605_KSK) == 55648
    # Whitespace/Zeilenumbrueche im Base64-Teil stoeren nicht
    assert compute_key_tag("  257  3 13 GojIhhXUN/u4v54ZQqGSnyhWJwaubCvTmeexv7bR6edbkrSqQpF64cYbcB7w\n"
                           "NcP+e+MAnLr+Wi9xMWyQLc8NAA== ") == 55648


@pytest.mark.parametrize("value", [None, "", "   ", "kaputt", "257 3 13 !!!kein-base64!!!", "x y z w"])
def test_compute_key_tag_garbage_is_none(value):
    assert compute_key_tag(value) is None


def test_normalize_dnskey():
    assert normalize_dnskey("  257   3 13\n  AbC= ") == "257 3 13 AbC="
    assert normalize_dnskey(None) == "" and normalize_dnskey("") == ""
    assert normalize_dnskey(RFC6605_KSK) == RFC6605_KSK
