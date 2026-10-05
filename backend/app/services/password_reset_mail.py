"""Versand von Passwort-Reset-Links (Self-Service "Passwort vergessen" und Admin-initiiert, F2/F3 5.5).

- Die Basis-URL kommt ausschliesslich aus der Konfiguration (``app_base_url`` in ``system_settings``, sonst der
  erste Eintrag von ``WEBAUTHN_ORIGIN``), **nie** aus dem Host-Header der Anfrage: sonst koennte ein Angreifer
  per "Passwort vergessen" einen Reset-Link auf seine eigene Domain zustellen lassen (Link-Poisoning).
- Token und Reset-URL werden nie geloggt.
- Fehler werden als ``ResetMailError`` mit Maschinencode gemeldet; der Aufrufer entscheidet, ob der Client davon
  erfaehrt (Admin-Endpunkt) oder nicht (Self-Service, immer gleiche Antwort).

Bereitgestellt fuer andere Workstreams: ``resolve_public_base_url(db)`` (F10 redirect_uri, F9 Hilfe-URL).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.auth import create_password_reset_token
from app.core.config import settings as app_settings
from app.services import system_settings
from app.services.email_service import get_smtp_settings, send_email
from app.services.email_templates import password_reset, pick_language

logger = logging.getLogger(__name__)

# Gueltigkeit der Links: Self-Service unveraendert 1 h, Admin-initiiert 24 h (F3 E9); Einmal-Nutzung ueber pwv.
SELF_SERVICE_VALID_MINUTES = 60
ADMIN_VALID_MINUTES = 1440

RESET_MAIL_ERROR_CODES = ("no_email", "no_base_url", "smtp_disabled", "send_failed")


class ResetMailError(Exception):
    """Reset-Mail nicht versendet. ``code``: no_email | no_base_url | smtp_disabled | send_failed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def resolve_public_base_url(db: AsyncSession) -> str:
    """Oeffentliche Basis-URL ohne abschliessenden Slash; "" wenn nichts konfiguriert ist."""
    base = (await system_settings.get_setting(db, "app_base_url") or "").strip()
    if not base:
        base = (app_settings.WEBAUTHN_ORIGIN or "").split(",")[0].strip()
    return base.rstrip("/")


async def reset_mail_available(db: AsyncSession) -> bool:
    """SMTP aktiv mit Host UND oeffentliche Basis-URL gesetzt (Voraussetzung fuer den Admin-Reset-Link)."""
    try:
        smtp = await get_smtp_settings(db)
    except Exception as exc:  # noqa: BLE001 - Anzeige-Flag, darf die Benutzerliste nicht brechen
        logger.warning("SMTP-Einstellungen nicht lesbar: %s", exc)
        return False
    if not smtp.get("enabled") or not smtp.get("host"):
        return False
    return bool(await resolve_public_base_url(db))


async def send_password_reset_mail(
    db: AsyncSession,
    user,
    *,
    valid_minutes: int = SELF_SERVICE_VALID_MINUTES,
    admin_initiated: bool = False,
) -> None:
    """Erzeugt einen Reset-Token (gebunden an den aktuellen Passwort-Hash) und versendet den Link.

    Wirft ``ResetMailError``; das Passwort des Nutzers bleibt unveraendert.
    """
    if not getattr(user, "email", None):
        raise ResetMailError("no_email")
    base = await resolve_public_base_url(db)
    if not base:
        logger.error(
            "Passwort-Reset nicht versendet: keine App-Basis-URL konfiguriert. "
            "Bitte unter Einstellungen -> Profil -> Oeffentliche Basis-URL eintragen "
            "(oder WEBAUTHN_ORIGIN in der .env setzen)."
        )
        raise ResetMailError("no_base_url")
    smtp = await get_smtp_settings(db)
    if not smtp.get("enabled") or not smtp.get("host"):
        logger.warning("SMTP not configured - cannot send password reset email")
        raise ResetMailError("smtp_disabled")

    token = create_password_reset_token(user.id, user.hashed_password, expires_minutes=valid_minutes)
    reset_url = f"{base}/reset-password?token={token}"
    lang = pick_language(getattr(user, "preferred_language", None), app_settings.DEFAULT_LANGUAGE)
    subject, body_html, body_text = password_reset(
        lang,
        user.display_name or user.username,
        reset_url,
        valid_hours=max(1, int(valid_minutes) // 60),
        admin_initiated=admin_initiated,
    )
    try:
        await run_in_threadpool(send_email, smtp, user.email, subject, body_html, body_text)
    except Exception as exc:  # noqa: BLE001 - SMTP-Fehler aller Art -> send_failed
        # Nur Typ und Text der Ausnahme, nie Token/URL
        logger.error("Failed to send password reset email (user_id=%s): %s: %s", user.id, type(exc).__name__, exc)
        raise ResetMailError("send_failed") from exc
