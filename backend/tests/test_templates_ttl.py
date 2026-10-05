"""Vorlagen: TTL-Grenzen 60..604800 wie bei Records (F8 3.8/5.5, Test Nr. 13).

Ohne Grenzen liessen sich Vorlagen speichern, deren Records beim Anlegen einer Zone an ``RecordCreate.ttl``
(ge=60, le=604800) scheitern. Ungueltige Werte werden jetzt schon beim Speichern der Vorlage mit 422 abgelehnt.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from authfakes import FakeSession, build_app
from app.core.auth import get_admin_user
from app.routers import templates
from app.routers.templates import (
    TEMPLATE_TTL_MAX,
    TEMPLATE_TTL_MIN,
    TemplateCreate,
    TemplateRecord,
    TemplateUpdate,
)
from app.schemas.dns import RecordCreate


def test_template_ttl_bounds():
    # F8 9.1 Nr. 13
    with pytest.raises(ValidationError):
        TemplateRecord(name="@", type="A", content="1.2.3.4", ttl=30)
    with pytest.raises(ValidationError):
        TemplateCreate(name="x", default_ttl=604801)


def test_template_ttl_bounds_match_record_schema():
    # Vorlagen und Records nutzen dieselben Grenzen (sonst scheitern Vorlagen-Records beim Zonen-Anlegen).
    ttl_field = RecordCreate.model_fields["ttl"]
    bounds = {type(m).__name__: m for m in ttl_field.metadata}
    assert bounds["Ge"].ge == TEMPLATE_TTL_MIN == 60
    assert bounds["Le"].le == TEMPLATE_TTL_MAX == 604800


@pytest.mark.parametrize("ttl", [TEMPLATE_TTL_MIN, 3600, TEMPLATE_TTL_MAX])
def test_template_ttl_limits_inclusive(ttl):
    assert TemplateRecord(name="@", type="A", content="192.0.2.1", ttl=ttl).ttl == ttl
    assert TemplateCreate(name="x", default_ttl=ttl).default_ttl == ttl
    assert TemplateUpdate(default_ttl=ttl).default_ttl == ttl


@pytest.mark.parametrize("ttl", [-1, 0, 59, 604801, 10**9])
def test_template_ttl_out_of_range_rejected(ttl):
    with pytest.raises(ValidationError):
        TemplateRecord(name="@", type="A", content="192.0.2.1", ttl=ttl)
    with pytest.raises(ValidationError):
        TemplateCreate(name="x", default_ttl=ttl)
    with pytest.raises(ValidationError):
        TemplateUpdate(default_ttl=ttl)


def test_template_defaults_unchanged():
    # Ohne Angabe gelten weiterhin 3600 s; Update ohne default_ttl laesst den Wert unveraendert (None).
    assert TemplateRecord(name="@", type="A", content="192.0.2.1").ttl == 3600
    assert TemplateCreate(name="x").default_ttl == 3600
    assert TemplateUpdate().default_ttl is None


def test_template_record_list_validated_in_create():
    # Ein einzelner ungueltiger Record macht die ganze Vorlage ungueltig.
    with pytest.raises(ValidationError):
        TemplateCreate(name="x", records=[
            {"name": "@", "type": "A", "content": "192.0.2.1", "ttl": 3600},
            {"name": "www", "type": "A", "content": "192.0.2.2", "ttl": 10},
        ])


def _client():
    app = build_app(FakeSession(), templates)
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(id=1, role="admin", username="admin")
    return TestClient(app, raise_server_exceptions=False)


def test_create_template_endpoint_rejects_invalid_ttl():
    c = _client()
    r = c.post("/api/v1/templates", json={"name": "x", "default_ttl": 30})
    assert r.status_code == 422, r.text
    r = c.post("/api/v1/templates", json={
        "name": "x",
        "records": [{"name": "@", "type": "A", "content": "192.0.2.1", "ttl": 604801}],
    })
    assert r.status_code == 422, r.text


def test_update_template_endpoint_rejects_invalid_ttl():
    c = _client()
    r = c.put("/api/v1/templates/1", json={"default_ttl": 59})
    assert r.status_code == 422, r.text
    r = c.put("/api/v1/templates/1", json={"records": [{"name": "@", "type": "A", "content": "x", "ttl": 0}]})
    assert r.status_code == 422, r.text


def test_create_template_endpoint_accepts_valid_ttl():
    c = _client()
    r = c.post("/api/v1/templates", json={
        "name": "x",
        "default_ttl": 60,
        "records": [{"name": "@", "type": "A", "content": "192.0.2.1", "ttl": 604800}],
    })
    assert r.status_code == 200, r.text
    assert "erstellt" in r.json()["message"]
