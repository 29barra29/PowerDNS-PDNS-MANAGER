"""F8 §9.1 Nr. 9–12 (Backend-Teil von F8 in routers/settings.py): App-Info, Logo, SMTP-Passwort-Semantik.

Endpunkt-Tests gegen SQLite (Infrastruktur aus ``test_secrets_routes``): leere Basis-URL leert, Logo entfernen
loescht ``custom-logo.*``, Audits ``APP_INFO_UPDATE`` (nur Feldnamen) und ``APP_LOGO_UPLOAD``.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.secret_mask import SECRET_MASK, resolve_secret_update
from app.routers import settings as settings_router
from app.routers.settings import AppInfoUpdate, SmtpSettings, _remove_custom_logo_files

from test_secrets_routes import detached_audits, sdb, settings_client  # noqa: F401 - Fixtures

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# --- Nr. 9: leere Basis-URL bedeutet "leeren" -------------------------------------------------

def test_app_info_base_url_empty_means_clear():
    assert AppInfoUpdate(app_name="x", app_base_url="").app_base_url == ""
    assert AppInfoUpdate(app_name="x", app_base_url="  ").app_base_url == ""
    assert AppInfoUpdate(app_name="x").app_base_url is None
    assert AppInfoUpdate(app_name="x", app_base_url=" https://dns.example.com ").app_base_url == "https://dns.example.com"
    for bad in ("ftp://x", "https://a b", "dns.example.com"):
        with pytest.raises(ValidationError):
            AppInfoUpdate(app_name="x", app_base_url=bad)


# --- Nr. 10: Logo-Dateien loeschen ------------------------------------------------------------

def test_remove_custom_logo_files(tmp_path):
    (tmp_path / "custom-logo.png").write_bytes(b"a")
    (tmp_path / "custom-logo.svg").write_bytes(b"b")
    (tmp_path / "other.png").write_bytes(b"c")
    (tmp_path / "custom-logo.dir").mkdir()
    assert _remove_custom_logo_files(tmp_path) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["custom-logo.dir", "other.png"]
    assert _remove_custom_logo_files(tmp_path / "fehlt") == 0


# --- Nr. 11/12: SMTP-Passwort-Semantik --------------------------------------------------------

@pytest.mark.parametrize("new,old,expected", [
    (None, "a", "a"), (SECRET_MASK, "a", "a"), (" ", "a", ""), ("", "a", ""), ("neu", "a", "neu"), (None, None, ""),
])
def test_resolve_secret_update(new, old, expected):
    assert resolve_secret_update(new, old) == expected


def test_smtp_settings_password_optional():
    assert SmtpSettings().password is None


# --- Endpunkte ---------------------------------------------------------------------------------

@pytest.fixture
def uploads(tmp_path, monkeypatch):
    d = tmp_path / "uploads"
    monkeypatch.setattr(settings_router, "_uploads_dir", lambda: d)
    return d


def test_app_info_clear_base_url_and_audit(settings_client):
    sdb = settings_client.sdb
    c = settings_client
    assert c.put("/api/v1/settings/app-info", json={"app_name": "Panel", "app_base_url": "https://a.example"}).status_code == 200
    first = sdb.audits("APP_INFO_UPDATE")[-1]
    assert first.details == {"changed": ["app_base_url", "app_name"], "logo_files_removed": 0}
    assert c.get("/api/v1/settings/admin-info").json()["app_base_url"] == "https://a.example"

    r = c.put("/api/v1/settings/app-info", json={"app_name": "Panel", "app_base_url": ""})
    assert r.status_code == 200 and r.json() == {"message": "Einstellungen aktualisiert"}
    assert c.get("/api/v1/settings/admin-info").json()["app_base_url"] is None
    assert sdb.setting("app_base_url") == ""
    audits = sdb.audits("APP_INFO_UPDATE")
    assert audits[-1].details == {"changed": ["app_base_url"], "logo_files_removed": 0}
    assert audits[-1].resource_type == "settings" and audits[-1].resource_name == "app-info"
    assert "a.example" not in str(audits[-1].details)  # keine Werte im Audit

    # Nichts geaendert -> kein weiterer Audit-Eintrag
    assert c.put("/api/v1/settings/app-info", json={"app_name": "Panel"}).status_code == 200
    assert len(sdb.audits("APP_INFO_UPDATE")) == len(audits)


def test_app_info_flags_and_branding(settings_client):
    sdb = settings_client.sdb
    r = settings_client.put("/api/v1/settings/app-info", json={
        "app_name": "X", "registration_enabled": True, "forgot_password_enabled": False,
        "app_tagline": "  Tag  ", "app_creator": "Me"})
    assert r.status_code == 200
    assert (sdb.setting("registration_enabled"), sdb.setting("forgot_password_enabled")) == ("true", "false")
    assert sdb.setting("app_tagline") == "Tag"
    public = settings_client.get("/api/v1/settings/app-info").json()
    assert public["app_name"] == "X" and public["registration_enabled"] is True and public["app_tagline"] == "Tag"
    assert sdb.audits("APP_INFO_UPDATE")[-1].details["changed"] == [
        "app_creator", "app_name", "app_tagline", "forgot_password_enabled", "registration_enabled"]


def test_logo_upload_and_remove(settings_client, uploads):
    sdb = settings_client.sdb
    uploads.mkdir()
    (uploads / "custom-logo.svg").write_bytes(b"<svg/>")  # Altlast, wird beim Upload ersetzt
    r = settings_client.post("/api/v1/settings/app-logo", files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["app_logo_url"] == "/uploads/custom-logo.png"
    assert sorted(p.name for p in uploads.iterdir()) == ["custom-logo.png"]
    assert sdb.setting("app_logo_url") == "/uploads/custom-logo.png"
    up = sdb.audits("APP_LOGO_UPLOAD")[-1]
    assert (up.resource_type, up.resource_name, up.details) == ("settings", "app-logo", {"ext": ".png", "bytes": len(PNG)})

    r = settings_client.put("/api/v1/settings/app-info", json={"app_name": "P", "app_logo_url": ""})
    assert r.status_code == 200
    assert list(uploads.iterdir()) == [] and sdb.setting("app_logo_url") == ""
    assert sdb.audits("APP_INFO_UPDATE")[-1].details == {"changed": ["app_logo_url", "app_name"], "logo_files_removed": 1}


def test_logo_external_url_cleared_keeps_files(settings_client, uploads):
    uploads.mkdir()
    (uploads / "custom-logo.png").write_bytes(PNG)
    sdb = settings_client.sdb
    sdb.put_setting("app_logo_url", "https://cdn.example.com/logo.png")
    r = settings_client.put("/api/v1/settings/app-info", json={"app_name": "P", "app_logo_url": "  "})
    assert r.status_code == 200 and sdb.setting("app_logo_url") == ""
    assert (uploads / "custom-logo.png").exists()
    assert sdb.audits("APP_INFO_UPDATE")[-1].details["logo_files_removed"] == 0


def test_logo_upload_rejects_unknown_type(settings_client, uploads):
    r = settings_client.post("/api/v1/settings/app-logo", files={"file": ("x.gif", b"GIF89a....", "image/gif")})
    assert r.status_code == 400 and r.json()["detail"] == "Nur PNG, JPG, WEBP oder SVG erlaubt"
    assert settings_client.sdb.audits("APP_LOGO_UPLOAD") == []
