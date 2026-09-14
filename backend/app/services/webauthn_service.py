"""WebAuthn / Passkey-Service.

Kapselt die zwei FIDO2-Zeremonien (Registrierung = Attestation, Login = Assertion)
und die Relying-Party-Ableitung. Bewusst stateless gehalten: die Server-Challenge
wird in einem kurzlebigen, signierten JWT (siehe app.core.auth) an die Zeremonie
gebunden – kein Redis/keine Server-State-Tabelle nötig.

Backend-Library: py_webauthn (Duo Labs). Das JSON-Format von ``options_to_json``
passt 1:1 zu ``@simplewebauthn/browser`` im Frontend.
"""
from __future__ import annotations

import json
import logging
import secrets
from typing import Optional
from urllib.parse import urlparse

from fastapi import Request

from app.core.config import settings

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

logger = logging.getLogger(__name__)


class WebAuthnError(Exception):
    """Fehler in einer WebAuthn-Zeremonie (vom Router in HTTP 400 übersetzt)."""


def _origin_from_request(request: Request) -> str:
    """Ermittelt die Origin (Schema://host[:port]) der anfragenden Seite.

    Bevorzugt den vom Browser gesetzten ``Origin``-Header. Der ist vertrauenswürdig,
    weil JavaScript ihn nicht überschreiben kann (verbotener Header). Fällt auf
    X-Forwarded-* bzw. die Request-URL zurück, falls er mal fehlt.
    """
    origin = (request.headers.get("origin") or "").strip()
    if origin:
        return origin.rstrip("/")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "https").split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}".rstrip("/")


def get_relying_party(request: Request) -> tuple[str, str, list[str]]:
    """Liefert (rp_id, rp_name, expected_origins) für die aktuelle Anfrage.

    - rp_id: ``WEBAUTHN_RP_ID`` falls gesetzt, sonst der Hostname der Origin (ohne Port).
    - rp_name: ``WEBAUTHN_RP_NAME`` falls gesetzt, sonst der App-Name.
    - expected_origins: konfigurierte Origins (WEBAUTHN_ORIGIN); die Origin der Anfrage
      nur, wenn ihr Host exakt der rp_id entspricht.
    """
    origin = _origin_from_request(request)
    parsed = urlparse(origin)
    host = parsed.hostname or "localhost"

    rp_id = (settings.WEBAUTHN_RP_ID or "").strip() or host
    rp_name = (settings.WEBAUTHN_RP_NAME or "").strip() or (settings.APP_NAME or "PDNS Manager")

    allowed: list[str] = []
    if settings.WEBAUTHN_ORIGIN:
        allowed.extend(o.strip().rstrip("/") for o in settings.WEBAUTHN_ORIGIN.split(",") if o.strip())
    # Die Origin der Anfrage wird NICHT blind akzeptiert (sonst waere die Origin-Pruefung
    # wirkungslos). Ohne Konfiguration gilt sie nur, wenn ihr Host EXAKT der RP-ID
    # entspricht – Subdomains einer als RP-ID gesetzten Parent-Domain also nicht.
    if origin and origin not in allowed and host.lower() == rp_id.lower():
        allowed.append(origin)
    return rp_id, rp_name, allowed


def ensure_user_handle(user) -> str:
    """Stellt sicher, dass der User einen stabilen, zufälligen WebAuthn-User-Handle hat.

    Setzt ihn (in-place) falls noch leer. Der Aufrufer muss flushen/committen.
    """
    handle = getattr(user, "webauthn_user_handle", None)
    if not handle:
        handle = bytes_to_base64url(secrets.token_bytes(32))
        user.webauthn_user_handle = handle
    return handle


def _transports_to_enums(transports: Optional[list]) -> Optional[list[AuthenticatorTransport]]:
    if not transports:
        return None
    out: list[AuthenticatorTransport] = []
    for t in transports:
        try:
            out.append(AuthenticatorTransport(t))
        except ValueError:
            continue  # unbekannten Transport-Hinweis ignorieren
    return out or None


# ========================
# Registrierung (Attestation)
# ========================
def build_registration_options(request: Request, user, existing_creds: list) -> tuple[str, str]:
    """Erzeugt Creation-Options + Challenge (base64url).

    Liefert (options_json, challenge_b64). ``existing_creds`` werden ausgeschlossen,
    damit derselbe Authenticator nicht doppelt registriert wird.
    """
    rp_id, rp_name, _ = get_relying_party(request)
    handle_b64 = ensure_user_handle(user)

    exclude = [
        PublicKeyCredentialDescriptor(
            id=base64url_to_bytes(c.credential_id),
            transports=_transports_to_enums(c.transports),
        )
        for c in existing_creds
    ]

    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=base64url_to_bytes(handle_b64),
        user_name=user.username,
        user_display_name=user.display_name or user.username,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=exclude,
        supported_pub_key_algs=[
            COSEAlgorithmIdentifier.ECDSA_SHA_256,
            COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
        ],
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verify_registration(request: Request, credential: dict, challenge_b64: str) -> dict:
    """Verifiziert die Attestation. Liefert ein Dict zum Speichern in WebAuthnCredential.

    Wirft WebAuthnError bei ungültiger Antwort.
    """
    rp_id, _, origins = get_relying_party(request)
    try:
        verification = verify_registration_response(
            credential=json.dumps(credential),
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=rp_id,
            expected_origin=origins,
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001 - library wirft diverse Typen
        logger.warning("WebAuthn registration verification failed: %s", exc)
        raise WebAuthnError("Passkey konnte nicht verifiziert werden") from exc

    transports = None
    try:
        transports = (credential.get("response") or {}).get("transports") or None
    except AttributeError:
        transports = None

    return {
        "credential_id": bytes_to_base64url(verification.credential_id),
        "public_key": bytes_to_base64url(verification.credential_public_key),
        "sign_count": int(verification.sign_count or 0),
        "transports": transports,
        "aaguid": getattr(verification, "aaguid", None),
    }


# ========================
# Login (Assertion)
# ========================
def build_authentication_options(request: Request, allow_creds: Optional[list] = None) -> tuple[str, str]:
    """Erzeugt Request-Options + Challenge (base64url).

    ``allow_creds`` leer/None => discoverable credentials (Usernameless / „1-Klick").
    """
    rp_id, _, _ = get_relying_party(request)
    allow = None
    if allow_creds:
        allow = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(c.credential_id),
                transports=_transports_to_enums(c.transports),
            )
            for c in allow_creds
        ]
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json(options), bytes_to_base64url(options.challenge)


def verify_authentication(request: Request, credential: dict, challenge_b64: str, cred_row) -> int:
    """Verifiziert die Assertion gegen den gespeicherten Public-Key.

    Liefert den neuen sign_count. Wirft WebAuthnError bei ungültiger Antwort.
    """
    rp_id, _, origins = get_relying_party(request)
    try:
        verification = verify_authentication_response(
            credential=json.dumps(credential),
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=rp_id,
            expected_origin=origins,
            credential_public_key=base64url_to_bytes(cred_row.public_key),
            credential_current_sign_count=int(cred_row.sign_count or 0),
            require_user_verification=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("WebAuthn authentication verification failed: %s", exc)
        raise WebAuthnError("Passkey konnte nicht verifiziert werden") from exc
    return int(verification.new_sign_count or 0)


def extract_credential_id(credential: dict) -> Optional[str]:
    """Holt die (base64url-)Credential-ID aus der Browser-Antwort für den DB-Lookup."""
    if not isinstance(credential, dict):
        return None
    cid = credential.get("id") or credential.get("rawId")
    return cid if isinstance(cid, str) and cid else None
