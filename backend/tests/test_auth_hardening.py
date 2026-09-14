"""Tests fuer Session-Bindung, Einmal-Reset-Links und Replay-Schutz."""
import time
from types import SimpleNamespace

import pyotp

from app.core import auth as a


def _user(pw_hash="hash-A"):
    return SimpleNamespace(id=7, hashed_password=pw_hash)


def test_access_token_carries_password_version():
    tok = a.create_access_token({"sub": "7", "role": "user"}, user=_user())
    payload = a.decode_token(tok)
    assert payload["pwv"] == a.password_version("hash-A")
    assert payload["pwv"] != a.password_version("hash-B")


def test_reset_token_bound_to_current_hash():
    tok = a.create_password_reset_token(7, "hash-A")
    payload = a.decode_password_reset_payload(tok)
    assert payload["sub"] == "7"
    assert payload["pwv"] == a.password_version("hash-A")
    # nach Passwortaenderung passt der Link nicht mehr
    assert payload["pwv"] != a.password_version("hash-NEU")
    assert a.decode_password_reset_token(tok) == 7


def test_totp_code_accepted_only_once(monkeypatch):
    a._TOTP_USED.clear()
    secret = pyotp.random_base32()
    code = pyotp.TOTP(secret).now()
    assert a.totp_verify_once(7, secret, code) is True
    assert a.totp_verify_once(7, secret, code) is False   # Replay
    assert a.totp_verify_once(8, secret, code) is True    # anderer Nutzer, eigener Zaehler
    assert a.totp_verify_once(7, secret, "000000") in (False,)


def test_webauthn_challenge_single_use():
    a._WEBAUTHN_USED.clear()
    tok = a.create_webauthn_challenge_token("Y2hhbGxlbmdl", purpose=a.TOKEN_TYPE_WEBAUTHN_AUTH)
    assert a.decode_webauthn_challenge_token(tok, purpose=a.TOKEN_TYPE_WEBAUTHN_AUTH) is not None
    assert a.decode_webauthn_challenge_token(tok, purpose=a.TOKEN_TYPE_WEBAUTHN_AUTH) is None
