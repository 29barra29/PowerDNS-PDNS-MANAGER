"""Tests fuer services/sso_oidc.py (F10 9.1 Nr. 8-14, Nr. 32; Konfigurationstest 3.3.3).

Der IdP ist ein ``httpx.MockTransport`` (Discovery, JWKS, Token, UserInfo); Schluessel und ID-Tokens erzeugt joserfc.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import httpx  # noqa: E402
import pytest  # noqa: E402
from joserfc import jwt  # noqa: E402
from joserfc.jwk import ECKey, OctKey, RSAKey  # noqa: E402

from app.core.auth import create_access_token, password_version  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import sso_oidc  # noqa: E402
from app.services.sso_oidc import OidcError  # noqa: E402
from app.services.sso_settings import OidcCfg, SsoConfig  # noqa: E402

ISS = "https://idp.example.com/realms/firma"
BASE_URL = "https://dns.example.com"
REDIRECT = BASE_URL + "/api/v1/auth/oidc/callback"
RSA = RSAKey.generate_key(2048, parameters={"kid": "rsa-1"})
EC = ECKey.generate_key("P-256", parameters={"kid": "ec-1"})
RSA2 = RSAKey.generate_key(2048, parameters={"kid": "rsa-2"})


def cfg(**oidc) -> SsoConfig:
    base = dict(enabled=True, issuer=ISS, client_id="pdns", client_secret="client-secret",
                token_auth_method="client_secret_basic")
    base.update(oidc)
    return SsoConfig(oidc=OidcCfg(**base), base_url=BASE_URL)


def metadata(**extra) -> dict:
    doc = {
        "issuer": ISS,
        "authorization_endpoint": ISS + "/protocol/openid-connect/auth",
        "token_endpoint": ISS + "/protocol/openid-connect/token",
        "userinfo_endpoint": ISS + "/protocol/openid-connect/userinfo",
        "jwks_uri": ISS + "/protocol/openid-connect/certs",
        "id_token_signing_alg_values_supported": ["RS256", "ES256", "HS256", "none"],
        "code_challenge_methods_supported": ["plain", "S256"],
    }
    doc.update(extra)
    return {k: v for k, v in doc.items() if v is not None}


class FakeIdP:
    """Kleiner IdP fuer MockTransport; zaehlt Aufrufe je Pfad."""

    def __init__(self):
        self.meta = metadata()
        self.keys = [RSA, EC]
        self.calls: dict[str, int] = {}
        self.token_response: tuple[int, dict] | None = None
        self.userinfo: dict | None = {"sub": "user-1", "groups": ["pdns-admins"], "email": "ui@example.com"}
        self.last_token_request: httpx.Request | None = None
        self.fail: dict[str, Exception] = {}

    def jwks(self) -> dict:
        return {"keys": [k.as_dict(private=False) for k in self.keys]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls[path] = self.calls.get(path, 0) + 1
        if path in self.fail:
            raise self.fail[path]
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=self.meta)
        if path.endswith("/certs"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/token"):
            self.last_token_request = request
            status, body = self.token_response or (200, {"access_token": "at-1", "token_type": "Bearer",
                                                           "id_token": make_token()})
            return httpx.Response(status, json=body)
        if path.endswith("/userinfo"):
            assert request.headers["authorization"] == "Bearer at-1"
            return httpx.Response(200, json=self.userinfo)
        return httpx.Response(404, json={})


@pytest.fixture(autouse=True)
def _reset():
    sso_oidc.reset_for_tests()
    yield
    sso_oidc.reset_for_tests()


@pytest.fixture
def idp():
    fake = FakeIdP()
    sso_oidc.set_transport_for_tests(httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(sso_oidc, "_now", lambda: state.t)
    return state


def run(coro):
    return asyncio.run(coro)


def claims(**kw) -> dict:
    now = int(time.time())
    c = {"iss": ISS, "sub": "user-1", "aud": "pdns", "exp": now + 300, "iat": now, "nonce": "n-1",
         "preferred_username": "jdoe", "email": "jdoe@example.com", "email_verified": True, "name": "John Doe"}
    c.update(kw)
    return {k: v for k, v in c.items() if v is not None}


def make_token(key=RSA, alg="RS256", kid=None, **kw) -> str:
    header = {"alg": alg, "kid": kid or key.kid}
    return jwt.encode(header, claims(**kw), key, algorithms=[alg])


def b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


# ---------------------------------------------------------------------------------------------
# Nr. 32 Import ohne AuthlibDeprecationWarning
# ---------------------------------------------------------------------------------------------
def test_import_without_authlib_deprecation_warning():
    code = (
        "import warnings\n"
        "warnings.simplefilter('always')\n"
        "with warnings.catch_warnings(record=True) as rec:\n"
        "    warnings.simplefilter('always')\n"
        "    import app.services.sso_oidc\n"
        "print('|'.join(type(w.message).__name__ + ':' + w.category.__name__ for w in rec))\n"
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60,
                         cwd=str(Path(__file__).resolve().parents[1]))
    assert out.returncode == 0, out.stderr
    assert "AuthlibDeprecationWarning" not in out.stdout


# ---------------------------------------------------------------------------------------------
# Nr. 8 extract_profile / get_claim
# ---------------------------------------------------------------------------------------------
def test_extract_profile_paths_groups_locale():
    c = cfg(groups_claim="realm_access.roles")
    p = sso_oidc.extract_profile(c, ISS, claims(realm_access={"roles": ["a", "b", 3, True]}, locale="de-DE"))
    assert p.groups == ["a", "b", "3"] and p.locale == "de"
    assert p.username_hint == "jdoe" and p.email == "jdoe@example.com" and p.email_verified is True
    assert p.source == "oidc" and p.issuer == ISS and p.subject == "user-1" and p.display_name == "John Doe"
    p = sso_oidc.extract_profile(cfg(), ISS, claims(groups="single", locale="xx"))
    assert p.groups == ["single"] and p.locale is None
    p = sso_oidc.extract_profile(cfg(), ISS, claims())
    assert p.groups is None                                   # Claim fehlt -> unbekannt
    p = sso_oidc.extract_profile(cfg(groups_claim=""), ISS, claims(groups=["x"]))
    assert p.groups is None


def test_extract_profile_email_rules_and_namespaced_claim():
    p = sso_oidc.extract_profile(cfg(), ISS, claims(email_verified=False))
    assert p.email is None and p.email_verified is False
    p = sso_oidc.extract_profile(cfg(), ISS, claims(email_verified="true"))
    assert p.email_verified is True
    p = sso_oidc.extract_profile(cfg(), ISS, claims(email_verified=None))
    assert p.email == "jdoe@example.com" and p.email_verified is None
    p = sso_oidc.extract_profile(cfg(), ISS, claims(email="kein-mail"))
    assert p.email is None
    p = sso_oidc.extract_profile(cfg(groups_claim="https://example.com/groups"), ISS,
                                 claims(**{"https://example.com/groups": ["g1"]}))
    assert p.groups == ["g1"]
    p = sso_oidc.extract_profile(cfg(), ISS, claims(groups=[f"g{i}" for i in range(600)]))
    assert len(p.groups) == 500
    assert sso_oidc.get_claim({"a": {"b": 1}}, "a.b") == 1
    assert sso_oidc.get_claim({"a": {"b": 1}}, "a.c") is None
    assert sso_oidc.get_claim({"a": 1}, "") is None


# ---------------------------------------------------------------------------------------------
# Nr. 9 Discovery
# ---------------------------------------------------------------------------------------------
def test_discovery_success_and_cache(idp, clock):
    doc = run(sso_oidc.get_provider_metadata(ISS))
    assert doc["issuer"] == ISS
    run(sso_oidc.get_provider_metadata(ISS + "/"))
    assert idp.calls["/realms/firma/.well-known/openid-configuration"] == 1
    clock.t += 3601
    run(sso_oidc.get_provider_metadata(ISS))
    assert idp.calls["/realms/firma/.well-known/openid-configuration"] == 2


def test_discovery_issuer_mismatch(idp):
    idp.meta = metadata(issuer="https://evil.example.com/realms/firma")
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.get_provider_metadata(ISS))
    assert ei.value.code == "discovery"


def test_discovery_trailing_slash_issuer_is_exact(idp):
    idp.meta = metadata(issuer=ISS + "/")
    doc = run(sso_oidc.get_provider_metadata(ISS))
    assert doc["issuer"] == ISS + "/"         # erwarteter iss im Token ist der Discovery-Wert, exakt


def test_discovery_http_requires_insecure(idp, monkeypatch):
    insecure_iss = "http://idp.local/realms/firma"
    idp.meta = metadata(issuer=insecure_iss, authorization_endpoint="http://idp.local/auth",
                        token_endpoint="http://idp.local/token", jwks_uri="http://idp.local/certs",
                        userinfo_endpoint=None)
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.get_provider_metadata(insecure_iss))
    assert "HTTPS" in ei.value.log_detail
    monkeypatch.setattr(settings, "SSO_ALLOW_INSECURE", True)
    assert run(sso_oidc.get_provider_metadata(insecure_iss))["issuer"] == insecure_iss


def test_discovery_endpoint_without_https(idp):
    idp.meta = metadata(token_endpoint="http://idp.example.com/token")
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.get_provider_metadata(ISS))
    assert ei.value.log_detail == "Endpunkt token_endpoint nutzt kein HTTPS"


def test_discovery_too_large_and_redirect(idp):
    def big(request):
        return httpx.Response(200, content=b"{" + b" " * 1_048_600 + b"}", headers={"content-type": "application/json"})

    sso_oidc.set_transport_for_tests(httpx.MockTransport(big))
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.get_provider_metadata(ISS))
    assert "1 MB" in ei.value.log_detail

    seen = []

    def redirect(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example/.well-known/openid-configuration"})

    sso_oidc.set_transport_for_tests(httpx.MockTransport(redirect))
    with pytest.raises(OidcError):
        run(sso_oidc.get_provider_metadata(ISS))
    assert len(seen) == 1                          # Redirect wird nicht verfolgt


def test_discovery_network_error_uses_stale_cache(idp, clock):
    run(sso_oidc.get_provider_metadata(ISS))
    clock.t += 7200                                # TTL abgelaufen, < 24 h
    idp.fail["/realms/firma/.well-known/openid-configuration"] = httpx.ConnectError("down")
    assert run(sso_oidc.get_provider_metadata(ISS))["issuer"] == ISS
    clock.t += 86400                               # aelter als 24 h -> Fehler
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.get_provider_metadata(ISS))
    assert ei.value.code == "discovery"


def test_discovery_network_error_without_cache(idp):
    idp.fail["/realms/firma/.well-known/openid-configuration"] = httpx.ConnectError("down")
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.get_provider_metadata(ISS))
    assert ei.value.code == "discovery" and "ConnectError" in ei.value.log_detail


def test_allowed_algs():
    assert sso_oidc.allowed_algs({}) == ["RS256"]
    assert sso_oidc.allowed_algs({"id_token_signing_alg_values_supported": ["HS256", "ES256", "RS256"]}) == \
        ["RS256", "ES256"]
    with pytest.raises(OidcError):
        sso_oidc.allowed_algs({"id_token_signing_alg_values_supported": ["HS256", "none"]})


# ---------------------------------------------------------------------------------------------
# Nr. 10 ID-Token
# ---------------------------------------------------------------------------------------------
def _validate(token, idp_meta, *, nonce="n-1", **cfg_kw):
    return run(sso_oidc.validate_id_token(token, idp_meta, cfg(**cfg_kw), nonce=nonce))


def test_id_token_valid_rs256_and_es256(idp):
    c = _validate(make_token(), idp.meta)
    assert c["sub"] == "user-1"
    c = _validate(make_token(EC, "ES256"), idp.meta)
    assert c["sub"] == "user-1"


@pytest.mark.parametrize("kw,detail", [
    ({"aud": "other"}, "Claim"),
    ({"iss": "https://evil.example.com"}, "Claim"),
    ({"nonce": "other"}, "Claim"),
    ({"exp": int(time.time()) - 120}, "Claim"),
    ({"iat": int(time.time()) - 700, "exp": int(time.time()) + 300}, "iat zu alt"),
    ({"aud": ["pdns", "other"]}, "azp"),
    ({"aud": ["pdns", "other"], "azp": "other"}, "azp"),
    ({"azp": "other"}, "azp"),
    ({"sub": ""}, "Claim"),
    ({"sub": None}, "Claim"),
])
def test_id_token_claim_errors(idp, kw, detail):
    with pytest.raises(OidcError) as ei:
        _validate(make_token(**kw), idp.meta)
    assert ei.value.code == "id_token" and detail in ei.value.log_detail


def test_id_token_multi_aud_with_azp_ok(idp):
    assert _validate(make_token(aud=["pdns", "other"], azp="pdns"), idp.meta)["aud"] == ["pdns", "other"]


def test_id_token_alg_none_and_hs256_rejected(idp):
    none_token = f"{b64({'alg': 'none', 'kid': 'rsa-1'})}.{b64(claims())}."
    with pytest.raises(OidcError) as ei:
        _validate(none_token, idp.meta)
    assert ei.value.code == "id_token"
    hs = jwt.encode({"alg": "HS256", "kid": "rsa-1"}, claims(), OctKey.import_key("client-secret" * 3),
                    algorithms=["HS256"])
    with pytest.raises(OidcError) as ei:
        _validate(hs, idp.meta)
    assert ei.value.code == "id_token"


def test_id_token_kid_rotation_refreshes_once(idp, clock):
    _validate(make_token(), idp.meta)                      # JWKS in den Cache
    assert idp.calls["/realms/firma/protocol/openid-connect/certs"] == 1
    clock.t += 120                                         # letzter Abruf > 60 s her
    idp.keys = [RSA2]
    assert _validate(make_token(RSA2), idp.meta)["sub"] == "user-1"
    assert idp.calls["/realms/firma/protocol/openid-connect/certs"] == 2
    clock.t += 10                                          # zweiter unbekannter kid innerhalb 60 s
    unknown = RSAKey.generate_key(2048, parameters={"kid": "rsa-3"})
    with pytest.raises(OidcError) as ei:
        _validate(make_token(unknown), idp.meta)
    assert ei.value.log_detail == "kid unbekannt"
    assert idp.calls["/realms/firma/protocol/openid-connect/certs"] == 2


def test_id_token_bad_signature(idp):
    forged = make_token(RSAKey.generate_key(2048, parameters={"kid": "rsa-1"}))
    with pytest.raises(OidcError) as ei:
        _validate(forged, idp.meta)
    assert ei.value.code == "id_token"
    with pytest.raises(OidcError):
        _validate("kein.jwt", idp.meta)


# ---------------------------------------------------------------------------------------------
# Nr. 11 Token-Tausch
# ---------------------------------------------------------------------------------------------
def _form(req: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def test_exchange_code_basic_auth(idp):
    tok = run(sso_oidc.exchange_code(cfg(), idp.meta, code="the-code", code_verifier="v" * 50))
    assert tok["access_token"] == "at-1" and tok["id_token"]
    req = idp.last_token_request
    form = _form(req)
    assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"
    assert form["code_verifier"] == "v" * 50 and form["redirect_uri"] == REDIRECT
    assert "client_secret" not in form
    expected = base64.b64encode(b"pdns:client-secret").decode()
    assert req.headers["authorization"] == f"Basic {expected}"


def test_exchange_code_post_and_none(idp):
    run(sso_oidc.exchange_code(cfg(token_auth_method="client_secret_post"), idp.meta, code="c", code_verifier="v"))
    form = _form(idp.last_token_request)
    assert form["client_id"] == "pdns" and form["client_secret"] == "client-secret"
    assert "authorization" not in idp.last_token_request.headers
    run(sso_oidc.exchange_code(cfg(token_auth_method="none", client_secret=""), idp.meta, code="c", code_verifier="v"))
    form = _form(idp.last_token_request)
    assert form["client_id"] == "pdns" and "client_secret" not in form


@pytest.mark.parametrize("status,body,detail", [
    (400, {"error": "invalid_grant", "error_description": "geheim"}, "invalid_grant"),
    (200, {"access_token": "a", "token_type": "Bearer"}, "id_token fehlt"),
    (200, {"access_token": "a", "token_type": "mac", "id_token": "x"}, "Bearer"),
])
def test_exchange_code_errors(idp, status, body, detail):
    idp.token_response = (status, body)
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.exchange_code(cfg(), idp.meta, code="c", code_verifier="v"))
    assert ei.value.code == "token" and detail in ei.value.log_detail
    assert "geheim" not in ei.value.log_detail


def test_exchange_code_server_and_network_errors(idp):
    idp.token_response = (502, {})
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.exchange_code(cfg(), idp.meta, code="c", code_verifier="v"))
    assert ei.value.code == "token"
    idp.fail["/realms/firma/protocol/openid-connect/token"] = httpx.ReadTimeout("slow")
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.exchange_code(cfg(), idp.meta, code="c", code_verifier="v"))
    assert ei.value.log_detail == "ReadTimeout"


# ---------------------------------------------------------------------------------------------
# Nr. 12 UserInfo
# ---------------------------------------------------------------------------------------------
def test_userinfo_ok_mismatch_and_errors(idp):
    assert run(sso_oidc.fetch_userinfo(idp.meta, "at-1", "user-1"))["groups"] == ["pdns-admins"]
    idp.userinfo = {"sub": "someone-else"}
    with pytest.raises(OidcError) as ei:
        run(sso_oidc.fetch_userinfo(idp.meta, "at-1", "user-1"))
    assert ei.value.code == "id_token" and ei.value.log_detail == "userinfo sub"
    idp.fail["/realms/firma/protocol/openid-connect/userinfo"] = httpx.ConnectError("down")
    assert run(sso_oidc.fetch_userinfo(idp.meta, "at-1", "user-1")) == {}
    assert run(sso_oidc.fetch_userinfo({}, "at-1", "user-1")) == {}

    def jwt_userinfo(request):
        return httpx.Response(200, content=b"eyJ...", headers={"content-type": "application/jwt"})

    sso_oidc.set_transport_for_tests(httpx.MockTransport(jwt_userinfo))
    assert run(sso_oidc.fetch_userinfo(idp.meta, "at-1", "user-1")) == {}
    sso_oidc.set_transport_for_tests(httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    assert run(sso_oidc.fetch_userinfo(idp.meta, "at-1", "user-1")) == {}


def test_complete_authorization_merges_userinfo(idp):
    payload = {"cv": "verifier", "n": "n-1"}
    prof = run(sso_oidc.complete_authorization(cfg(), idp.meta, code="c", state_payload=payload))
    assert prof.groups == ["pdns-admins"]                  # aus UserInfo
    assert prof.email == "jdoe@example.com"                # ID-Token gewinnt
    assert prof.subject == "user-1" and prof.issuer == ISS
    idp.calls.clear()
    prof = run(sso_oidc.complete_authorization(cfg(use_userinfo=False), idp.meta, code="c", state_payload=payload))
    assert prof.groups is None and "/realms/firma/protocol/openid-connect/userinfo" not in idp.calls


# ---------------------------------------------------------------------------------------------
# Nr. 13 Autorisierungs-URL und State-Token
# ---------------------------------------------------------------------------------------------
def test_authorization_request_login():
    url, cookie = sso_oidc.create_authorization_request(cfg(scopes="profile email"), metadata())
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert url.startswith(ISS + "/protocol/openid-connect/auth?")
    assert q["response_type"] == "code" and q["client_id"] == "pdns" and q["redirect_uri"] == REDIRECT
    assert q["scope"] == "openid profile email" and q["code_challenge_method"] == "S256"
    assert q["response_mode"] == "query" and q["state"] and q["nonce"] and "prompt" not in q
    payload = sso_oidc.decode_oidc_state_token(cookie)
    assert payload["st"] == q["state"] and payload["n"] == q["nonce"] and payload["iss"] == ISS
    assert payload["it"] == "login" and "uid" not in payload and "pwv" not in payload
    assert len(payload["cv"]) >= 43 and q["code_challenge"] == sso_oidc.s256_challenge(payload["cv"])


def test_authorization_request_prompt_link_and_existing_query():
    user = SimpleNamespace(id=7, hashed_password="hash-abc")
    meta = metadata(authorization_endpoint="https://login.example/authorize?p=b2c_policy")
    url, cookie = sso_oidc.create_authorization_request(cfg(prompt="login"), meta, intent="link", user=user)
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert q["p"] == "b2c_policy" and q["prompt"] == "login"
    payload = sso_oidc.decode_oidc_state_token(cookie)
    assert payload["it"] == "link" and payload["uid"] == 7 and payload["pwv"] == password_version("hash-abc")
    with pytest.raises(ValueError):
        sso_oidc.create_authorization_request(cfg(), meta, intent="link")
    with pytest.raises(OidcError):
        sso_oidc.create_authorization_request(SsoConfig(oidc=OidcCfg(client_id="x")), meta)


def test_state_token_rejects_foreign_or_broken_tokens():
    assert sso_oidc.decode_oidc_state_token(create_access_token({"sub": "1"})) is None
    assert sso_oidc.decode_oidc_state_token("") is None
    assert sso_oidc.decode_oidc_state_token(None) is None
    good = sso_oidc.create_oidc_state_token(state="s", nonce="n", code_verifier="v", issuer=ISS, intent="login")
    assert sso_oidc.decode_oidc_state_token(good)["st"] == "s"
    assert sso_oidc.decode_oidc_state_token(good[:-3] + "AAA") is None
    link_wo_uid = sso_oidc.create_oidc_state_token(state="s", nonce="n", code_verifier="v", issuer=ISS,
                                                   intent="link")
    assert sso_oidc.decode_oidc_state_token(link_wo_uid) is None
    with pytest.raises(ValueError):
        sso_oidc.create_oidc_state_token(state="s", nonce="n", code_verifier="v", issuer=ISS, intent="x")


# ---------------------------------------------------------------------------------------------
# Nr. 14 Einmal-Verbrauch des State
# ---------------------------------------------------------------------------------------------
def test_consume_state_once_and_cleanup():
    assert sso_oidc.consume_oidc_state("abc") is True
    assert sso_oidc.consume_oidc_state("abc") is False
    assert sso_oidc._consume_oidc_state("abc") is False
    sso_oidc._OIDC_STATE_USED["old"] = time.time() - 1
    assert sso_oidc.consume_oidc_state("new") is True
    assert "old" not in sso_oidc._OIDC_STATE_USED
    assert sso_oidc.consume_oidc_state("") is False


# ---------------------------------------------------------------------------------------------
# Konfigurationstest (POST /settings/sso/test, F10 3.3.3)
# ---------------------------------------------------------------------------------------------
def test_configuration_success_does_not_touch_cache(idp):
    out = run(sso_oidc.test_configuration(cfg().oidc, REDIRECT))
    assert out["success"] is True and out["error"] is None
    d = out["details"]
    assert d["issuer"] == ISS and d["signing_algs"] == ["RS256", "ES256"] and d["pkce_s256"] is True
    assert d["jwks_keys"] == 2 and d["redirect_uri"] == REDIRECT
    assert out["warnings"] == ["Client-ID und Secret werden erst bei der ersten Anmeldung geprüft"]
    assert sso_oidc._discovery_cache == {} and sso_oidc._jwks_cache == {}


def test_configuration_warnings(idp):
    idp.meta = metadata(code_challenge_methods_supported=None, userinfo_endpoint=None)
    out = run(sso_oidc.test_configuration(cfg().oidc, None))
    assert out["success"] is True and out["details"]["pkce_s256"] is None
    assert "Der Anbieter meldet keine PKCE-Unterstützung (S256)" in out["warnings"]
    assert "Keine Basis-URL – Redirect-URI kann nicht gebildet werden" in out["warnings"]
    assert "Kein UserInfo-Endpunkt – Gruppen müssen im ID-Token stehen" in out["warnings"]


def test_configuration_errors(idp):
    idp.meta = metadata(issuer="https://other.example")
    out = run(sso_oidc.test_configuration(cfg().oidc, REDIRECT))
    assert out["success"] is False
    assert out["error"] == ("Issuer im Discovery-Dokument (https://other.example) passt nicht zur konfigurierten "
                            "Issuer-URL")
    idp.meta = metadata(jwks_uri="http://idp.example.com/certs")
    out = run(sso_oidc.test_configuration(cfg().oidc, REDIRECT))
    assert out["error"] == "Endpunkt jwks_uri nutzt kein HTTPS"
    idp.meta = metadata(id_token_signing_alg_values_supported=["HS256"])
    out = run(sso_oidc.test_configuration(cfg().oidc, REDIRECT))
    assert out["error"] == "Keine unterstützten Signaturalgorithmen (RS*/PS*/ES*/EdDSA) angeboten"
    idp.meta = metadata()
    idp.fail["/realms/firma/protocol/openid-connect/certs"] = httpx.ConnectError("down")
    out = run(sso_oidc.test_configuration(cfg().oidc, REDIRECT))
    assert out["error"] == "JWKS konnte nicht geladen werden: ConnectError"
    idp.fail["/realms/firma/.well-known/openid-configuration"] = httpx.ConnectError("down")
    out = run(sso_oidc.test_configuration(cfg().oidc, REDIRECT))
    assert out["error"] == "Discovery fehlgeschlagen: ConnectError"
    out = run(sso_oidc.test_configuration(cfg(issuer="http://idp.example.com").oidc, REDIRECT))
    assert out["error"] == "Discovery fehlgeschlagen: Die Issuer-URL muss mit https:// beginnen"


def test_clear_caches(idp):
    run(sso_oidc.get_provider_metadata(ISS))
    run(sso_oidc.get_jwks(idp.meta))
    assert sso_oidc._discovery_cache and sso_oidc._jwks_cache
    sso_oidc.clear_caches()
    assert not sso_oidc._discovery_cache and not sso_oidc._jwks_cache
