"""Lokalisierte Texte fuer ausgehende E-Mails (Passwort-Reset, Test-Mail).

Alles was rausgeht laeuft ueber diese Stelle, damit wir nicht spaeter im
Router wieder vergessen welche Mails deutsch/englisch sind. Neue Sprache
hinzufuegen: einfach unten einen weiteren Zweig ergaenzen und in
``SUPPORTED_LANGS`` aufnehmen.
"""

import html
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


def _validity_de(valid_hours: int) -> str:
    return "1 Stunde" if valid_hours == 1 else f"{valid_hours} Stunden"


def _validity_en(valid_hours: int) -> str:
    return "1 hour" if valid_hours == 1 else f"{valid_hours} hours"


def password_reset(
    lang: str,
    display_name: str,
    reset_url: str,
    *,
    valid_hours: int = 1,
    admin_initiated: bool = False,
) -> Tuple[str, str, str]:
    """Liefert ``(subject, body_html, body_text)`` fuer die Passwort-Reset-Mail.

    ``valid_hours``: Gueltigkeit des Links (Self-Service 1 h, Admin-Reset-Link 24 h, F3 E9).
    ``admin_initiated``: Ein Administrator hat den Link angefordert (andere Einleitung, Hinweis an den Admin
    statt "einfach ignorieren"). Der Default-Aufruf liefert exakt den Text von 2.4.1.
    """
    name = html.escape(display_name or "")
    safe_url = html.escape(reset_url, quote=True)
    valid_hours = max(1, int(valid_hours or 1))
    default_call = valid_hours == 1 and not admin_initiated
    if lang == "de":
        subject = "Passwort zuruecksetzen - PDNS Manager"
        validity = _validity_de(valid_hours)
        if admin_initiated:
            intro = "ein Administrator hat fuer dein Konto das Zuruecksetzen des Passworts angefordert."
            ignore = "Wenn du damit nicht gerechnet hast, wende dich an deinen Administrator."
        else:
            intro = "du hast eine Zuruecksetzung deines Passworts angefordert."
            ignore = "Falls du das nicht warst, kannst du diese E-Mail einfach ignorieren."
        body_html = (
            f"<p>Hallo {name},</p>"
            f"<p>{intro}</p>"
            f"<p>Klicke auf den folgenden Link, um ein neues Passwort zu setzen "
            f"(der Link ist {validity} gueltig):</p>"
            f'<p><a href="{safe_url}">{safe_url}</a></p>'
            f"<p>{ignore}</p>"
        )
        if default_call:
            body_text = f"Passwort zuruecksetzen: {reset_url}"
        else:
            body_text = (
                f"Hallo {display_name or ''},\n\n"
                f"{intro}\n\n"
                f"Passwort zuruecksetzen (der Link ist {validity} gueltig und nur einmal verwendbar):\n"
                f"{reset_url}\n\n"
                f"{ignore}"
            )
        return subject, body_html, body_text

    # Fallback / en
    subject = "Reset your password - PDNS Manager"
    validity = _validity_en(valid_hours)
    if admin_initiated:
        intro = "an administrator requested a password reset for your account."
        ignore = "If you did not expect this, please contact your administrator."
    else:
        intro = "You requested a password reset."
        ignore = "If this wasn't you, you can simply ignore this email."
    body_html = (
        f"<p>Hi {name},</p>"
        f"<p>{intro[0].upper() + intro[1:]}</p>"
        f"<p>Click the following link to set a new password "
        f"(the link is valid for {validity}):</p>"
        f'<p><a href="{safe_url}">{safe_url}</a></p>'
        f"<p>{ignore}</p>"
    )
    if default_call:
        body_text = f"Reset your password: {reset_url}"
    else:
        body_text = (
            f"Hi {display_name or ''},\n\n"
            f"{intro[0].upper() + intro[1:]}\n\n"
            f"Reset your password (the link is valid for {validity} and can be used once):\n"
            f"{reset_url}\n\n"
            f"{ignore}"
        )
    return subject, body_html, body_text


def test_email(lang: str) -> Tuple[str, str, str]:
    """Liefert ``(subject, body_html, body_text)`` fuer die Admin-Test-Mail."""
    if lang == "de":
        return (
            "PDNS Manager - Test-E-Mail",
            "<h2>Test erfolgreich!</h2>"
            "<p>Diese E-Mail wurde vom PDNS Manager gesendet.</p>"
            "<p>Dein SMTP ist korrekt konfiguriert.</p>",
            "Test erfolgreich! Diese E-Mail wurde vom PDNS Manager gesendet.",
        )
    return (
        "PDNS Manager - Test email",
        "<h2>Test successful!</h2>"
        "<p>This email was sent by PDNS Manager.</p>"
        "<p>Your SMTP is configured correctly.</p>",
        "Test successful! This email was sent by PDNS Manager.",
    )


# ----------------------------------------------------------------------------
# Welcome-Mail (nach erfolgreicher Registrierung; Admin kann den Text anpassen)
# ----------------------------------------------------------------------------
# Default-Templates pro Sprache - werden geladen, wenn Admin keine eigenen
# Texte gesetzt hat. Platzhalter (in geschweiften Klammern) werden in
# ``render_welcome_email`` ersetzt.
_WELCOME_DEFAULTS = {
    "de": (
        "Willkommen bei {app_name}, {display_name}!",
        (
            "Hallo {display_name},\n\n"
            "dein Konto bei {app_name} wurde erfolgreich erstellt.\n\n"
            "Benutzername: {username}\n"
            "Du kannst dich jetzt anmelden: {login_url}\n\n"
            "Viele Gruesse\n"
            "Dein {app_name}-Team"
        ),
    ),
    "en": (
        "Welcome to {app_name}, {display_name}!",
        (
            "Hi {display_name},\n\n"
            "your account on {app_name} has been created successfully.\n\n"
            "Username: {username}\n"
            "Sign in here: {login_url}\n\n"
            "Cheers,\n"
            "The {app_name} team"
        ),
    ),
    # SR/HR/BS sind sprachlich sehr nah - bewusst eigene Defaults, damit die
    # Anrede jeweils natuerlich klingt.
    "sr": (
        "Dobrodošli u {app_name}, {display_name}!",
        (
            "Zdravo {display_name},\n\n"
            "vaš nalog na {app_name} je uspešno kreiran.\n\n"
            "Korisničko ime: {username}\n"
            "Prijavite se ovde: {login_url}\n\n"
            "Pozdrav,\n"
            "{app_name} tim"
        ),
    ),
    "hr": (
        "Dobrodošli u {app_name}, {display_name}!",
        (
            "Pozdrav {display_name},\n\n"
            "vaš račun na {app_name} uspješno je kreiran.\n\n"
            "Korisničko ime: {username}\n"
            "Prijavite se ovdje: {login_url}\n\n"
            "Lijepi pozdrav,\n"
            "{app_name} tim"
        ),
    ),
    "bs": (
        "Dobrodošli u {app_name}, {display_name}!",
        (
            "Zdravo {display_name},\n\n"
            "vaš račun na {app_name} je uspješno kreiran.\n\n"
            "Korisničko ime: {username}\n"
            "Prijavite se ovdje: {login_url}\n\n"
            "Pozdrav,\n"
            "{app_name} tim"
        ),
    ),
    "hu": (
        "Üdvözlünk a {app_name} oldalán, {display_name}!",
        (
            "Szia {display_name},\n\n"
            "a fiókod a(z) {app_name} oldalon sikeresen létrejött.\n\n"
            "Felhasználónév: {username}\n"
            "Bejelentkezés: {login_url}\n\n"
            "Üdvözlettel,\n"
            "A {app_name} csapata"
        ),
    ),
}


def welcome_email_default(lang: str) -> Tuple[str, str]:
    """Liefert ``(subject, body)`` als Default-Template fuer die Welcome-Mail.

    Sprache wird auf den naechsten unterstuetzten Code gemappt, ansonsten Englisch.
    """
    code = (lang or "en").strip().lower()
    if code in _WELCOME_DEFAULTS:
        return _WELCOME_DEFAULTS[code]
    return _WELCOME_DEFAULTS["en"]


def render_welcome_email(
    *,
    lang: str,
    subject_template: Optional[str],
    body_template: Optional[str],
    username: str,
    display_name: Optional[str],
    email: Optional[str],
    app_name: str,
    login_url: str,
) -> Tuple[str, str, str]:
    """Rendert Subject + HTML/Text-Body fuer die Welcome-Mail.

    - Wenn der Admin keine Templates gesetzt hat (leere Strings), greifen die
      sprachbasierten Defaults aus ``_WELCOME_DEFAULTS``.
    - Platzhalter werden via ``str.format_map`` ersetzt; unbekannte Platzhalter
      bleiben als ``{xyz}`` stehen, damit nichts crasht.
    """
    subject_t = (subject_template or "").strip()
    body_t = body_template or ""
    if not subject_t or not body_t.strip():
        d_subject, d_body = welcome_email_default(lang)
        if not subject_t:
            subject_t = d_subject
        if not body_t.strip():
            body_t = d_body

    values = {
        "username": username,
        "display_name": display_name or username,
        "email": email or "",
        "app_name": app_name,
        "login_url": login_url,
    }

    class _Safe(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    safe_values = _Safe(values)
    subject = subject_t.format_map(safe_values)
    body_text = body_t.format_map(safe_values)

    # Fuer die HTML-Variante alle Platzhalter escapen: username/display_name/email
    # sind vom Nutzer frei waehlbar und duerfen kein eigenes HTML in die Mail bringen.
    html_values = _Safe({k: html.escape(str(v), quote=True) for k, v in values.items()})
    body_html_src = body_t.format_map(html_values)

    # Plain-Text-Body in einen einfachen HTML-Block wickeln, falls der Admin
    # keinen <html>-Tag selbst geschrieben hat. So sehen Mails in Gmail/Outlook
    # ordentlich aus, ohne dass der Admin HTML lernen muss.
    stripped = body_html_src.lstrip()
    if stripped.startswith("<") and ">" in stripped[:80]:
        body_html = body_html_src
    else:
        # Zeilenumbrueche -> <br>; doppelte -> Absatz-Trenner
        paragraphs = [p.strip() for p in body_html_src.split("\n\n") if p.strip()]
        body_html = "".join(
            "<p>" + p.replace("\n", "<br>") + "</p>" for p in paragraphs
        ) or f"<p>{body_html_src}</p>"

    return subject, body_html, body_text
