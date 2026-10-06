"""OIDC (Authorization Code Flow mit PKCE S256): Discovery, JWKS, Token-Tausch, ID-Token, UserInfo (F10 5.4).

- Discovery und JWKS werden im Prozess gecacht (1 h; bei Fehlern bis 24 h alt weiterverwendet). Ein unbekannter
  ``kid`` loest hoechstens alle 60 s einen JWKS-Neuabruf aus (Key-Rotation).
- Keine Redirects, Antworten max. 1 MB, Timeouts 5 s Connect / 10 s gesamt; HTTPS-Pflicht fuer alle Endpunkte
  (``SSO_ALLOW_INSECURE`` erlaubt ``http://`` nur fuer Tests). Private Netze sind erlaubt (interner IdP).
- ID-Token mit joserfc (``authlib.jose`` ist in Authlib 1.8 abgekuendigt); nur asymmetrische Algorithmen,
  ``iss`` exakt aus der Discovery, ``aud``/``azp``, ``exp``/``iat`` (60 s Spielraum, ``iat`` hoechstens 10 min alt),
  ``nonce``. Der Token-Tausch laeuft ueber Authlibs ``AsyncOAuth2Client``.
- State-Cookie: signierter Kurzzeit-JWT (``create_oidc_state_token``) mit State, Nonce und Code-Verifier;
  ``consume_oidc_state`` verhindert die Wiederverwendung (prozesslokal, Single-Worker).
- Nie geloggt: Client-Secret, ``code``, ``state``, ``nonce``, Code-Verifier, Tokens, ``error_description``.

Tests ersetzen den HTTP-Transport ueber ``set_transport_for_tests(httpx.MockTransport(...))``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from jose import jwt as jose_jwt
from joserfc import jwt
from joserfc.errors import ClaimError, InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry

from app.core.auth import decode_token, password_version
from app.core.config import settings
from app.services.sso_provisioning import ExternalProfile
from app.services.sso_settings import OidcCfg, SsoConfig, insecure_allowed, redirect_uri

# Authlib bevorzugt das Paket httpx2 und faellt mit AuthlibDeprecationWarning auf httpx zurueck; authlib.deprecate
# setzt dafuer "always" – ohne Gegenmassnahme stuende bei jedem Start eine Warnung im Log (F10 5.13).
from authlib.deprecate import AuthlibDeprecationWarning  # noqa: E402

with warnings.catch_warnings():
    warnings.simplefilter("ignore", AuthlibDeprecationWarning)
    from authlib.integrations.base_client import OAuthError  # noqa: E402
    from authlib.integrations.httpx_client import AsyncOAuth2Client  # noqa: E402

logger = logging.getLogger(__name__)

ASYMMETRIC_ALGS = (
    "RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA", "Ed25519",
)
_DISCOVERY_TTL = 3600.0
_DISCOVERY_STALE_MAX = 86400.0
_JWKS_TTL = 3600.0
_JWKS_STALE_MAX = 86400.0
_JWKS_MIN_REFRESH = 60.0
_MAX_DOC_BYTES = 1_048_576
_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
_LEEWAY = 60
_IAT_MAX_AGE = 600
_MAX_GROUPS = 500
_SUPPORTED_LOCALES = frozenset({"de", "en", "sr", "hr", "bs", "hu"})

TOKEN_TYPE_OIDC_STATE = "oidc_state"
STATE_TTL_SECONDS = 600
_OIDC_STATE_TTL = 11 * 60.0
INTENTS = ("login", "link")

_discovery_cache: dict[str, tuple[dict, float]] = {}
_jwks_cache: dict[str, tuple[KeySet, float]] = {}       # Schluessel: jwks_uri
_OIDC_STATE_USED: dict[str, float] = {}

# Uhr der Caches (Tests ersetzen sie)
_now: Callable[[], float] = time.monotonic
# HTTP-Transport fuer Tests (httpx.MockTransport); None = echtes Netz
_transport: Optional[httpx.AsyncBaseTransport] = None


class OidcError(Exception):
    """Fehler im OIDC-Ablauf. ``code``: config | discovery | token | id_token | userinfo.

    ``log_detail``: kurzer technischer Grund ohne Geheimnisse (fuer Log/Audit ``error_message``, max. 200 Zeichen).
    """

    def __init__(self, code: str, log_detail: str = ""):
        super().__init__(code)
        self.code = code
        self.log_detail = (log_detail or "")[:200]

    def __str__(self) -> str:
        return f"{self.code}: {self.log_detail}" if self.log_detail else self.code


def clear_caches() -> None:
    """Discovery- und JWKS-Cache leeren (nach Aenderung von Issuer/Client-ID/Aktivierung)."""
    _discovery_cache.clear()
    _jwks_cache.clear()


def set_transport_for_tests(transport: Optional[httpx.AsyncBaseTransport]) -> None:
    global _transport
    _transport = transport


def reset_for_tests() -> None:
    global _now, _transport
    clear_caches()
    _OIDC_STATE_USED.clear()
    _now = time.monotonic
    _transport = None


def _user_agent() -> str:
    return f"PDNS-Manager/{getattr(settings, 'APP_VERSION', '') or 'dev'}"


def _http_client() -> httpx.AsyncClient:
    kwargs: dict[str, Any] = {
        "timeout": _HTTP_TIMEOUT,
        "follow_redirects": False,
        "headers": {"Accept": "application/json", "User-Agent": _user_agent()},
    }
    if _transport is not None:
        kwargs["transport"] = _transport
    return httpx.AsyncClient(**kwargs)   # trust_env bleibt an (Firmen-Proxy)


def _oauth_client(cfg: SsoConfig) -> AsyncOAuth2Client:
    kwargs: dict[str, Any] = {
        "client_id": cfg.oidc.client_id,
        "client_secret": cfg.oidc.client_secret or None,
        "token_endpoint_auth_method": cfg.oidc.token_auth_method,
        "timeout": _HTTP_TIMEOUT,
        "follow_redirects": False,
        "headers": {"User-Agent": _user_agent()},
    }
    if _transport is not None:
        kwargs["transport"] = _transport
    return AsyncOAuth2Client(**kwargs)


def _check_url(url: Any, what: str) -> None:
    """Absolute http(s)-URL mit Host; HTTPS Pflicht (ausser SSO_ALLOW_INSECURE)."""
    if not isinstance(url, str) or not url:
        raise OidcError("discovery", f"{what} fehlt")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise OidcError("discovery", f"{what} ist keine gültige URL") from None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname:
        raise OidcError("discovery", f"{what} ist keine gültige URL")
    if scheme != "https" and not insecure_allowed():
        raise OidcError("discovery", f"{what} nutzt kein HTTPS")


async def _get_json(url: str, what: str, *, headers: Optional[dict] = None) -> tuple[int, Any, str]:
    """GET ohne Redirects mit 1-MB-Grenze. Rueckgabe (Status, JSON oder None, Content-Type)."""
    async with _http_client() as client:
        async with client.stream("GET", url, headers=headers) as resp:
            ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if resp.status_code != 200:
                return resp.status_code, None, ctype
            length = resp.headers.get("content-length")
            if length and length.isdigit() and int(length) > _MAX_DOC_BYTES:
                raise OidcError("discovery", f"{what}: Antwort größer als 1 MB")
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_DOC_BYTES:
                    raise OidcError("discovery", f"{what}: Antwort größer als 1 MB")
    if ctype == "application/jwt":
        return 200, None, ctype
    try:
        data = json.loads(bytes(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise OidcError("discovery", f"{what}: Antwort ist kein JSON") from None
    return 200, data, ctype


# ---------------------------------------------------------------------------------------------
# Discovery und JWKS
# ---------------------------------------------------------------------------------------------
class DiscoveryError(OidcError):
    """Discovery-Fehler mit Art (fuer die Texte des Test-Endpunkts)."""

    def __init__(self, kind: str, log_detail: str, *, found_issuer: Optional[str] = None):
        super().__init__("discovery", log_detail)
        self.kind = kind                    # fetch | issuer_mismatch | https | invalid
        self.found_issuer = found_issuer


def _metadata_url(issuer: str) -> str:
    return issuer.rstrip("/") + "/.well-known/openid-configuration"


async def _load_metadata(issuer: str) -> dict:
    """Discovery-Dokument laden und pruefen (ohne Cache)."""
    if not issuer:
        raise DiscoveryError("invalid", "Issuer-URL fehlt")
    try:
        _check_url(issuer, "Issuer-URL")
    except OidcError as exc:
        raise DiscoveryError("https", exc.log_detail) from None
    url = _metadata_url(issuer)
    try:
        status, doc, _ = await _get_json(url, "Discovery")
    except OidcError as exc:
        raise DiscoveryError("fetch", exc.log_detail) from None
    except httpx.HTTPError as exc:
        raise DiscoveryError("fetch", f"{type(exc).__name__}") from None
    if status != 200:
        raise DiscoveryError("fetch", f"HTTP {status}")
    if not isinstance(doc, dict):
        raise DiscoveryError("invalid", "Discovery-Dokument ist kein JSON-Objekt")
    found = doc.get("issuer")
    if not isinstance(found, str) or found.rstrip("/") != issuer.rstrip("/"):
        raise DiscoveryError("issuer_mismatch", "Issuer passt nicht zur Konfiguration",
                             found_issuer=str(found)[:200] if found is not None else None)
    for key, what in (("authorization_endpoint", "Endpunkt authorization_endpoint"),
                      ("token_endpoint", "Endpunkt token_endpoint"), ("jwks_uri", "Endpunkt jwks_uri")):
        try:
            _check_url(doc.get(key), what)
        except OidcError as exc:
            raise DiscoveryError("https", exc.log_detail) from None
    if doc.get("userinfo_endpoint") is not None:
        try:
            _check_url(doc.get("userinfo_endpoint"), "Endpunkt userinfo_endpoint")
        except OidcError as exc:
            raise DiscoveryError("https", exc.log_detail) from None
    return doc


async def get_provider_metadata(issuer: str, *, force: bool = False) -> dict:
    """Discovery mit Cache (1 h). Bei Fehlern wird ein bis zu 24 h alter Eintrag weiterverwendet."""
    key = issuer.rstrip("/")
    cached = _discovery_cache.get(key)
    now = _now()
    if cached and not force and now - cached[1] < _DISCOVERY_TTL:
        return cached[0]
    try:
        doc = await _load_metadata(issuer)
    except OidcError as exc:
        if cached and now - cached[1] < _DISCOVERY_STALE_MAX:
            logger.warning("OIDC-Discovery fehlgeschlagen (%s) – verwende zwischengespeicherte Metadaten", exc)
            return cached[0]
        raise
    _discovery_cache[key] = (doc, now)
    return doc


def _registry(algs: list[str]) -> JWSRegistry:
    reg = JWSRegistry(algorithms=algs, strict_check_header=False)
    reg.max_header_length = 8192   # x5c-Ketten im Header einiger IdPs
    return reg


async def _load_jwks(uri: str) -> KeySet:
    try:
        status, doc, _ = await _get_json(uri, "JWKS")
    except httpx.HTTPError as exc:
        raise OidcError("discovery", f"JWKS: {type(exc).__name__}") from None
    if status != 200:
        raise OidcError("discovery", f"JWKS: HTTP {status}")
    if not isinstance(doc, dict) or not isinstance(doc.get("keys"), list):
        raise OidcError("discovery", "JWKS: kein Schlüsselsatz")
    try:
        return KeySet.import_key_set(doc)
    except (JoseError, ValueError, TypeError, KeyError) as exc:
        raise OidcError("discovery", f"JWKS: {type(exc).__name__}") from None


async def get_jwks(metadata: dict, *, force: bool = False) -> KeySet:
    """JWKS mit Cache (1 h). ``force`` holt neu – hoechstens alle 60 s (sonst bleibt der Cache)."""
    uri = metadata["jwks_uri"]
    cached = _jwks_cache.get(uri)
    now = _now()
    if cached:
        age = now - cached[1]
        if not force and age < _JWKS_TTL:
            return cached[0]
        if force and age < _JWKS_MIN_REFRESH:
            return cached[0]
    try:
        keys = await _load_jwks(uri)
    except OidcError as exc:
        if cached and not force and now - cached[1] < _JWKS_STALE_MAX:
            logger.warning("OIDC-JWKS-Abruf fehlgeschlagen (%s) – verwende zwischengespeicherte Schluessel", exc)
            return cached[0]
        raise
    _jwks_cache[uri] = (keys, now)
    return keys


def allowed_algs(metadata: dict) -> list[str]:
    """Angebotene asymmetrische Signaturalgorithmen (Default RS256); keine -> ``OidcError("discovery")``."""
    offered = metadata.get("id_token_signing_alg_values_supported")
    if offered is None:
        return ["RS256"]
    if not isinstance(offered, list):
        raise OidcError("discovery", "id_token_signing_alg_values_supported ist keine Liste")
    algs = [a for a in ASYMMETRIC_ALGS if a in offered]
    if not algs:
        raise OidcError("discovery", "keine unterstuetzten Signaturalgorithmen")
    return algs


# ---------------------------------------------------------------------------------------------
# State-Cookie
# ---------------------------------------------------------------------------------------------
def create_oidc_state_token(*, state: str, nonce: str, code_verifier: str, issuer: str, intent: str,
                            user_id: Optional[int] = None, pwv: Optional[str] = None) -> str:
    """Signierter Kurzzeit-JWT (10 min) fuer das State-Cookie (Muster wie WebAuthn-Challenge)."""
    if intent not in INTENTS:
        raise ValueError("unbekannter OIDC-Intent")
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "typ": TOKEN_TYPE_OIDC_STATE, "st": state, "n": nonce, "cv": code_verifier, "iss": issuer, "it": intent,
        # iat: Startzeit – der Abschluss einer Verknuepfung prueft damit den Sitzungs-Widerruf (W2-NACHARBEIT 4.1)
        "iat": int(now.timestamp()),
        "exp": now + timedelta(seconds=STATE_TTL_SECONDS),
    }
    if user_id is not None:
        payload["uid"] = int(user_id)
    if pwv is not None:
        payload["pwv"] = pwv
    return jose_jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_oidc_state_token(token: Optional[str]) -> Optional[dict]:
    """Payload des State-Cookies oder ``None`` (Signatur, Ablauf, Typ, Pflichtfelder). Kein Verbrauch."""
    if not token or not isinstance(token, str) or len(token) > 4096:
        return None
    payload = decode_token(token)
    if not payload or payload.get("typ") != TOKEN_TYPE_OIDC_STATE:
        return None
    for key in ("st", "n", "cv", "iss"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            return None
    if payload.get("it") not in INTENTS:
        return None
    if payload["it"] == "link":
        if not isinstance(payload.get("uid"), int) or not isinstance(payload.get("pwv"), str):
            return None
    return payload


def consume_oidc_state(state: str) -> bool:
    """Markiert einen State als verbraucht; False bei Wiederverwendung (Replay). Abgelaufene Eintraege entfallen."""
    now = time.time()
    for k, exp in list(_OIDC_STATE_USED.items()):
        if exp < now:
            _OIDC_STATE_USED.pop(k, None)
    if not state or state in _OIDC_STATE_USED:
        return False
    _OIDC_STATE_USED[state] = now + _OIDC_STATE_TTL
    return True


# Spec-Name (F10 5.2: _consume_oidc_state in core/auth.py)
_consume_oidc_state = consume_oidc_state


# ---------------------------------------------------------------------------------------------
# Autorisierung
# ---------------------------------------------------------------------------------------------
def _scopes(cfg: SsoConfig) -> str:
    tokens = [t for t in (cfg.oidc.scopes or "").split() if t]
    if "openid" not in tokens:
        tokens.insert(0, "openid")
    return " ".join(dict.fromkeys(tokens))


def s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _add_query(url: str, params: list[tuple[str, str]]) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True) + params
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def create_authorization_request(cfg: SsoConfig, metadata: dict, *, intent: str = "login",
                                 user=None) -> tuple[str, str]:
    """Autorisierungs-URL und Wert des State-Cookies. ``intent="link"`` bindet ``uid`` und ``pwv`` des Kontos."""
    if intent not in INTENTS:
        raise ValueError("unbekannter OIDC-Intent")
    if intent == "link" and user is None:
        raise ValueError("Verknuepfung ohne Benutzer")
    redirect = redirect_uri(cfg)
    if not redirect or not cfg.oidc.client_id:
        raise OidcError("config", "Redirect-URI oder Client-ID fehlt")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)   # 86 Zeichen (RFC 7636: 43-128)
    params = [
        ("response_type", "code"), ("client_id", cfg.oidc.client_id), ("redirect_uri", redirect),
        ("scope", _scopes(cfg)), ("state", state), ("nonce", nonce),
        ("code_challenge", s256_challenge(verifier)), ("code_challenge_method", "S256"),
        ("response_mode", "query"),
    ]
    if cfg.oidc.prompt:
        params.append(("prompt", cfg.oidc.prompt))
    url = _add_query(metadata["authorization_endpoint"], params)
    cookie = create_oidc_state_token(
        state=state, nonce=nonce, code_verifier=verifier, issuer=metadata["issuer"], intent=intent,
        user_id=user.id if user is not None else None,
        pwv=password_version(user.hashed_password) if user is not None else None,
    )
    return url, cookie


# ---------------------------------------------------------------------------------------------
# Token-Tausch, ID-Token, UserInfo
# ---------------------------------------------------------------------------------------------
async def exchange_code(cfg: SsoConfig, metadata: dict, *, code: str, code_verifier: str) -> dict:
    """Code gegen Tokens tauschen (Client-Authentifizierung laut Konfiguration). Pflicht: ``id_token``, Bearer."""
    try:
        async with _oauth_client(cfg) as client:
            token = await client.fetch_token(
                metadata["token_endpoint"], grant_type="authorization_code", code=code,
                code_verifier=code_verifier, redirect_uri=redirect_uri(cfg),
            )
    except OAuthError as exc:
        raise OidcError("token", f"OAuthError: {str(getattr(exc, 'error', '') or 'unbekannt')[:40]}") from None
    except httpx.HTTPError as exc:
        raise OidcError("token", type(exc).__name__) from None
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise OidcError("token", type(exc).__name__) from None
    if not isinstance(token, dict):
        raise OidcError("token", "Antwort ist kein Objekt")
    if not isinstance(token.get("id_token"), str) or not token["id_token"]:
        raise OidcError("token", "id_token fehlt")
    if str(token.get("token_type") or "").lower() != "bearer":
        raise OidcError("token", "token_type ist nicht Bearer")
    return dict(token)


def _decode(id_token: str, keys: KeySet, algs: list[str]):
    return jwt.decode(id_token, keys, algorithms=algs, registry=_registry(algs))


async def validate_id_token(id_token: str, metadata: dict, cfg: SsoConfig, *, nonce: str) -> dict:
    """ID-Token pruefen (Signatur, Algorithmus, iss/sub/aud/azp/exp/iat/nonce) und Claims liefern."""
    algs = allowed_algs(metadata)
    keys = await get_jwks(metadata)
    try:
        try:
            tok = _decode(id_token, keys, algs)
        except InvalidKeyIdError:
            keys = await get_jwks(metadata, force=True)      # Key-Rotation (hoechstens alle 60 s)
            try:
                tok = _decode(id_token, keys, algs)
            except InvalidKeyIdError:
                raise OidcError("id_token", "kid unbekannt") from None
    except OidcError:
        raise
    except (JoseError, ValueError, TypeError) as exc:
        raise OidcError("id_token", type(exc).__name__) from None
    if tok.header.get("alg") not in algs:
        raise OidcError("id_token", "alg")
    claims = tok.claims
    if not isinstance(claims, dict):
        raise OidcError("id_token", "Claims sind kein Objekt")
    client_id = cfg.oidc.client_id
    registry = jwt.JWTClaimsRegistry(
        leeway=_LEEWAY,
        iss={"essential": True, "value": metadata["issuer"]},
        sub={"essential": True},
        aud={"essential": True, "value": client_id},
        exp={"essential": True},
        iat={"essential": True},
        nonce={"essential": True, "value": nonce},
    )
    try:
        registry.validate(claims)
    except ClaimError as exc:
        raise OidcError("id_token", f"Claim {getattr(exc, 'claim', '') or type(exc).__name__}"[:60]) from None
    except (JoseError, ValueError, TypeError) as exc:
        raise OidcError("id_token", type(exc).__name__) from None
    aud = claims["aud"]
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
        raise OidcError("id_token", "azp")
    if "azp" in claims and claims["azp"] != client_id:
        raise OidcError("id_token", "azp")
    if isinstance(claims["iat"], bool) or claims["iat"] < time.time() - _IAT_MAX_AGE - _LEEWAY:
        raise OidcError("id_token", "iat zu alt")
    sub = claims["sub"]
    if not isinstance(sub, str) or not 0 < len(sub) <= 255:
        raise OidcError("id_token", "sub")
    return dict(claims)


async def fetch_userinfo(metadata: dict, access_token: str, sub: str) -> dict:
    """UserInfo als JSON-Objekt; ``sub`` muss passen (OIDC Core 5.3.2). Netz-/HTTP-Fehler -> ``{}`` (Warnung)."""
    url = metadata.get("userinfo_endpoint")
    if not url or not access_token:
        return {}
    try:
        status, data, ctype = await _get_json(url, "UserInfo", headers={"Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError as exc:
        logger.warning("OIDC-UserInfo nicht erreichbar (%s) – Anmeldung mit den Claims des ID-Tokens", type(exc).__name__)
        return {}
    except OidcError as exc:
        logger.warning("OIDC-UserInfo unbrauchbar (%s) – Anmeldung mit den Claims des ID-Tokens", exc.log_detail)
        return {}
    if status != 200:
        logger.warning("OIDC-UserInfo antwortet mit HTTP %s – Anmeldung mit den Claims des ID-Tokens", status)
        return {}
    if ctype == "application/jwt":
        logger.warning("OIDC-UserInfo als JWT wird nicht unterstuetzt – Anmeldung mit den Claims des ID-Tokens")
        return {}
    if not isinstance(data, dict):
        logger.warning("OIDC-UserInfo ist kein JSON-Objekt – Anmeldung mit den Claims des ID-Tokens")
        return {}
    if data.get("sub") != sub:
        raise OidcError("id_token", "userinfo sub")
    return data


# ---------------------------------------------------------------------------------------------
# Claims -> Profil
# ---------------------------------------------------------------------------------------------
_MISSING = object()


def get_claim(claims: dict, path: str):
    """Claim per Name oder Punkt-Pfad (``realm_access.roles``); exakter Name hat Vorrang (``https://x.y/groups``)."""
    if not path or not isinstance(claims, dict):
        return None
    if path in claims:
        return claims[path]
    cur: Any = claims
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _short_str(value: Any, limit: int = 255) -> Optional[str]:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        s = str(value).strip()
        return s[:limit] if s else None
    return None


def extract_profile(cfg: SsoConfig, issuer: str, claims: dict) -> ExternalProfile:
    o: OidcCfg = cfg.oidc
    username = _short_str(get_claim(claims, o.username_claim), 255) or ""
    email_verified = _as_bool(claims.get("email_verified"))
    email = _short_str(get_claim(claims, o.email_claim), 255) if o.email_claim else None
    if email is not None and ("@" not in email or email_verified is False):
        email = None
    display_name = _short_str(get_claim(claims, o.name_claim), 255) if o.name_claim else None
    groups: Optional[list[str]] = None
    if o.groups_claim:
        raw = get_claim(claims, o.groups_claim)
        if isinstance(raw, str):
            groups = [raw]
        elif isinstance(raw, list):
            groups = [str(g) for g in raw if isinstance(g, (str, int)) and not isinstance(g, bool)][:_MAX_GROUPS]
        elif raw is not None:
            groups = []
    locale = None
    loc = claims.get("locale")
    if isinstance(loc, str) and loc[:2].lower() in _SUPPORTED_LOCALES:
        locale = loc[:2].lower()
    return ExternalProfile(
        source="oidc", issuer=issuer, subject=str(claims["sub"]), username_hint=username, email=email,
        email_verified=email_verified, display_name=display_name, groups=groups, locale=locale,
    )


async def complete_authorization(cfg: SsoConfig, metadata: dict, *, code: str, state_payload: dict) -> ExternalProfile:
    """Callback-Kern: Token-Tausch, ID-Token-Pruefung, optional UserInfo, Profil."""
    token = await exchange_code(cfg, metadata, code=code, code_verifier=state_payload["cv"])
    claims = await validate_id_token(token["id_token"], metadata, cfg, nonce=state_payload["n"])
    if cfg.oidc.use_userinfo and metadata.get("userinfo_endpoint") and token.get("access_token"):
        ui = await fetch_userinfo(metadata, str(token["access_token"]), claims["sub"])
        claims = {**ui, **claims}   # ID-Token gewinnt bei Konflikten (iss/sub/aud/nonce)
    return extract_profile(cfg, metadata["issuer"], claims)


# ---------------------------------------------------------------------------------------------
# Test der Konfiguration (POST /settings/sso/test)
# ---------------------------------------------------------------------------------------------
async def test_configuration(oidc: OidcCfg, redirect: Optional[str]) -> dict:
    """Discovery und JWKS frisch laden (Cache bleibt unberuehrt); Struktur F10 3.3.3."""
    warnings_out: list[str] = []
    details: dict[str, Any] = {"redirect_uri": redirect}
    try:
        doc = await _load_metadata(oidc.issuer)
    except DiscoveryError as exc:
        if exc.kind == "issuer_mismatch":
            err = (f"Issuer im Discovery-Dokument ({exc.found_issuer or '–'}) passt nicht zur konfigurierten "
                   f"Issuer-URL")
        elif exc.kind == "https" and exc.log_detail == "Issuer-URL nutzt kein HTTPS":
            err = "Discovery fehlgeschlagen: Die Issuer-URL muss mit https:// beginnen"
        elif exc.kind == "https" and exc.log_detail.endswith("nutzt kein HTTPS"):
            err = exc.log_detail   # "Endpunkt <name> nutzt kein HTTPS"
        else:
            err = f"Discovery fehlgeschlagen: {exc.log_detail}"
        return {"success": False, "message": None, "error": err, "warnings": warnings_out, "details": details}
    details.update({
        "issuer": doc.get("issuer"),
        "authorization_endpoint": doc.get("authorization_endpoint"),
        "token_endpoint": doc.get("token_endpoint"),
        "userinfo_endpoint": doc.get("userinfo_endpoint"),
        "jwks_uri": doc.get("jwks_uri"),
        "end_session_endpoint": doc.get("end_session_endpoint"),
        "signing_algs": [],
        "pkce_s256": None,
        "jwks_keys": 0,
    })
    methods = doc.get("code_challenge_methods_supported")
    if isinstance(methods, list):
        details["pkce_s256"] = "S256" in methods
    try:
        algs = allowed_algs(doc)
    except OidcError:
        return {"success": False, "message": None,
                "error": "Keine unterstützten Signaturalgorithmen (RS*/PS*/ES*/EdDSA) angeboten",
                "warnings": warnings_out, "details": details}
    details["signing_algs"] = algs
    try:
        keys = await _load_jwks(doc["jwks_uri"])
    except OidcError as exc:
        reason = exc.log_detail.removeprefix("JWKS: ")
        return {"success": False, "message": None, "error": f"JWKS konnte nicht geladen werden: {reason}",
                "warnings": warnings_out, "details": details}
    details["jwks_keys"] = len(keys.keys)
    if details["pkce_s256"] is not True:
        warnings_out.append("Der Anbieter meldet keine PKCE-Unterstützung (S256)")
    if not redirect:
        warnings_out.append("Keine Basis-URL – Redirect-URI kann nicht gebildet werden")
    if not doc.get("userinfo_endpoint"):
        warnings_out.append("Kein UserInfo-Endpunkt – Gruppen müssen im ID-Token stehen")
    warnings_out.append("Client-ID und Secret werden erst bei der ersten Anmeldung geprüft")
    return {"success": True, "message": "Discovery und Schlüssel erfolgreich geladen", "error": None,
            "warnings": warnings_out, "details": details}
