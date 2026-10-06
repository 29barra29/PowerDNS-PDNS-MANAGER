"""Tests fuer services/sso_settings.py und schemas/sso.py (F10 3.3.2, 5.3; Plan S1, S3, S5, S8, S16).

Speicherung ueber SQLite hinter einem AsyncSession-Adapter (aiosqlite fehlt im Testimage); Secrets laufen durch
den echten F5-Kern (``configure_for_tests``).
"""
from __future__ import annotations

import asyncio
import json
import os

os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.core import secrets as secret_store  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.secret_mask import SECRET_MASK, SecretReentryRequired  # noqa: E402
from app.models.models import SystemSetting, User  # noqa: E402
from app.schemas.sso import (  # noqa: E402
    LdapSettingsIn,
    OidcSettingsIn,
    SsoSettingsUpdate,
    SsoTestRequest,
)
from app.services import sso_settings as ss  # noqa: E402
from app.services.sso_settings import SsoSettingsError  # noqa: E402


class _AsyncNested:
    def __init__(self, session):
        self._s, self._tx = session, None

    async def __aenter__(self):
        self._tx = self._s.begin_nested()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        (self._tx.commit if exc_type is None else self._tx.rollback)()
        return False


class AsyncSessionAdapter:
    def __init__(self, session: Session):
        self.sync_session = session

    async def execute(self, *a, **k):
        return self.sync_session.execute(*a, **k)

    async def scalar(self, *a, **k):
        return self.sync_session.scalar(*a, **k)

    def add(self, obj):
        self.sync_session.add(obj)

    async def flush(self):
        self.sync_session.flush()

    def begin_nested(self):
        return _AsyncNested(self.sync_session)


@pytest.fixture
def db():
    secret_store.configure_for_tests()
    eng = create_engine("sqlite://")

    @event.listens_for(eng, "connect")
    def _connect(dbapi_conn, _rec):
        dbapi_conn.isolation_level = None

    @event.listens_for(eng, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    SystemSetting.__table__.create(eng)
    User.__table__.create(eng)
    with Session(eng) as s:
        yield AsyncSessionAdapter(s)
    eng.dispose()


@pytest.fixture
def insecure(monkeypatch):
    monkeypatch.setattr(settings, "SSO_ALLOW_INSECURE", True)


def run(coro):
    return asyncio.run(coro)


def raw(db, key):
    return db.sync_session.execute(select(SystemSetting.value).where(SystemSetting.key == key)).scalar_one_or_none()


def put(db, **values):
    for k, v in values.items():
        db.add(SystemSetting(key=k, value=v))
    db.sync_session.flush()


def upd(db, **sections):
    return run(ss.update(db, SsoSettingsUpdate.model_validate(sections)))


def load(db):
    return run(ss.load_sso_config(db))


BASE = {"app_base_url": "https://dns.example.com/"}
OIDC_OK = {"enabled": True, "issuer": "https://idp.example.com/realms/x", "client_id": "pdns",
           "client_secret": "s3cret-value"}
LDAP_OK = {"enabled": True, "server_urls": ["ldaps://dc1.example.com"], "user_base_dn": "DC=example,DC=com",
           "bind_dn": "CN=svc,DC=example,DC=com", "bind_password": "bind-pw-123"}


def _validation_text(exc: ValidationError) -> str:
    return " | ".join(str(e.get("msg")) for e in exc.errors())


# ---------------------------------------------------------------------------------------------
# Schema-Validierung (422)
# ---------------------------------------------------------------------------------------------
def test_issuer_validation(insecure, monkeypatch):
    assert OidcSettingsIn(issuer=" https://idp.example/realms/a ").issuer == "https://idp.example/realms/a"
    assert OidcSettingsIn(issuer="").issuer == ""
    assert OidcSettingsIn(issuer="http://idp.local:8080/x").issuer == "http://idp.local:8080/x"   # insecure an
    monkeypatch.setattr(settings, "SSO_ALLOW_INSECURE", False)
    with pytest.raises(ValidationError) as ei:
        OidcSettingsIn(issuer="http://idp.example")
    assert "Die Issuer-URL muss mit https:// beginnen" in _validation_text(ei.value)
    for bad in ("https://idp.example/?a=b", "https://idp.example/#x", "https://user@idp.example", "ftp://x",
                "https://", "https://idp example"):
        with pytest.raises(ValidationError) as ei:
            OidcSettingsIn(issuer=bad)
        assert "Ungültige Issuer-URL" in _validation_text(ei.value)


def test_scopes_validation():
    assert OidcSettingsIn(scopes="profile email").scopes == "openid profile email"
    assert OidcSettingsIn(scopes="openid  groups openid").scopes == "openid groups"
    with pytest.raises(ValidationError) as ei:
        OidcSettingsIn(scopes="openid b$d")
    assert "Ungültiger Scope: b$d" in _validation_text(ei.value)
    with pytest.raises(ValidationError):
        OidcSettingsIn(scopes=" ".join(f"s{i}" for i in range(21)))


def test_claim_validation():
    assert OidcSettingsIn(groups_claim="realm_access.roles").groups_claim == "realm_access.roles"
    assert OidcSettingsIn(email_claim="").email_claim == ""
    with pytest.raises(ValidationError) as ei:
        OidcSettingsIn(username_claim="")
    assert "Ungültiger Claim-Name" in _validation_text(ei.value)
    with pytest.raises(ValidationError) as ei:
        OidcSettingsIn(name_claim="bad claim")
    assert "Ungültiger Claim-Name: bad claim" in _validation_text(ei.value)


def test_group_lists_normalized():
    m = OidcSettingsIn(allowed_groups=[" a ", "", "A", "b", "  "])
    assert m.allowed_groups == ["a", "b"]
    with pytest.raises(ValidationError):
        OidcSettingsIn(admin_groups=["x" * 256])
    with pytest.raises(ValidationError):
        OidcSettingsIn(admin_groups=[f"g{i}" for i in range(51)])


def test_ldap_groups_must_be_full_dns():
    m = LdapSettingsIn(allowed_groups=["CN=pdns-admins,OU=Groups,DC=x", "cn=PDNS-admins, ou=groups, dc=X"])
    assert m.allowed_groups == ["CN=pdns-admins,OU=Groups,DC=x"]     # gleiche DN -> dedupliziert
    for bad in ("pdns-admins", "CN=pdns-admins", "CN=,DC=x"):
        with pytest.raises(ValidationError) as ei:
            LdapSettingsIn(admin_groups=[bad])
        assert "Ungültiger Gruppen-DN" in _validation_text(ei.value)


def test_ldap_url_attr_filter_validation():
    m = LdapSettingsIn(server_urls=["LDAPS://dc1.example.com:636/", "", "ldaps://DC1.example.com:636"])
    assert m.server_urls == ["ldaps://dc1.example.com:636"]
    for bad in ("http://dc1", "ldaps://dc1:99999", "ldaps://dc 1", "ldaps://dc1/ou=x"):
        with pytest.raises(ValidationError) as ei:
            LdapSettingsIn(server_urls=[bad])
        assert "Ungültige LDAP-Server-URL" in _validation_text(ei.value)
    assert LdapSettingsIn(unique_id_attr="").unique_id_attr == ""
    with pytest.raises(ValidationError):
        LdapSettingsIn(username_attr="")
    with pytest.raises(ValidationError) as ei:
        LdapSettingsIn(email_attr="1mail")
    assert "Ungültiger Attributname" in _validation_text(ei.value)
    for bad in ("(uid=x)", "uid={username}", "(&(uid={username})"):
        with pytest.raises(ValidationError) as ei:
            LdapSettingsIn(user_filter=bad)
        assert "Benutzerfilter" in _validation_text(ei.value)
    assert LdapSettingsIn(group_filter="(&(objectClass=posixGroup)(memberUid={username}))").group_filter
    with pytest.raises(ValidationError):
        LdapSettingsIn(group_filter="(objectClass=group)")
    with pytest.raises(ValidationError):
        LdapSettingsIn(timeout=1)
    with pytest.raises(ValidationError):
        LdapSettingsIn(user_base_dn="keine dn")
    # Bind-Benutzer darf UPN sein (AD)
    assert LdapSettingsIn(bind_dn="svc@corp.example").bind_dn == "svc@corp.example"


def _pem_cert() -> str:
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test-CA")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_ca_cert_validation():
    pem = _pem_cert()
    assert LdapSettingsIn(ca_cert=pem).ca_cert == pem.strip()
    assert LdapSettingsIn(ca_cert="  ").ca_cert == ""
    with pytest.raises(ValidationError) as ei:
        LdapSettingsIn(ca_cert="nur text")
    assert "PEM-Format" in _validation_text(ei.value)
    with pytest.raises(ValidationError) as ei:
        LdapSettingsIn(ca_cert="-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----")
    assert "konnte nicht gelesen werden" in _validation_text(ei.value)


def test_email_domains_and_display_name():
    m = OidcSettingsIn(allowed_email_domains=["@Example.COM", "example.com.", "bücher.de", ""])
    assert m.allowed_email_domains == ["example.com", "xn--bcher-kva.de"]
    for bad in ("localhost", "1.2.3.4", "-a.com", "a..b"):
        with pytest.raises(ValidationError):
            OidcSettingsIn(allowed_email_domains=[bad])
    with pytest.raises(ValidationError):
        OidcSettingsIn(display_name="   ")
    assert OidcSettingsIn(display_name=" Firmen-Login ").display_name == "Firmen-Login"


# ---------------------------------------------------------------------------------------------
# Laden, Defaults, Ausgabe
# ---------------------------------------------------------------------------------------------
def test_defaults(db):
    cfg = load(db)
    assert cfg.oidc.jit_enabled is False and cfg.ldap.jit_enabled is False
    assert cfg.oidc.jit_allow_any_account is False and cfg.ldap.unique_id_attr_confirmed is False
    assert cfg.ldap.user_filter == "(&(objectCategory=person)(objectClass=user)(sAMAccountName={username}))"
    assert cfg.ldap.unique_id_attr == "objectGUID" and cfg.oidc.allowed_email_domains == ()
    assert cfg.general.local_login_enabled is True and cfg.general.require_totp is True
    assert cfg.base_url == "" and ss.redirect_uri(cfg) is None
    assert not ss.oidc_ready(cfg) and not ss.ldap_ready(cfg)


def test_base_url_and_fallback(db, monkeypatch):
    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", "https://origin.example/, https://b.example")
    assert load(db).base_url == "https://origin.example"
    put(db, **BASE)
    cfg = load(db)
    assert cfg.base_url == "https://dns.example.com"
    assert ss.redirect_uri(cfg) == "https://dns.example.com/api/v1/auth/oidc/callback"
    assert ss.emergency_login_url(cfg) == "https://dns.example.com/login?local=1"


def test_broken_values_fall_back(db):
    put(db, oidc_allowed_groups="{kein json", oidc_token_auth_method="private_key_jwt", ldap_timeout="999",
        ldap_security="plain", oidc_enabled="TRUE")
    cfg = load(db)
    assert cfg.oidc.allowed_groups == ()
    assert cfg.oidc.token_auth_method == "client_secret_basic"
    assert cfg.ldap.timeout == 30 and cfg.ldap.security == "ldaps"
    assert cfg.oidc.enabled is True


def test_settings_out_masks_secrets_and_stores_encrypted(db):
    put(db, **BASE)
    upd(db, oidc=OIDC_OK)
    stored = raw(db, "oidc_client_secret")
    assert stored.startswith("enc:v1:") and "s3cret-value" not in stored
    out = ss.settings_out(load(db), linked={"oidc": 3})
    assert out["oidc"]["client_secret"] == SECRET_MASK and out["oidc"]["client_secret_set"] is True
    assert out["oidc"]["linked_accounts"] == 3 and out["ldap"]["bind_password"] == ""
    assert out["ldap"]["unique_id_attr_safe"] is True
    assert "s3cret-value" not in json.dumps(out)
    assert out["general"]["redirect_uri"] == "https://dns.example.com/api/v1/auth/oidc/callback"


def test_unreadable_secret_counts_as_not_set(db):
    secret_store.configure_for_tests()
    foreign = secret_store.SecretBox(secret_store.generate_key()).encrypt("alt")
    put(db, oidc_client_secret=foreign)
    cfg = load(db)
    assert cfg.oidc.client_secret == "" and cfg.oidc.client_secret_unreadable is True
    out = ss.settings_out(cfg)
    assert out["oidc"]["client_secret_set"] is False and out["oidc"]["client_secret_unreadable"] is True
    # Behalten (Maske) schreibt nichts, Ziel aendern verlangt keine Neueingabe (nichts zu schuetzen)
    upd(db, oidc={"issuer": "https://other.example", "client_secret": SECRET_MASK})
    assert raw(db, "oidc_client_secret") == foreign


# ---------------------------------------------------------------------------------------------
# Speichern: Secrets, Audit, Caches
# ---------------------------------------------------------------------------------------------
def test_secret_keep_clear_set(db):
    put(db, **BASE)
    upd(db, oidc=OIDC_OK)
    first = raw(db, "oidc_client_secret")
    r = upd(db, oidc={"client_secret": None, "display_name": "Firma"})
    assert raw(db, "oidc_client_secret") == first and "oidc_client_secret" not in r.changed
    upd(db, oidc={"client_secret": SECRET_MASK})
    assert raw(db, "oidc_client_secret") == first
    r = upd(db, oidc={"client_secret": "neu-geheim"})
    assert r.changed["oidc_client_secret"] == "changed"
    assert load(db).oidc.client_secret == "neu-geheim"
    r = upd(db, oidc={"enabled": False, "client_secret": ""})
    assert r.changed["oidc_client_secret"] == "removed" and load(db).oidc.client_secret == ""
    assert "neu-geheim" not in json.dumps(r.audit_details())


def test_audit_details_format(db):
    put(db, **BASE)
    r = upd(db, oidc={**OIDC_OK, "allowed_groups": ["pdns"], "jit_enabled": True},
            ldap={"ca_cert": _pem_cert()})
    d = r.audit_details()
    assert d["sections"] == ["oidc", "ldap"]
    assert d["changed"]["oidc_issuer"] == {"from": "", "to": OIDC_OK["issuer"]}
    assert d["changed"]["oidc_allowed_groups"] == {"from": [], "to": ["pdns"]}
    assert d["changed"]["oidc_client_secret"] == "changed"
    assert d["changed"]["ldap_ca_cert"] == "changed"
    assert "s3cret-value" not in json.dumps(d)
    assert "jit_allow_any_account" not in d


def test_clear_caches_on_issuer_change(db, monkeypatch):
    from app.services import sso_oidc

    calls = []
    monkeypatch.setattr(sso_oidc, "clear_caches", lambda: calls.append(1))
    put(db, **BASE)
    upd(db, oidc={"display_name": "X"})
    assert calls == []
    upd(db, oidc=OIDC_OK)
    assert calls == [1]
    upd(db, oidc={"client_id": "pdns2", "client_secret": "s3cret-value"})
    assert calls == [1, 1]


def test_partial_update_keeps_other_fields(db):
    put(db, **BASE)
    upd(db, oidc={**OIDC_OK, "groups_claim": "roles"})
    upd(db, oidc={"display_name": "Login"})
    cfg = load(db)
    assert cfg.oidc.groups_claim == "roles" and cfg.oidc.issuer == OIDC_OK["issuer"]
    assert raw(db, "oidc_enabled") == "true" and raw(db, "oidc_display_name") == "Login"


# ---------------------------------------------------------------------------------------------
# Konsistenz (400, exakte Texte)
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("prep,sections,text", [
    ({}, {"oidc": {"enabled": True, "client_id": "x", "client_secret": "y"}},
     "Für OIDC müssen Issuer-URL und Client-ID gesetzt sein"),
    ({}, {"oidc": OIDC_OK},
     "Für OIDC muss zuerst die öffentliche Basis-URL gesetzt werden (Einstellungen → Profil)"),
    (BASE, {"oidc": {**OIDC_OK, "client_secret": ""}},
     "Für die gewählte Client-Authentifizierung fehlt das Client-Secret"),
    ({}, {"ldap": {"enabled": True, "server_urls": ["ldaps://dc1"]}},
     "Für LDAP müssen Server und Suchbasis gesetzt sein"),
    ({}, {"ldap": {**LDAP_OK, "server_urls": ["ldap://dc1"]}},
     "Verschlüsselung und Server-URL passen nicht zusammen (LDAPS braucht ldaps://, StartTLS ldap://)"),
    ({}, {"ldap": {**LDAP_OK, "security": "starttls"}},
     "Verschlüsselung und Server-URL passen nicht zusammen (LDAPS braucht ldaps://, StartTLS ldap://)"),
    ({}, {"ldap": {**LDAP_OK, "tls_verify": False}},
     "Unverschlüsselte LDAP-Verbindungen bzw. das Abschalten der Zertifikatsprüfung erfordern SSO_ALLOW_INSECURE=true"),
    ({}, {"ldap": {**LDAP_OK, "server_urls": ["ldap://dc1"], "security": "none"}},
     "Unverschlüsselte LDAP-Verbindungen bzw. das Abschalten der Zertifikatsprüfung erfordern SSO_ALLOW_INSECURE=true"),
    ({}, {"ldap": {"group_mode": "search"}}, "Für die Gruppensuche wird eine Suchbasis benötigt"),
    ({}, {"oidc": {"role_mode": "promote"}},
     "Für die Rollen-Zuordnung muss mindestens eine Admin-Gruppe eingetragen sein"),
    ({}, {"oidc": {"role_mode": "sync", "admin_groups": ["a"], "groups_claim": ""}},
     "Für die Rollen-Zuordnung müssen Gruppen ermittelt werden (Gruppen-Claim bzw. Gruppenmodus)"),
    ({}, {"ldap": {"role_mode": "sync", "admin_groups": ["CN=a,DC=x"], "group_mode": "none"}},
     "Für die Rollen-Zuordnung müssen Gruppen ermittelt werden (Gruppen-Claim bzw. Gruppenmodus)"),
    ({}, {"ldap": {"allowed_groups": ["CN=a,DC=x"], "group_mode": "none"}},
     "Für erlaubte Gruppen müssen Gruppen ermittelt werden (Gruppen-Claim bzw. Gruppenmodus)"),
    ({}, {"general": {"local_login_enabled": False}},
     "Die lokale Anmeldung kann nur abgeschaltet werden, wenn OIDC oder LDAP aktiv ist"),
])
def test_consistency_rules(db, prep, sections, text):
    if prep:
        put(db, **prep)
    with pytest.raises(SsoSettingsError) as ei:
        upd(db, **sections)
    assert ei.value.status_code == 400 and ei.value.detail == text
    assert raw(db, "oidc_enabled") is None and raw(db, "ldap_enabled") is None   # nichts gespeichert


def test_valid_full_configuration_saves(db, insecure):
    put(db, **BASE)
    r = upd(db, general={"local_login_enabled": False}, oidc=OIDC_OK,
            ldap={**LDAP_OK, "server_urls": ["ldap://dc1"], "security": "starttls", "tls_verify": False})
    assert r.config.general.local_login_enabled is False
    assert ss.oidc_ready(r.config) and ss.ldap_ready(r.config)


# ---------------------------------------------------------------------------------------------
# S5: JIT-Freigabe, S16: ID-Attribut (422)
# ---------------------------------------------------------------------------------------------
def test_jit_without_groups_needs_confirmation(db):
    with pytest.raises(SsoSettingsError) as ei:
        upd(db, oidc={"jit_enabled": True})
    assert ei.value.status_code == 422 and "jit_allow_any_account" in ei.value.detail
    with pytest.raises(SsoSettingsError) as ei:
        upd(db, ldap={"jit_enabled": True})
    assert ei.value.status_code == 422
    # LDAP: E-Mail-Domains gibt es nicht -> Gruppen oder Freigabe
    r = upd(db, oidc={"jit_enabled": True, "allowed_email_domains": ["example.com"]})
    assert any("email_verified" in w for w in r.warnings)
    r = upd(db, ldap={"jit_enabled": True, "jit_allow_any_account": True})
    d = r.audit_details()
    assert d["jit_allow_any_account"] is True and d["jit_allow_any_account_sources"] == ["ldap"]
    assert any("jedes Konto des Verzeichnisses" in w for w in r.warnings)
    upd(db, oidc={"jit_enabled": True, "allowed_groups": ["pdns"], "allowed_email_domains": []})
    # Gruppen entfernen, ohne Freigabe -> wieder 422
    with pytest.raises(SsoSettingsError):
        upd(db, oidc={"allowed_groups": []})


def test_unique_id_attr_requires_confirmation(db):
    with pytest.raises(SsoSettingsError) as ei:
        upd(db, ldap={"unique_id_attr": "sAMAccountName"})
    assert ei.value.status_code == 422 and "sAMAccountName" in ei.value.detail
    r = upd(db, ldap={"unique_id_attr": "sAMAccountName", "unique_id_attr_confirmed": True})
    assert r.audit_details()["unique_id_attr_confirmed"] is True
    assert any("sAMAccountName ist änderbar" in w for w in r.warnings)
    assert raw(db, "ldap_unique_id_attr_confirmed") == "true"
    upd(db, ldap={"unique_id_attr": "sAMAccountName", "display_name": "AD"})   # gleiches Attribut: bleibt bestaetigt
    with pytest.raises(SsoSettingsError):                                       # anderes Attribut: neu bestaetigen
        upd(db, ldap={"unique_id_attr": "uid"})
    with pytest.raises(SsoSettingsError) as ei:
        upd(db, ldap={"unique_id_attr": ""})
    assert "DN" in ei.value.detail
    r = upd(db, ldap={"unique_id_attr": "entryuuid"})                           # Allowlist, case-insensitiv
    assert raw(db, "ldap_unique_id_attr_confirmed") == "false"
    assert r.audit_details()["unique_id_attr_confirmed"] is False
    for safe in ("objectGUID", "entryUUID", "nsUniqueId", "ipaUniqueID"):
        upd(db, ldap={"unique_id_attr": safe})


# ---------------------------------------------------------------------------------------------
# S3: Retarget-Schutz
# ---------------------------------------------------------------------------------------------
def test_ldap_retarget_requires_password(db):
    upd(db, ldap=LDAP_OK)
    for secret in (None, SECRET_MASK):
        with pytest.raises(SecretReentryRequired) as ei:
            upd(db, ldap={"server_urls": ["ldaps://evil.example"], "bind_password": secret})
        assert ei.value.changed_fields == ("server_urls",)
    with pytest.raises(SecretReentryRequired):
        upd(db, ldap={"bind_dn": "CN=other,DC=example,DC=com"})
    with pytest.raises(SecretReentryRequired):
        upd(db, ldap={"ca_cert": _pem_cert()})
    assert load(db).ldap.server_urls == ("ldaps://dc1.example.com",)
    # gleiche Ziele in anderer Reihenfolge/Schreibweise: kein Retarget
    upd(db, ldap={"server_urls": ["LDAPS://DC1.example.com"], "bind_password": SECRET_MASK, "timeout": 5})
    upd(db, ldap={"server_urls": ["ldaps://dc2.example.com"], "bind_password": "neues-pw"})
    assert load(db).ldap.bind_password == "neues-pw"
    upd(db, ldap={"enabled": False, "server_urls": ["ldaps://dc3.example.com"], "bind_password": ""})
    assert load(db).ldap.bind_password == ""


def test_oidc_retarget_requires_secret(db):
    put(db, **BASE)
    upd(db, oidc=OIDC_OK)
    for change in ({"issuer": "https://evil.example"}, {"client_id": "other"},
                   {"token_auth_method": "client_secret_post"}):
        with pytest.raises(SecretReentryRequired):
            upd(db, oidc={**change, "client_secret": SECRET_MASK})
    upd(db, oidc={"issuer": OIDC_OK["issuer"].upper().replace("/REALMS/X", "/realms/x"),
                  "client_secret": SECRET_MASK})                   # Host-Schreibweise egal: kein Retarget
    upd(db, oidc={"issuer": "https://new.example", "client_secret": "new-secret"})
    assert load(db).oidc.issuer == "https://new.example"


def test_build_test_config_uses_stored_secret_only_for_same_target(db):
    put(db, **BASE)
    upd(db, oidc=OIDC_OK, ldap=LDAP_OK)
    cfg = run(ss.build_test_config(db, SsoTestRequest(target="ldap", ldap=LdapSettingsIn(timeout=3))))
    assert cfg.ldap.bind_password == "bind-pw-123" and cfg.ldap.timeout == 3
    assert load(db).ldap.timeout == 8                                     # nichts gespeichert
    with pytest.raises(SecretReentryRequired):
        run(ss.build_test_config(db, SsoTestRequest(
            target="ldap", ldap=LdapSettingsIn(server_urls=["ldaps://evil.example"]))))
    cfg = run(ss.build_test_config(db, SsoTestRequest(
        target="ldap", ldap=LdapSettingsIn(server_urls=["ldaps://evil.example"], bind_password="eingegeben"))))
    assert cfg.ldap.bind_password == "eingegeben"
    with pytest.raises(SecretReentryRequired):
        run(ss.build_test_config(db, SsoTestRequest(
            target="oidc", oidc=OidcSettingsIn(issuer="https://evil.example", client_secret=SECRET_MASK))))
    cfg = run(ss.build_test_config(db, SsoTestRequest(target="oidc")))
    assert cfg.oidc.client_secret == "s3cret-value"


# ---------------------------------------------------------------------------------------------
# S8: changed_sensitive, Warnungen
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("sections,sensitive", [
    ({"oidc": {"display_name": "X"}}, False),
    ({"oidc": {"scopes": "openid profile"}}, False),
    ({"ldap": {"timeout": 5}}, False),
    ({"general": {"require_totp": False}}, True),   # L-2 (WS-W3-NACHARBEIT): 2FA-Pflicht nur mit Step-up
    ({"oidc": {"admin_groups": ["a"]}}, True),
    ({"oidc": {"allowed_groups": ["a"]}}, True),
    ({"oidc": {"groups_claim": "roles"}}, True),
    ({"ldap": {"allowed_groups": ["CN=a,DC=x"]}}, True),
    ({"ldap": {"unique_id_attr": "entryUUID"}}, True),
    ({"oidc": {"jit_enabled": True, "jit_allow_any_account": True}}, True),
    ({"oidc": {"allowed_email_domains": ["example.com"]}}, True),
])
def test_changed_sensitive(db, sections, sensitive):
    assert upd(db, **sections).changed_sensitive is sensitive


def test_changed_sensitive_targets_and_local_login(db):
    put(db, **BASE)
    assert upd(db, oidc=OIDC_OK).changed_sensitive is True
    assert upd(db, ldap=LDAP_OK).changed_sensitive is True
    assert upd(db, general={"local_login_enabled": False}).changed_sensitive is True
    assert upd(db, oidc={"client_secret": "rotated"}).changed_sensitive is False   # nur Secret
    assert upd(db, oidc={"display_name": "SSO"}).changed_sensitive is False       # unveraendert


def test_dry_run_checks_but_writes_nothing(db, monkeypatch):
    from app.services import sso_oidc

    calls = []
    monkeypatch.setattr(sso_oidc, "clear_caches", lambda: calls.append(1))
    put(db, **BASE)
    data = SsoSettingsUpdate.model_validate({"oidc": OIDC_OK})
    preview = run(ss.update(db, data, dry_run=True))
    assert preview.changed_sensitive is True and preview.changed["oidc_issuer"]["to"] == OIDC_OK["issuer"]
    assert raw(db, "oidc_issuer") is None and raw(db, "oidc_client_secret") is None and calls == []
    with pytest.raises(SsoSettingsError):
        run(ss.update(db, SsoSettingsUpdate.model_validate({"oidc": {"jit_enabled": True}}), dry_run=True))
    run(ss.update(db, data))
    assert raw(db, "oidc_issuer") == OIDC_OK["issuer"] and calls == [1]


def test_warnings_for_linked_accounts(db):
    put(db, **BASE)
    upd(db, oidc=OIDC_OK, ldap=LDAP_OK)
    for i, src in enumerate(("oidc", "oidc", "ldap")):
        db.add(User(username=f"e{i}", hashed_password="x", auth_source=src, external_issuer="i", external_id=str(i)))
    db.sync_session.flush()
    assert run(ss.count_linked_accounts(db)) == {"oidc": 2, "ldap": 1}
    r = upd(db, oidc={"issuer": "https://new.example", "client_secret": "neu"})
    assert ("2 bestehende OIDC-Konten sind an den bisherigen Issuer gebunden und werden nicht übernommen – diese "
            "Personen erhalten bei der nächsten Anmeldung neue Konten.") in r.warnings
    r = upd(db, ldap={"unique_id_attr": "entryUUID"})
    assert ("1 bestehende LDAP-Konten sind über das bisherige ID-Attribut verknüpft und werden nicht "
            "übernommen.") in r.warnings


# ---------------------------------------------------------------------------------------------
# Oeffentliche Sicht und Policy
# ---------------------------------------------------------------------------------------------
def test_public_providers(db):
    put(db, **BASE)
    out = ss.public_providers(load(db))
    assert out == {"local_login_enabled": True, "providers": [], "ldap": {"enabled": False, "label": None},
                   "linking": {"oidc": False, "ldap": False}}
    upd(db, oidc={**OIDC_OK, "display_name": "Firmen-Login"}, ldap={**LDAP_OK, "display_name": "Windows",
                                                                   "allow_linking": False})
    out = ss.public_providers(load(db))
    assert out["providers"] == [{"id": "oidc", "type": "oidc", "label": "Firmen-Login",
                                 "start_url": "/api/v1/auth/oidc/start"}]
    assert out["ldap"] == {"enabled": True, "label": "Windows"}
    assert out["linking"] == {"oidc": True, "ldap": False}
    text = json.dumps(out)
    assert "idp.example.com" not in text and "s3cret" not in text and "bind-pw" not in text


def test_oidc_ready_without_secret_for_method_none(db):
    put(db, **BASE)
    upd(db, oidc={"enabled": True, "issuer": "https://idp.example", "client_id": "pub", "token_auth_method": "none"})
    assert ss.oidc_ready(load(db))


def test_secret_keys_are_f5_secret_settings():
    assert ss.SECRET_KEYS <= secret_store.SECRET_SETTING_KEYS
    assert set(ss.ALL_KEYS) >= ss.SECRET_KEYS


def test_settings_error_is_http_exception():
    from fastapi import HTTPException

    err = SsoSettingsError(422, "Text")
    assert isinstance(err, HTTPException) and err.status_code == 422 and err.detail == "Text"
    assert str(err) == "Text"


def test_policy_from(db):
    put(db, **BASE)
    upd(db, oidc={"allowed_groups": ["a"], "allowed_email_domains": ["example.com"], "role_mode": "promote",
                  "admin_groups": ["adm"], "jit_enabled": True},
        ldap={"allowed_groups": ["CN=a,DC=x"], "jit_enabled": True})
    cfg = load(db)
    p = ss.policy_from(cfg.oidc)
    assert p.source == "oidc" and p.allowed_email_domains == ("example.com",) and p.jit_permitted
    assert p.role_mode == "promote" and p.admin_groups == ("adm",)
    lp = ss.policy_from(cfg.ldap, "ldap")
    assert lp.source == "ldap" and lp.allowed_email_domains == () and lp.allowed_groups == ("CN=a,DC=x",)


# ---------------------------------------------------------------------------------------------
# Parallele Workstreams (Welle-1-Integration): F3 resolve_public_base_url
# ---------------------------------------------------------------------------------------------
@pytest.mark.wave_integration
def test_base_url_matches_f3_resolver(db, monkeypatch):
    from app.services.password_reset_mail import resolve_public_base_url   # WS-F2F3

    monkeypatch.setattr(settings, "WEBAUTHN_ORIGIN", "https://origin.example/")
    assert run(resolve_public_base_url(db)) == load(db).base_url
    put(db, app_base_url=" https://dns.example.com/ ")
    assert run(resolve_public_base_url(db)) == load(db).base_url
