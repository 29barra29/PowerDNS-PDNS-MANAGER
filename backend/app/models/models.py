"""Database models for the PDNS Manager backend.

These models store backend-specific data like audit logs, server configs,
and zone templates. The actual DNS data lives in PowerDNS (accessed via API).
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, JSON, Enum,
    Index, UniqueConstraint, event, func, text,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT, VARCHAR as MyVARCHAR
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.request_context import audit_auth_context, get_actor_username, get_client_ip_ctx
from app.core.secrets import (
    SECRET_SETTING_KEYS,
    EncryptedString,
    EncryptedText,
    encrypt_value,
    is_encrypted,
)
from app.core.timeutil import utcnow

# Externe Identitaeten (OIDC "sub", LDAP-GUID) sind case-sensitiv: binaere Kollation auf MariaDB (F10 4.2).
_BIN255 = String(255).with_variant(MyVARCHAR(255, charset="utf8mb4", collation="utf8mb4_bin"), "mysql", "mariadb")
# Webhook-Body: TEXT (64 KiB) reicht fuer Bulk-Ereignisse nicht (F6 4.1).
_MEDIUM_BODY = Text().with_variant(MEDIUMTEXT(), "mysql", "mariadb")


class User(Base):
    """User accounts for authentication."""
    __tablename__ = "users"
    __table_args__ = (
        # NULLs (lokale Konten) kollidieren nicht; verhindert Doppelkonten je externer Identitaet (F10).
        UniqueConstraint("auth_source", "external_issuer", "external_id", name="uq_users_external"),
    )

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
    # base32, verschluesselt gespeichert (enc:v1:, siehe core/secrets.py); VARCHAR(512) fuer den Chiffretext
    totp_secret = Column(EncryptedString(512, label="users.totp_secret"), nullable=True)  # aktiv
    totp_pending_secret = Column(  # während /auth/.../totp/begin → enable
        EncryptedString(512, label="users.totp_pending_secret"), nullable=True
    )
    # WebAuthn/Passkey: stabiler, zufälliger User-Handle (KEINE PII wie Username).
    # Wird beim ersten Passkey-Registrieren gesetzt, base64url-kodiert.
    webauthn_user_handle = Column(String(64), nullable=True)
    # Erzwingt nach dem naechsten Login einen Passwortwechsel (Admin-Reset/-Anlage). Nur lokale Konten (F3).
    must_change_password = Column(Boolean, default=False, nullable=False, server_default=text("0"))
    # Anmeldequelle: local | oidc | ldap. Externe Konten haben ein unbekanntes Zufallspasswort (F10).
    auth_source = Column(String(16), nullable=False, default="local", server_default=text("'local'"))
    # Externe Identitaet: OIDC (iss, sub) bzw. LDAP ("ldap", objectGUID/entryUUID/DN). Binaere Kollation.
    external_issuer = Column(_BIN255, nullable=True)
    external_id = Column(_BIN255, nullable=True)


class UserZoneAccess(Base):
    """Maps which users can access which zones."""
    __tablename__ = "user_zone_access"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    zone_name = Column(String(255), nullable=False, index=True)  # z.B. "example.de."
    permission = Column(String(20), default="manage")  # manage, read
    created_at = Column(DateTime, default=func.now())


class AuditLog(Base):
    """Logs all changes made through the backend for accountability.

    Schreiben nur ueber ``services.audit.write_audit``/``write_audit_detached`` (Bauplan B.7).
    ``details["auth"]`` ist reserviert (F14): der Konstruktor haengt den Token-Kontext der
    Anfrage an. Format der Details ab 3.0: ``{"version": 2, ...}`` (F7 4.3).
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_zone_ts", "zone_name", "timestamp"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_revert_of_id", "revert_of_id"),
    )

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
    # Normalisiert (lower + Trailing-Dot); NUR bei zonenbezogenen Eintraegen (F7 4.1).
    zone_name = Column(String(255), nullable=True)
    # RECORD_ROLLBACK: ID des zurueckgesetzten Eintrags (kein FK – die Aufbewahrung darf das Ziel loeschen).
    revert_of_id = Column(Integer, nullable=True)
    # Akteur zum Zeitpunkt der Aktion (bleibt nach dem Loeschen des Benutzers lesbar) und Client-IP (E-F7-1).
    actor_username = Column(String(100), nullable=True)
    client_ip = Column(String(64), nullable=True)

    def __init__(self, **kwargs):
        # Token-Kontext (F14 5.5) – laeuft im Request-Task, die ContextVars sind dort sichtbar.
        ctx = audit_auth_context()
        if ctx is not None:
            det = kwargs.get("details")
            if det is None:
                kwargs["details"] = {"auth": ctx}
            elif isinstance(det, dict) and "auth" not in det:
                kwargs["details"] = {**det, "auth": ctx}
        # Akteur/IP aus dem Request-Kontext, falls der Aufrufer sie nicht setzt (E-F7-1).
        if "actor_username" not in kwargs:
            name = get_actor_username()
            kwargs["actor_username"] = name[:100] if name else None
        if "client_ip" not in kwargs:
            ip = get_client_ip_ctx()
            kwargs["client_ip"] = ip[:64] if ip else None
        super().__init__(**kwargs)


class ServerConfig(Base):
    """Stores PowerDNS server configurations in the database."""
    __tablename__ = "server_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)  # e.g., "server1", "server2"
    display_name = Column(String(255), nullable=True)  # e.g., "Nameserver 1"
    url = Column(String(500), nullable=False)  # e.g., "http://87.106.117.76:8089"
    # PowerDNS API Key – verschluesselt (enc:v1:), siehe core/secrets.py
    api_key = Column(EncryptedText(label="server_configs.api_key"), nullable=False)
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


@event.listens_for(Session, "before_flush")
def _encrypt_secret_settings(session, flush_context, instances):
    """Verschluesselt Secret-Settings auch, wenn jemand SystemSetting(...) direkt schreibt (F5 5.7).

    Folge: nach dem Flush steht im ORM-Objekt der Chiffretext – Leser gehen ueber
    ``services.system_settings.get_settings`` (entschluesselt immer).
    """
    for obj in list(session.new) + list(session.dirty):
        if (
            isinstance(obj, SystemSetting)
            and obj.key in SECRET_SETTING_KEYS
            and obj.value
            and not is_encrypted(obj.value)
        ):
            obj.value = encrypt_value(obj.value)


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
    """Bearer-Token für die Panel-API, optional auf Zonen/Leserecht/Ablauf beschränkt (F14)."""

    __tablename__ = "panel_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    token_prefix = Column(String(20), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    last_used_ip = Column(String(64), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)  # False = pausiert (oder widerrufen, s. revoked_at)
    # ["a.de.", ...] normalisiert/sortiert; NULL = alle Zonen des Besitzers; [] = keine Zone
    scope_zones = Column(JSON, nullable=True)
    # manage | read (unbekannter Wert gilt als read)
    permission = Column(String(10), nullable=False, default="manage", server_default="manage")
    expires_at = Column(DateTime, nullable=True)  # naive UTC; NULL = kein Ablauf
    # Admin-Endpunkte (get_admin_user) erlaubt
    allow_admin = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    revoked_at = Column(DateTime, nullable=True)  # gesetzt = endgueltig widerrufen


class Webhook(Base):
    """Outbound-Webhook: POST JSON + HMAC-Signatur (X-DNS-Manager-Signature)."""

    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    # Ziel-URL – verschluesselt (enc:v1:), siehe core/secrets.py: bei Slack/Teams/Discord ist die URL
    # selbst das Geheimnis [S10]. SSRF-Pruefung und Pinning arbeiten mit dem entschluesselten Wert.
    url = Column(EncryptedText(label="webhooks.url"), nullable=False)
    # Shared secret für HMAC – verschluesselt (enc:v1:), siehe core/secrets.py
    secret = Column(EncryptedText(label="webhooks.secret"), nullable=False)
    # z. B. ["*"] oder ["zone", "record"] (Präfix-Match: zone.* trifft zone.imported)
    events = Column(JSON, nullable=False, default=list)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    # own = nur eigene Aenderungen | zones = alle Aenderungen in Zonen mit Leserecht (F6)
    scope = Column(String(16), nullable=False, default="own", server_default="own")
    updated_at = Column(DateTime, nullable=True, onupdate=utcnow)
    last_success_at = Column(DateTime, nullable=True)
    last_failure_at = Column(DateTime, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0, server_default="0")


class WebhookDelivery(Base):
    """Outbox-Zeile je Ereignis und Empfaenger (F6 4.1). Zeitstempel naive UTC, Python-seitig."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_wd_due", "status", "next_attempt_at"),
        Index("ix_wd_webhook_created", "webhook_id", "created_at"),
        Index("ix_wd_user_status", "user_id", "status"),
        Index("ix_wd_created", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(String(36), nullable=False, unique=True)  # UUID4, Header X-DNS-Manager-Delivery
    event_id = Column(String(36), nullable=False)  # UUID4, gleich fuer alle Empfaenger eines Ereignisses
    webhook_id = Column(Integer, nullable=False)  # kein FK (Projektkonvention), manuell mitloeschen
    user_id = Column(Integer, nullable=False)  # Webhook-Besitzer
    event = Column(String(64), nullable=False)  # Katalogname (webhook_events.EVENT_CATALOG)
    zone_name = Column(String(255), nullable=True)  # normalisiert (lower + Trailing-Dot)
    audit_log_id = Column(Integer, nullable=True)
    body = Column(_MEDIUM_BODY, nullable=False)  # exakt die signierten Bytes (ASCII-JSON)
    signature = Column(String(80), nullable=False)  # "sha256=" + 64 hex
    # queued | in_progress | succeeded | failed | dead | cancelled
    status = Column(String(16), nullable=False, default="queued", server_default="queued")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    max_attempts = Column(Integer, nullable=False, default=6, server_default="6")
    next_attempt_at = Column(DateTime, nullable=False, default=utcnow)
    last_attempt_at = Column(DateTime, nullable=True)
    last_status_code = Column(Integer, nullable=True)
    last_error_code = Column(String(32), nullable=True)  # Maschinencode, UI uebersetzt
    last_error = Column(String(512), nullable=True)  # deutscher Text, ohne URL/Userinfo
    last_response_excerpt = Column(String(1024), nullable=True)
    last_duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    delivered_at = Column(DateTime, nullable=True)


class DynDnsToken(Base):
    """DynDNS-Token: darf nur A/AAAA der gelisteten Hostnamen setzen (Rechte des Besitzers zum Update-Zeitpunkt).

    Gespeichert wird nur der SHA-256-Hash (wie Panel-/ACME-Tokens). Zeitstempel naive UTC, Python-seitig.
    """

    __tablename__ = "dyndns_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)  # Besitzer, kein FK (Konvention)
    name = Column(String(100), nullable=False)
    token_prefix = Column(String(20), nullable=False, index=True)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)  # SHA-256 hex
    hostnames = Column(JSON, nullable=False, default=list)  # ["home.example.com."] normalisiert
    allowed_types = Column(JSON, nullable=False, default=lambda: ["A", "AAAA"])
    ttl = Column(Integer, nullable=False, default=60, server_default="60")
    update_ptr = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=utcnow)
    last_used_at = Column(DateTime, nullable=True)  # jeder authentifizierte Request
    last_used_ip = Column(String(64), nullable=True)
    last_ip_v4 = Column(String(15), nullable=True)  # zuletzt gesetzter/bestaetigter Wert
    last_ip_v6 = Column(String(45), nullable=True)
    last_result = Column(String(32), nullable=True)  # good|nochg|nohost|badip|dnserr|911|abuse
    last_changed_at = Column(DateTime, nullable=True)  # letzter echter Write (good)
    # Server, die beim letzten Write fehlten – persistenter Ersatz fuer _peer_dirty [D5]
    stale_servers = Column(JSON, nullable=True)


class WebAuthnCredential(Base):
    """Registrierter Passkey / FIDO2-Credential für die passwortlose Anmeldung.

    Wir speichern ausschließlich den ÖFFENTLICHEN Schlüssel (COSE, base64url) plus
    die Credential-ID. Der private Schlüssel verlässt nie das Gerät des Nutzers
    (Phone/Laptop/Security-Key). ``sign_count`` dient der Klon-Erkennung gemäß
    WebAuthn-Spec; viele Plattform-Authenticatoren liefern allerdings konstant 0.
    """

    __tablename__ = "webauthn_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    # Menschenlesbares Label zur Wiedererkennung (z. B. "iPhone", "YubiKey").
    name = Column(String(100), nullable=False)
    # Credential-ID (base64url) – eindeutig pro Authenticator/Relying-Party.
    credential_id = Column(String(512), nullable=False, unique=True, index=True)
    # Öffentlicher COSE-Schlüssel, base64url-kodiert.
    public_key = Column(Text, nullable=False)
    sign_count = Column(Integer, default=0, nullable=False)
    # Transporthinweise vom Browser (["internal"], ["hybrid"], ["usb"], …) – optional.
    transports = Column(JSON, nullable=True)
    # Authenticator-Modell (AAGUID, optional, nur informativ).
    aaguid = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    last_used_at = Column(DateTime, nullable=True)

