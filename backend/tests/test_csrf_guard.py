"""CSRF-Schutz: Cross-Site-Cookie-Requests werden abgelehnt, eigene Seite und Bearer nicht."""
import os
os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
URL = "/api/v1/auth/logout"


def test_cross_site_origin_rejected():
    r = client.post(URL, headers={"Origin": "https://evil.example", "Host": "dns.example.com"})
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


def test_sec_fetch_cross_site_rejected():
    r = client.post(URL, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_allowed():
    r = client.post(URL, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code != 403
    r = client.post(URL, headers={"Origin": "https://dns.example.com", "Host": "dns.example.com"})
    assert r.status_code != 403


def test_bearer_and_non_browser_allowed():
    r = client.post(URL, headers={"Authorization": "Bearer dnsmgr_usr_x", "Origin": "https://evil.example"})
    assert r.status_code != 403
    r = client.post(URL)  # curl/Skript: weder Origin noch Sec-Fetch-Site
    assert r.status_code != 403


def test_get_never_blocked():
    r = client.get("/api/v1/setup/status", headers={"Origin": "https://evil.example"})
    assert r.status_code != 403
