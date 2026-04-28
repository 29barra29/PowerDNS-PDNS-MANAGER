"""Email service for sending emails via SMTP."""
import logging
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def get_smtp_settings(db: AsyncSession) -> dict:
    """Load SMTP settings from the database."""
    from app.models.models import SystemSetting
    
    settings = {}
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(SMTP_KEYS))
    )
    for row in result.scalars().all():
        settings[row.key] = row.value
    
    return {
        "host": settings.get("smtp_host", ""),
        "port": int(settings.get("smtp_port", "587")),
        "username": settings.get("smtp_username", ""),
        "password": settings.get("smtp_password", ""),
        "from_email": settings.get("smtp_from_email", ""),
        "from_name": settings.get("smtp_from_name", "PDNS Manager"),
        "encryption": settings.get("smtp_encryption", "starttls"),
        "enabled": settings.get("smtp_enabled", "false") == "true",
    }


async def save_smtp_settings(db: AsyncSession, settings: dict):
    """Save SMTP settings to the database."""
    from app.models.models import SystemSetting
    
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
    
    for field, db_key in key_map.items():
        value = str(settings.get(field, ""))
        
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == db_key)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            existing.value = value
        else:
            db.add(SystemSetting(key=db_key, value=value))
    
    await db.commit()


def send_email(smtp_settings: dict, to_email: str, subject: str, body_html: str, body_text: str = None):
    """Send an email using the configured SMTP settings. Runs synchronously."""
    if not smtp_settings.get("enabled"):
        raise RuntimeError("SMTP ist nicht aktiviert. Bitte zuerst in den Einstellungen konfigurieren.")
    
    if not smtp_settings.get("host"):
        raise RuntimeError("Kein SMTP-Server konfiguriert.")
    
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{smtp_settings.get('from_name', 'PDNS Manager')} <{smtp_settings['from_email']}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    
    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    
    host = smtp_settings["host"]
    port = int(smtp_settings.get("port", 587))
    encryption = smtp_settings.get("encryption", "starttls")
    
    try:
        if encryption == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            if encryption == "starttls":
                server.starttls()
        
        username = smtp_settings.get("username", "")
        password = smtp_settings.get("password", "")
        if username and password:
            server.login(username, password)
        
        server.send_message(msg)
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
    from app.models.models import SystemSetting

    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(WELCOME_EMAIL_KEYS))
    )
    rows = {row.key: row.value for row in result.scalars().all()}
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
    """Welcome-Mail-Einstellungen schreiben."""
    from app.models.models import SystemSetting

    pairs = {
        "welcome_email_enabled": "true" if enabled else "false",
        "welcome_email_subject": (subject or "").strip(),
        "welcome_email_body": body or "",
    }
    for key, value in pairs.items():
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        existing = result.scalar_one_or_none()
        if existing:
            existing.value = value
        else:
            db.add(SystemSetting(key=key, value=value))
    await db.commit()


async def test_smtp_connection(smtp_settings: dict) -> dict:
    """Test SMTP connection without sending an email."""
    host = smtp_settings.get("host", "")
    port = int(smtp_settings.get("port", 587))
    encryption = smtp_settings.get("encryption", "starttls")
    
    if not host:
        return {"success": False, "error": "Kein SMTP-Server angegeben."}
    
    try:
        if encryption == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=10)
        else:
            server = smtplib.SMTP(host, port, timeout=10)
            if encryption == "starttls":
                server.starttls()
        
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
