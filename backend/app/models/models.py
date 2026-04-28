"""Database models for the PDNS Manager backend.

These models store backend-specific data like audit logs, server configs,
and zone templates. The actual DNS data lives in PowerDNS (accessed via API).
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, JSON, Enum,
    func,
)
from app.core.database import Base


class User(Base):
    """User accounts for authentication."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    role = Column(String(20), default="user", nullable=False)  # admin, user
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    last_login = Column(DateTime, nullable=True)
    # Erweiterte Profilfelder (z. B. geschäftlich)
    phone = Column(String(50), nullable=True)
    company = Column(String(255), nullable=True)
    street = Column(String(255), nullable=True)
    postal_code = Column(String(20), nullable=True)
    city = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    date_of_birth = Column(DateTime, nullable=True)
    preferred_language = Column(String(10), nullable=True)  # de, en, etc.
    # 2FA (TOTP) – secret nur gesetzt wenn aktiviert oder während des Setups
    totp_enabled = Column(Boolean, default=False, nullable=False)
    totp_secret = Column(String(64), nullable=True)  # base32, aktiv
    totp_pending_secret = Column(String(64), nullable=True)  # während /auth/.../totp/begin → enable


class UserZoneAccess(Base):
    """Maps which users can access which zones."""
    __tablename__ = "user_zone_access"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    zone_name = Column(String(255), nullable=False, index=True)  # z.B. "example.de."
    permission = Column(String(20), default="manage")  # manage, read
    created_at = Column(DateTime, default=func.now())


class AuditLog(Base):
    """Logs all changes made through the backend for accountability."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=func.now(), nullable=False)
    action = Column(String(50), nullable=False)  # CREATE, UPDATE, DELETE, DNSSEC_ENABLE, etc.
    resource_type = Column(String(50), nullable=False)  # zone, record, dnssec_key
    resource_name = Column(String(255), nullable=True)  # e.g., "example.com."
    server_name = Column(String(100), nullable=True)  # e.g., "de", "fr"
    details = Column(JSON, nullable=True)  # Additional details as JSON
    status = Column(String(20), default="success")  # success, error
    error_message = Column(Text, nullable=True)
    user_id = Column(Integer, nullable=True)  # Who made the change


class ServerConfig(Base):
    """Stores PowerDNS server configurations in the database."""
    __tablename__ = "server_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)  # e.g., "server1", "server2"
    display_name = Column(String(255), nullable=True)  # e.g., "Nameserver 1"
    url = Column(String(500), nullable=False)  # e.g., "http://87.106.117.76:8089"
    api_key = Column(String(500), nullable=False)  # PowerDNS API Key
    description = Column(Text, nullable=True)  # Optional description
    is_active = Column(Boolean, default=True)
    # True = Zonen/Änderungen auf diesem Server speichern. False = nur lesen (z. B. gleiche DB wie anderer Server).
    allow_writes = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ZoneTemplate(Base):
    """Pre-defined zone templates for quick zone creation."""
    __tablename__ = "zone_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    records = Column(JSON, nullable=False)  # List of record templates
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class SystemSetting(Base):
    """Key-value store for system-wide settings like SMTP configuration."""
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AcmeToken(Base):
    """Scoped API tokens for ACME / DNS-01 automation (z.B. certbot).

    Auth-Modell: Bearer-Token im Authorization-Header. Wir speichern NIE den
    Plaintext-Token - nur einen SHA-256-Hash. Beim Erstellen wird der Plaintext
    EINMAL ans UI zurueckgegeben, danach kann er nicht mehr eingesehen werden
    (gleiches Pattern wie GitHub PATs / Cloudflare API Tokens).

    Scope: ``allowed_zones`` ist eine JSON-Liste mit normalisierten Zone-Namen
    (lowercase, mit Trailing-Dot, z.B. ``["gtgmail.de.", "example.com."]``).
    Der Token darf nur ``_acme-challenge.<sub>.<zone>`` TXT-Records anlegen/loeschen,
    wo ``<zone>`` exakt einer dieser Zonen entspricht. Eine leere Liste wird vom
    Code wie "keine Zonen erlaubt" behandelt - es muss immer explizit gescoped sein.
    """
    __tablename__ = "acme_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)  # menschenlesbarer Name, z.B. "smtp-server"
    token_prefix = Column(String(16), nullable=False, index=True)  # erste ~8 Zeichen, zum Wiedererkennen im UI
    token_hash = Column(String(128), nullable=False, unique=True, index=True)  # SHA-256 hex
    allowed_zones = Column(JSON, nullable=False, default=list)  # ["zone1.", "zone2."] - leer = keine
    created_by_id = Column(Integer, nullable=True)  # User der den Token erstellt hat
    created_at = Column(DateTime, default=func.now(), nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


class PanelToken(Base):
    """Bearer-Token für Voll-API (wie Session-JWT) – z. B. Skripte, Terraform."""

    __tablename__ = "panel_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    token_prefix = Column(String(20), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)


class Webhook(Base):
    """Outbound-Webhook: POST JSON + HMAC-Signatur (X-DNS-Manager-Signature)."""

    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    url = Column(String(1024), nullable=False)
    # Shared secret für HMAC; wird im Klartext gespeichert (nur Eigentümer-Zugriff)
    secret = Column(String(256), nullable=False)
    # z. B. ["*"] oder ["zone", "record"] (Präfix-Match: zone.* trifft zone.imported)
    events = Column(JSON, nullable=False, default=list)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)

