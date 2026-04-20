"""Lokalisierte Texte fuer ausgehende E-Mails (Passwort-Reset, Test-Mail).

Alles was rausgeht laeuft ueber diese Stelle, damit wir nicht spaeter im
Router wieder vergessen welche Mails deutsch/englisch sind. Neue Sprache
hinzufuegen: einfach unten einen weiteren Zweig ergaenzen und in
``SUPPORTED_LANGS`` aufnehmen.
"""

from typing import Optional, Tuple

SUPPORTED_LANGS = {"de", "en"}
_DEFAULT_FALLBACK = "en"


def pick_language(preferred: Optional[str], app_default: Optional[str]) -> str:
    """Waehlt die beste unterstuetzte Sprache aus:

    1. ``user.preferred_language`` (falls gesetzt und unterstuetzt)
    2. ``DEFAULT_LANGUAGE`` aus der App-Config (falls unterstuetzt)
    3. Fallback ``en``
    """
    for candidate in (preferred, app_default, _DEFAULT_FALLBACK):
        if not candidate:
            continue
        norm = candidate.strip().lower()
        if norm in SUPPORTED_LANGS:
            return norm
    return _DEFAULT_FALLBACK


def password_reset(lang: str, display_name: str, reset_url: str) -> Tuple[str, str, str]:
    """Liefert ``(subject, body_html, body_text)`` fuer die Passwort-Reset-Mail."""
    name = display_name or ""
    if lang == "de":
        subject = "Passwort zuruecksetzen - DNS Manager"
        body_html = (
            f"<p>Hallo {name},</p>"
            f"<p>du hast eine Zuruecksetzung deines Passworts angefordert.</p>"
            f"<p>Klicke auf den folgenden Link, um ein neues Passwort zu setzen "
            f"(der Link ist 1 Stunde gueltig):</p>"
            f'<p><a href="{reset_url}">{reset_url}</a></p>'
            f"<p>Falls du das nicht warst, kannst du diese E-Mail einfach ignorieren.</p>"
        )
        body_text = f"Passwort zuruecksetzen: {reset_url}"
        return subject, body_html, body_text

    # Fallback / en
    subject = "Reset your password - DNS Manager"
    body_html = (
        f"<p>Hi {name},</p>"
        f"<p>You requested a password reset.</p>"
        f"<p>Click the following link to set a new password "
        f"(the link is valid for 1 hour):</p>"
        f'<p><a href="{reset_url}">{reset_url}</a></p>'
        f"<p>If this wasn't you, you can simply ignore this email.</p>"
    )
    body_text = f"Reset your password: {reset_url}"
    return subject, body_html, body_text


def test_email(lang: str) -> Tuple[str, str, str]:
    """Liefert ``(subject, body_html, body_text)`` fuer die Admin-Test-Mail."""
    if lang == "de":
        return (
            "DNS Manager - Test-E-Mail",
            "<h2>Test erfolgreich!</h2>"
            "<p>Diese E-Mail wurde vom DNS Manager gesendet.</p>"
            "<p>Dein SMTP ist korrekt konfiguriert.</p>",
            "Test erfolgreich! Diese E-Mail wurde vom DNS Manager gesendet.",
        )
    return (
        "DNS Manager - Test email",
        "<h2>Test successful!</h2>"
        "<p>This email was sent by DNS Manager.</p>"
        "<p>Your SMTP is configured correctly.</p>",
        "Test successful! This email was sent by DNS Manager.",
    )
