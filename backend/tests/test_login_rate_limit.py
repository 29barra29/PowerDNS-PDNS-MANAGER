import pytest

from app.core import login_rate_limit as m


@pytest.fixture(autouse=True)
def clear_state():
    m._fails.clear()  # noqa: SLF001
    yield
    m._fails.clear()  # noqa: SLF001


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
