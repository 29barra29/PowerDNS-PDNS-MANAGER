"""Tests fuer core/rate_limit.py (SlidingWindowLimiter, ip_key)."""
import pytest

from app.core import rate_limit as rl
from app.core.rate_limit import SlidingWindowLimiter, ip_key


class Clock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_limit_after_max_events():
    lim = SlidingWindowLimiter(3, 60, clock=Clock())
    assert lim.hit("a") is False
    assert lim.hit("a") is False
    assert lim.is_limited("a") is False
    assert lim.hit("a") is False  # 3 = max_events -> noch nicht ueberschritten
    assert lim.is_limited("a") is True
    assert lim.hit("a") is True  # 4 > 3
    assert lim.count("a") == 4
    assert lim.is_limited("b") is False


def test_window_expiry():
    clock = Clock()
    lim = SlidingWindowLimiter(2, 60, clock=clock)
    lim.hit("k")
    clock.now += 30
    lim.hit("k")
    assert lim.is_limited("k") is True
    clock.now += 31  # erstes Ereignis faellt aus dem Fenster
    assert lim.count("k") == 1
    assert lim.is_limited("k") is False
    clock.now += 60
    assert lim.count("k") == 0
    assert len(lim) == 0


def test_window_expiry_with_monotonic_patch(monkeypatch):
    """Ohne eigene Uhr nutzt der Limiter time.monotonic (Spec-Fall F9 9)."""
    now = {"t": 5000.0}
    monkeypatch.setattr(rl.time, "monotonic", lambda: now["t"])
    lim = SlidingWindowLimiter(1, 10)
    lim.hit("x")
    assert lim.is_limited("x") is True
    now["t"] += 10.5
    assert lim.is_limited("x") is False


def test_is_limited_and_count_do_not_create_keys():
    lim = SlidingWindowLimiter(2, 60, clock=Clock())
    assert lim.count("nope") == 0
    assert lim.is_limited("nope") is False
    assert lim.retry_after("nope") == 1
    assert len(lim) == 0


def test_empty_and_long_keys_are_not_counted():
    lim = SlidingWindowLimiter(1, 60, clock=Clock())
    assert lim.hit("") is False
    assert lim.hit("x" * 129) is False
    assert len(lim) == 0
    assert lim.hit("x" * 128) is False
    assert len(lim) == 1


def test_max_keys_evicts_oldest():
    lim = SlidingWindowLimiter(5, 60, max_keys=3, clock=Clock())
    for k in ("a", "b", "c"):
        lim.hit(k)
    lim.hit("d")
    assert len(lim) == 3
    assert lim.count("a") == 0  # aeltester verdraengt
    assert lim.count("b") == 1 and lim.count("d") == 1


def test_retry_after_at_least_one():
    clock = Clock()
    lim = SlidingWindowLimiter(1, 100, clock=clock)
    lim.hit("r")
    assert lim.retry_after("r") == 100
    clock.now += 99.6
    assert lim.retry_after("r") == 1
    clock.now += 0.3
    assert lim.retry_after("r") >= 1


def test_reset_single_and_all():
    lim = SlidingWindowLimiter(1, 60, clock=Clock())
    lim.hit("a")
    lim.hit("b")
    lim.reset("a")
    assert lim.count("a") == 0 and lim.count("b") == 1
    lim.reset()
    assert len(lim) == 0


def test_periodic_sweep_removes_expired_keys():
    clock = Clock()
    lim = SlidingWindowLimiter(5, 10, clock=clock)
    lim.hit("old")
    clock.now += rl.CLEANUP_INTERVAL_SEC + 11
    lim.count("other")  # Lese-Check loest Aufraeumen aus
    assert len(lim) == 0


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:DB8:1:2::ffff", "2001:db8:1:2::/64"),
        ("::ffff:198.51.100.4", "198.51.100.4"),
        (None, "unknown"),
        ("", "unknown"),
        ("kaputt", "kaputt"),
    ],
)
def test_ip_key(ip, expected):
    assert ip_key(ip) == expected


def test_ip_key_groups_ipv6_64():
    assert ip_key("2001:db8:aa:bb::1") == ip_key("2001:db8:aa:bb:ffff:ffff:ffff:ffff")
    assert ip_key("2001:db8:aa:bb::1") != ip_key("2001:db8:aa:bc::1")


def test_constructor_validates():
    with pytest.raises(ValueError):
        SlidingWindowLimiter(0, 10)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(1, 0)
