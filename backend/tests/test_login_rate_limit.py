import pytest

from app.core import login_rate_limit as m


@pytest.fixture(autouse=True)
def clear_state():
    m._fails.clear()  # noqa: SLF001
    m._last_cleanup = 0.0  # noqa: SLF001
    yield
    m._fails.clear()  # noqa: SLF001
    m._last_cleanup = 0.0  # noqa: SLF001


def test_rate_limit_triggers():
    ip = "203.0.113.1"
    assert m.is_login_rate_limited(ip) is False
    for _ in range(m._MAX_FAILS - 1):  # noqa: SLF001
        m.record_failed_login(ip)
        assert m.is_login_rate_limited(ip) is False
    m.record_failed_login(ip)
    assert m.is_login_rate_limited(ip) is True


def test_clear_resets():
    ip = "203.0.113.2"
    for _ in range(m._MAX_FAILS):  # noqa: SLF001
        m.record_failed_login(ip)
    m.clear_login_fails(ip)
    assert m.is_login_rate_limited(ip) is False


def test_expired_entries_are_swept(monkeypatch):
    """Abgelaufene IP-Eintraege werden geraeumt, damit der Speicher nicht waechst."""
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(m.time, "time", lambda: clock["now"])

    m.record_failed_login("198.51.100.1")
    assert "198.51.100.1" in m._fails  # noqa: SLF001

    # Zeit ueber Fenster + Cleanup-Intervall vorruecken -> naechster Zugriff raeumt auf.
    clock["now"] += m._WINDOW_SEC + m._CLEANUP_INTERVAL_SEC + 1  # noqa: SLF001
    m.is_login_rate_limited("198.51.100.2")

    assert "198.51.100.1" not in m._fails  # noqa: SLF001
