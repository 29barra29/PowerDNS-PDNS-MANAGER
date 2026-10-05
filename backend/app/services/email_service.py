"""E-Mail-Versand ueber SMTP und die SMTP-/Welcome-Mail-Einstellungen (Key-Value in ``system_settings``).

- Lesen/Schreiben nur ueber ``services/system_settings.py``: ``smtp_password`` ist ein Secret-Key und liegt
  verschluesselt in der DB (F5). Ist er nicht entschluesselbar, liefert ``get_smtp_settings`` den Platzhalter
  ``UNREADABLE`` (``== ""``) und ``password_unreadable=True``; Versand und Verbindungstest brechen dann mit
  einer klaren Meldung ab, statt sich ohne Passwort anzumelden.
- Die Speicher-Funktionen machen nur ``flush`` – den Commit macht die Request-Dependency (``DbWrite``).
"""
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secrets import is_unreadable
from app.services.system_settings import get_settings, set_settings

logger = logging.getLogger(__name__)

# SMTP setting keys
SMTP_KEYS = [
    "smtp_host",
    "smtp_port",
    "smtp_username",
    "smtp_password",
    "smtp_from_email",
    "smtp_from_name",
    "smtp_encryption",  # "none", "starttls", "ssl"
    "smtp_enabled",
]

SMTP_PASSWORD_UNREADABLE = (
    "SMTP-Passwort kann nicht entschlüsselt werden – bitte unter Einstellungen → SMTP neu eintragen."
)


def _port(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 587


async def get_smtp_settings(db: AsyncSession) -> dict:
    """SMTP-Einstellungen aus der DB. ``password`` ist das Rohobjekt (kann ``UNREADABLE`` sein)."""
    settings = await get_settings(db, SMTP_KEYS)
    password = settings.get("smtp_password")
    if password is None:
        password = ""

    return {
        "host": settings.get("smtp_host") or "",
        "port": _port(settings.get("smtp_port") or "587"),
        "username": settings.get("smtp_username") or "",
        "password": password,
        "password_unreadable": is_unreadable(password),
        "from_email": settings.get("smtp_from_email") or "",
        "from_name": settings.get("smtp_from_name") or "PDNS Manager",
        "encryption": settings.get("smtp_encryption") or "starttls",
        "enabled": (settings.get("smtp_enabled") or "false") == "true",
    }


async def save_smtp_settings(db: AsyncSession, settings: dict):
    """SMTP-Einstellungen speichern (nur flush, kein Commit).

    ``password=None`` heisst: Passwort nicht anfassen (weder lesen noch neu schreiben).
    """
    key_map = {
        "host": "smtp_host",
        "port": "smtp_port",
        "username": "smtp_username",
        "password": "smtp_password",
        "from_email": "smtp_from_email",
        "from_name": "smtp_from_name",
        "encryption": "smtp_encryption",
        "enabled": "smtp_enabled",
    }

    values: dict[str, str] = {}
    for field, db_key in key_map.items():
        if field == "password" and settings.get("password") is None:
            continue
        value = settings.get(field, "")
        values[db_key] = "" if value is None else str(value)
    await set_settings(db, values)


def send_email(smtp_settings: dict, to_email: str, subject: str, body_html: str, body_text: str = None):
    """Send an email using the configured SMTP settings. Runs synchronously."""
    if not smtp_settings.get("enabled"):
        raise RuntimeError("SMTP ist nicht aktiviert. Bitte zuerst in den Einstellungen konfigurieren.")
    
    if not smtp_settings.get("host"):
        raise RuntimeError("Kein SMTP-Server konfiguriert.")

    if is_unreadable(smtp_settings.get("password")):
        raise RuntimeError(SMTP_PASSWORD_UNREADABLE)

    # Genau EINE Adresse: smtplib wuerde sonst alle kommagetrennten Empfaenger aus
    # dem To-Header bedienen (Mail-Relay ueber fremde Adressen).
    to_email = (to_email or "").strip()
    if not to_email or to_email.count("@") != 1 or any(ch in to_email for ch in ",;<>\"' \t\r\n"):
        raise RuntimeError("Ungueltige Empfaengeradresse.")

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{smtp_settings.get('from_name', 'PDNS Manager')} <{smtp_settings['from_email']}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    
    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    
    host = smtp_settings["host"]
    port = _port(smtp_settings.get("port", 587))
    encryption = smtp_settings.get("encryption", "starttls")
    
    try:
        if encryption == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            if encryption == "starttls":
                server.starttls(context=ssl.create_default_context())
        
        username = smtp_settings.get("username", "")
        password = smtp_settings.get("password", "")
        if username and password:
            server.login(username, password)
        
        server.send_message(msg, to_addrs=[to_email])
        server.quit()
        
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        raise RuntimeError(f"E-Mail-Versand fehlgeschlagen: {str(e)}")


# ----------------------------------------------------------------------------
# Welcome-Mail Settings (Key-Value, gleicher Store wie SMTP)
# ----------------------------------------------------------------------------
WELCOME_EMAIL_KEYS = [
    "welcome_email_enabled",
    "welcome_email_subject",
    "welcome_email_body",
]


async def get_welcome_email_settings(db: AsyncSession) -> dict:
    """Welcome-Mail-Einstellungen lesen. Leere Werte sind erlaubt - das UI
    blendet dann das Default-Template ein."""
    rows = await get_settings(db, WELCOME_EMAIL_KEYS)
    return {
        "enabled": (rows.get("welcome_email_enabled") or "false").strip().lower() == "true",
        "subject": (rows.get("welcome_email_subject") or "").strip(),
        "body": rows.get("welcome_email_body") or "",
    }


async def save_welcome_email_settings(
    db: AsyncSession,
    *,
    enabled: bool,
    subject: str,
    body: str,
) -> None:
    """Welcome-Mail-Einstellungen schreiben (nur flush, kein Commit)."""
    await set_settings(db, {
        "welcome_email_enabled": "true" if enabled else "false",
        "welcome_email_subject": (subject or "").strip(),
        "welcome_email_body": body or "",
    })


def _test_smtp_connection_sync(smtp_settings: dict):
    """Test SMTP connection without sending an email."""
    host = smtp_settings.get("host", "")
    port = _port(smtp_settings.get("port", 587))
    encryption = smtp_settings.get("encryption", "starttls")

    if not host:
        return {"success": False, "error": "Kein SMTP-Server angegeben."}

    if is_unreadable(smtp_settings.get("password")):
        return {"success": False, "error": SMTP_PASSWORD_UNREADABLE}

    try:
        if encryption == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            if encryption == "starttls":
                server.starttls(context=ssl.create_default_context())
        
        username = smtp_settings.get("username", "")
        password = smtp_settings.get("password", "")
        if username and password:
            server.login(username, password)
        
        server.quit()
        return {"success": True, "message": f"Verbindung zu {host}:{port} erfolgreich!"}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Anmeldung fehlgeschlagen – Benutzername oder Passwort falsch."}
    except Exception as e:
        return {"success": False, "error": f"Verbindung fehlgeschlagen: {str(e)}"}


async def test_smtp_connection(smtp_settings: dict):
    """Async-Wrapper: der blockierende SMTP-Verbindungstest laeuft im Threadpool,
    damit er den Event-Loop (und damit alle anderen Requests) nicht anhaelt."""
    import asyncio
    return await asyncio.to_thread(_test_smtp_connection_sync, smtp_settings)
