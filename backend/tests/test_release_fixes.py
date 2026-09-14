"""Regressionstests fuer die Review-Fixes von 2.4.1."""
import os
os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.datastructures import Headers

from app.main import app
from app.core import client_ip as cip
from app.core.config import settings
from app.routers.auth import ProfileUpdate, UserUpdate
from app.services import webhook_service as w

client = TestClient(app, raise_server_exceptions=False)


def test_uploads_never_serves_dotfiles(tmp_path):
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from app.main import _NoDotfiles
    (tmp_path / ".jwt_secret").write_text("SECRET")
    (tmp_path / "ok.txt").write_text("ok")
    (tmp_path / ".git").mkdir(); (tmp_path / ".git" / "config").write_text("x")
    mini = TestClient(Starlette(routes=[Mount("/uploads", _NoDotfiles(directory=str(tmp_path)))]), raise_server_exceptions=False)
    assert mini.get("/uploads/ok.txt").status_code == 200
    for path in ("/uploads/.jwt_secret", "/uploads/%2Ejwt_secret", "/uploads/.git/config", "/uploads/.env"):
        r = mini.get(path)
        assert r.status_code == 404 and "SECRET" not in r.text, path
    # In der echten App darf der Mount ebenfalls nie 200 liefern
    for path in ("/uploads/.jwt_secret", "/uploads/.env"):
        assert client.get(path).status_code in (404, 500) and "SECRET" not in client.get(path).text, path


def test_email_allows_special_use_domains_but_single_address():
    assert ProfileUpdate(email="admin@dns-manager.local").email == "admin@dns-manager.local"
    assert ProfileUpdate(email="  Admin@Example.LAN ").email.endswith("@example.lan")
    assert ProfileUpdate(email="").email is None and "email" in ProfileUpdate(email="").model_fields_set
    for bad in ("a@x.tld, b@y.tld", "a@x.tld;b@y.tld", "keine-adresse", "a@x.tld b@y.tld"):
        with pytest.raises(ValidationError):
            UserUpdate(email=bad)


def test_xff_multiple_header_lines(monkeypatch):
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1, raising=False)
    req = SimpleNamespace(
        headers=Headers(raw=[(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-for", b"9.9.9.9")]),
        client=SimpleNamespace(host="172.18.0.1"),
    )
    assert cip.get_client_ip(req) == "9.9.9.9"


def test_webhook_pinning_keeps_userinfo_and_skips_literals(monkeypatch):
    monkeypatch.setattr(w, "_resolve_checked", lambda url: ("hooks.example.com", ["203.0.113.5", "2001:db8::5"], False))
    targets = w.pin_webhook_targets("https://user:pw@hooks.example.com:8443/x?y=1")
    assert [t[0] for t in targets] == [
        "https://user:pw@203.0.113.5:8443/x?y=1",
        "https://user:pw@[2001:db8::5]:8443/x?y=1",
    ]
    assert targets[0][1] == {"Host": "hooks.example.com:8443"} and targets[0][2] == {"sni_hostname": "hooks.example.com"}
    monkeypatch.setattr(w, "_resolve_checked", lambda url: ("203.0.113.9", ["203.0.113.9"], True))
    assert w.pin_webhook_targets("http://203.0.113.9/x") == [("http://203.0.113.9/x", {}, {})]


def test_idn_host_is_idna_encoded(monkeypatch):
    calls = {}
    def fake_getaddrinfo(host, port, type=None):
        calls["host"] = host
        return [(None, None, None, None, ("93.184.216.34", port))]
    monkeypatch.setattr(w.socket, "getaddrinfo", fake_getaddrinfo)
    host, ips, lit = w._resolve_checked("https://bücher.example/x")
    assert host == "xn--bcher-kva.example" and calls["host"] == host and ips == ["93.184.216.34"] and lit is False


def test_admin_user_management_requires_browser_session():
    want = {("/auth/users", "POST"), ("/auth/users/{user_id}", "PUT"),
            ("/auth/users/{user_id}", "DELETE"), ("/auth/users/{user_id}/reset-password", "PUT"),
            ("/auth/users/{user_id}/zones", "PUT")}

    def _walk(routes):
        # FastAPI >= 0.14x haengt eingebundene Router als _IncludedRouter ein; die APIRoutes
        # liegen in original_router.routes (Pfade ohne den include-Prefix /api/v1).
        for r in routes:
            yield r
            inner = getattr(r, "routes", None)
            if inner is None:
                orig = getattr(r, "original_router", None)
                inner = getattr(orig, "routes", None)
            yield from _walk(inner or [])

    seen = set()
    for r in _walk(app.routes):
        path = getattr(r, "path", None)
        if not path:
            continue
        path = path.removeprefix("/api/v1")
        for m in (getattr(r, "methods", None) or []):
            if (path, m) in want:
                names = [getattr(d.call, "__name__", "") for d in r.dependant.dependencies]
                assert "get_admin_session_user" in names, (path, m, names)
                seen.add((path, m))
    assert seen == want
