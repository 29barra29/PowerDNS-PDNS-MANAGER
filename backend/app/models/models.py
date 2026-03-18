"""Database models for the DNS Manager backend.

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


class UserZoneAccess(Base):
    """Maps which users can access which zones."""
    __tablename__ = "user_zone_access"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    zone_name = Column(String(255), nullable=False, index=True)  # z.B. "mygtg.de."
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
    name = Column(String(100), unique=True, nullable=False)  # e.g., "ns1", "ns3"
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

