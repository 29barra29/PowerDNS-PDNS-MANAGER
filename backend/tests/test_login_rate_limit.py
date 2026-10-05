"""Tests fuer den Login-Limiter (IP /64 + Benutzername, Plan B.3 [S2])."""
import logging

import pytest

from app.core import login_rate_limit as m
from app.core.rate_limit import SlidingWindowLimiter


class Clock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def fresh_limiters(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(m, "_ip_limiter", SlidingWindowLimiter(m.IP_MAX_FAILS, m.WINDOW_SEC, clock=clock))
    monkeypatch.setattr(m, "_user_limiter", SlidingWindowLimiter(m.USER_MAX_FAILS, m.WINDOW_SEC, clock=clock))
    monkeypatch.setattr(m, "_deprecation_logged", False)
    return clock


def test_ip_limit_triggers_after_25():
    ip = "203.0.113.1"
    assert m.is_login_rate_limited(ip) is False
    for _ in range(m.IP_MAX_FAILS - 1):
        m.record_failed_login(ip)
        assert m.is_login_rate_limited(ip) is False
    m.record_failed_login(ip)
    assert m.is_login_rate_limited(ip) is True
    assert m.is_login_rate_limited("203.0.113.2") is False


def test_ipv6_prefix_64_shares_counter():
    for i in range(m.IP_MAX_FAILS):
        m.record_failed_login(f"2001:db8:1:2::{i + 1:x}")
    assert m.is_login_rate_limited("2001:db8:1:2:ffff::9") is True
    assert m.is_login_rate_limited("2001:db8:1:3::1") is False


def test_username_locked_after_5_from_changing_ips():
    for i in range(m.USER_MAX_FAILS):
        assert m.is_login_rate_limited(f"198.51.100.{i + 1}", "Alice") is False
        m.record_failed_login(f"198.51.100.{i + 1}", "Alice")
    # 6. Versuch von neuer IP -> gesperrt (Benutzer-Fenster), Schreibweise egal
    assert m.is_login_rate_limited("192.0.2.200", "  alice ") is True
    assert m.is_login_rate_limited("192.0.2.200", "bob") is False
    # IP allein ist nicht gesperrt
    assert m.is_login_rate_limited("192.0.2.200") is False


def test_username_window_expires(fresh_limiters):
    for _ in range(m.USER_MAX_FAILS):
        m.record_failed_login("198.51.100.9", "carol")
    assert m.is_login_rate_limited(None, "carol") is True
    fresh_limiters.now += m.WINDOW_SEC + 1
    assert m.is_login_rate_limited(None, "carol") is False


def test_successful_login_clears_only_the_pair():
    ip = "203.0.113.50"
    for _ in range(4):
        m.record_failed_login(ip, "dave")
    for _ in range(4):
        m.record_failed_login(ip, "erin")
    m.clear_login_fails(ip, "Dave")
    assert m._user_limiter.count("dave") == 0  # noqa: SLF001
    assert m._user_limiter.count("erin") == 4  # noqa: SLF001 - anderer Benutzer bleibt
    assert m._ip_limiter.count(ip) == 8  # noqa: SLF001 - IP-Zaehler bleibt


def test_ip_counter_is_never_cleared():
    ip = "203.0.113.60"
    for _ in range(m.IP_MAX_FAILS):
        m.record_failed_login(ip, "x")
    m.clear_login_fails(ip, "x")
    assert m.is_login_rate_limited(ip, "x") is True


def test_single_arg_clear_is_noop_with_deprecation_log(caplog):
    ip = "203.0.113.70"
    for _ in range(3):
        m.record_failed_login(ip, "frank")
    with caplog.at_level(logging.DEBUG, logger=m.logger.name):
        m.clear_login_fails(ip)
        m.clear_login_fails(ip)
    assert m._user_limiter.count("frank") == 3  # noqa: SLF001
    assert m._ip_limiter.count(ip) == 3  # noqa: SLF001
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "veraltet" in warnings[0].getMessage()


def test_compatible_signature_without_username():
    """2.4.x-Aufrufer (nur IP) funktionieren weiter."""
    m.record_failed_login("203.0.113.80")
    assert m._ip_limiter.count("203.0.113.80") == 1  # noqa: SLF001
    assert m.is_login_rate_limited("203.0.113.80") is False


def test_unknown_or_invalid_ip_is_ignored():
    for _ in range(m.IP_MAX_FAILS + 5):
        m.record_failed_login("unknown")
        m.record_failed_login("")
        m.record_failed_login("x" * 65)
    assert len(m._ip_limiter) == 0  # noqa: SLF001
    assert m.is_login_rate_limited("unknown") is False


def test_username_normalization_and_length():
    long_name = "A" * 150
    assert m.normalize_username(long_name) == "a" * 100
    assert m.normalize_username(None) == ""
    for _ in range(m.USER_MAX_FAILS):
        m.record_failed_login(None, long_name)
    assert m.is_login_rate_limited(None, "a" * 100 + "zzz") is True


def test_empty_username_is_not_counted():
    m.record_failed_login("203.0.113.90", "   ")
    assert len(m._user_limiter) == 0  # noqa: SLF001


def test_reset_for_tests_clears_module_state():
    m.record_failed_login("203.0.113.91", "gina")
    m.reset_for_tests()
    assert len(m._ip_limiter) == 0 and len(m._user_limiter) == 0  # noqa: SLF001
