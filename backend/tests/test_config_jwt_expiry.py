"""JWT_EXPIRE_MINUTES folgt AUTH_COOKIE_MAX_AGE, solange es nicht explizit gesetzt ist (2.4.1)."""
import os
os.environ.setdefault("JWT_SECRET_KEY", "testsecret")
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://x:y@127.0.0.1:3306/z")

from app.core.config import Settings


def test_jwt_expiry_follows_cookie_max_age(monkeypatch):
    monkeypatch.delenv("JWT_EXPIRE_MINUTES", raising=False)
    monkeypatch.setenv("AUTH_COOKIE_MAX_AGE", "2592000")  # compose-Default: 30 Tage
    assert Settings(_env_file=None).JWT_EXPIRE_MINUTES == 43200


def test_jwt_expiry_default_without_env(monkeypatch):
    monkeypatch.delenv("JWT_EXPIRE_MINUTES", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_MAX_AGE", raising=False)
    s = Settings(_env_file=None)
    assert s.JWT_EXPIRE_MINUTES == s.AUTH_COOKIE_MAX_AGE // 60 == 1440


def test_jwt_expiry_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("AUTH_COOKIE_MAX_AGE", "2592000")
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "60")
    assert Settings(_env_file=None).JWT_EXPIRE_MINUTES == 60


def test_jwt_expiry_explicit_kwarg_wins():
    s = Settings(_env_file=None, AUTH_COOKIE_MAX_AGE=600, JWT_EXPIRE_MINUTES=5)
    assert s.JWT_EXPIRE_MINUTES == 5


def test_jwt_expiry_never_below_one_minute():
    assert Settings(_env_file=None, AUTH_COOKIE_MAX_AGE=30).JWT_EXPIRE_MINUTES == 1
