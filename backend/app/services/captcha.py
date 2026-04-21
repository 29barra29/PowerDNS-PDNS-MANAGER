"""Captcha-Service: serverseitige Verifikation fuer Turnstile, hCaptcha und reCAPTCHA v2.

Architektur:
- Provider + Keys liegen in der ``system_settings``-Tabelle (gleicher Key-Value-Store wie SMTP).
- Frontend bekommt Provider + ``site_key`` ueber ``/api/app-info`` (public, read-only)
  und rendert das passende Widget. Das ``secret_key`` verlaesst nie das Backend.
- Auth-Endpunkte (login/register/forgot-password) verifizieren das vom Browser
  zurueckgegebene Token gegen die Provider-API, bevor sie die Aktion ausfuehren.

Hinzufuegen weiterer Provider: ``PROVIDERS`` erweitern und ``_VERIFY_URLS`` ergaenzen.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Konstanten
# ----------------------------------------------------------------------------
PROVIDER_NONE = "none"
PROVIDER_TURNSTILE = "turnstile"
PROVIDER_HCAPTCHA = "hcaptcha"
PROVIDER_RECAPTCHA = "recaptcha"

PROVIDERS = {PROVIDER_NONE, PROVIDER_TURNSTILE, PROVIDER_HCAPTCHA, PROVIDER_RECAPTCHA}

# Provider-spezifische Verify-Endpunkte (alle akzeptieren application/x-www-form-urlencoded)
_VERIFY_URLS = {
    PROVIDER_TURNSTILE: "https://challenges.cloudflare.com/turnstile/v0/siteverify",
    PROVIDER_HCAPTCHA: "https://api.hcaptcha.com/siteverify",
    PROVIDER_RECAPTCHA: "https://www.google.com/recaptcha/api/siteverify",
}

# Settings-Keys in der DB
KEY_PROVIDER = "captcha_provider"
KEY_SITE_KEY = "captcha_site_key"
KEY_SECRET_KEY = "captcha_secret_key"
ALL_KEYS = (KEY_PROVIDER, KEY_SITE_KEY, KEY_SECRET_KEY)

# Timeout fuer den HTTPS-Call zum Provider (typisch <500ms; 5s sind grosszuegig)
_VERIFY_TIMEOUT = 5.0


# ----------------------------------------------------------------------------
# Settings I/O
# ----------------------------------------------------------------------------
async def get_captcha_settings(db: AsyncSession) -> dict:
    """Liest Captcha-Settings. ``secret_key`` wird mit zurueckgegeben - nur
    intern verwenden, NIE im API-Response an Nicht-Admins durchreichen."""
    from app.models.models import SystemSetting

    result = await db.execute(
        select(SystemSetting.key, SystemSetting.value).where(
            SystemSetting.key.in_(ALL_KEYS)
        )
    )
    rows = {r[0]: r[1] for r in result.all()}
    provider = (rows.get(KEY_PROVIDER) or PROVIDER_NONE).strip().lower()
    if provider not in PROVIDERS:
        provider = PROVIDER_NONE
    return {
        "provider": provider,
        "site_key": (rows.get(KEY_SITE_KEY) or "").strip(),
        "secret_key": (rows.get(KEY_SECRET_KEY) or "").strip(),
    }


async def save_captcha_settings(
    db: AsyncSession,
    *,
    provider: str,
    site_key: Optional[str],
    secret_key: Optional[str],
) -> None:
    """Speichert die Captcha-Settings in der DB.

    ``secret_key=None`` bedeutet *unveraendert lassen* (Pattern wie bei den
    PowerDNS-API-Keys). ``secret_key=""`` bedeutet *loeschen*.
    """
    from app.models.models import SystemSetting

    provider = (provider or PROVIDER_NONE).strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Unbekannter Captcha-Provider: {provider!r}")

    pairs = {
        KEY_PROVIDER: provider,
        KEY_SITE_KEY: (site_key or "").strip(),
    }
    if secret_key is not None:
        pairs[KEY_SECRET_KEY] = secret_key.strip()

    for key, value in pairs.items():
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(SystemSetting(key=key, value=value))

    await db.commit()


async def is_captcha_required(db: AsyncSession) -> bool:
    """True wenn ein Captcha konfiguriert ist (Provider != none + beide Keys gesetzt)."""
    s = await get_captcha_settings(db)
    return (
        s["provider"] != PROVIDER_NONE
        and bool(s["site_key"])
        and bool(s["secret_key"])
    )


# ----------------------------------------------------------------------------
# Token-Verifikation
# ----------------------------------------------------------------------------
async def verify_captcha_token(
    provider: str,
    secret_key: str,
    token: Optional[str],
    remote_ip: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Verifiziert ein vom Browser geliefertes Captcha-Token serverseitig.

    Liefert ``(success, error_message)``. ``error_message`` ist nur bei Misserfolg
    gesetzt und sollte in der UI angezeigt werden.
    """
    provider = (provider or PROVIDER_NONE).strip().lower()
    if provider == PROVIDER_NONE:
        return True, None
    if provider not in _VERIFY_URLS:
        return False, f"Unbekannter Captcha-Provider: {provider}"
    if not secret_key:
        return False, "Captcha-Secret fehlt in den Einstellungen"
    if not token:
        return False, "Captcha-Token fehlt"

    payload = {"secret": secret_key, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    url = _VERIFY_URLS[provider]
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT) as client:
            response = await client.post(url, data=payload)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException:
        logger.warning("Captcha-Verify Timeout (provider=%s)", provider)
        return False, "Captcha-Server hat nicht geantwortet"
    except Exception as exc:  # noqa: BLE001 - alle Fehler an User melden, mit Log
        logger.error("Captcha-Verify Fehler (provider=%s): %s", provider, exc)
        return False, "Captcha konnte nicht geprueft werden"

    success = bool(data.get("success"))
    if success:
        return True, None

    # Provider-spezifische Fehler-Codes ausgeben (hilft beim Debugging in den Logs).
    error_codes = data.get("error-codes") or data.get("errorCodes") or []
    logger.info("Captcha verify rejected (provider=%s, codes=%s)", provider, error_codes)
    if any(c in ("invalid-input-response", "missing-input-response", "timeout-or-duplicate")
           for c in error_codes):
        return False, "Captcha ist abgelaufen oder ungueltig - bitte nochmal probieren"
    return False, "Captcha-Pruefung fehlgeschlagen"


async def verify_or_raise(
    db: AsyncSession,
    token: Optional[str],
    remote_ip: Optional[str] = None,
) -> None:
    """Komfort-Wrapper fuer die Auth-Routes: lese aktuelle Settings, verify, raise HTTPException.

    Macht nichts, wenn kein Captcha konfiguriert ist. Macht alle Pruefungen still,
    wenn provider=none, und wirft sonst eine 400 mit klarer Fehlermeldung.
    """
    from fastapi import HTTPException

    s = await get_captcha_settings(db)
    if s["provider"] == PROVIDER_NONE or not s["secret_key"]:
        return  # Captcha nicht konfiguriert, alles erlaubt

    ok, error = await verify_captcha_token(
        provider=s["provider"],
        secret_key=s["secret_key"],
        token=token,
        remote_ip=remote_ip,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=error or "Captcha-Pruefung fehlgeschlagen")
