"""Einfache IP-basierte Drosselung bei fehlgeschlagenen Logins (ohne extra Dependencies)."""
import time
from collections import deque
from typing import Deque, Dict

# Gleitfenster: pro IP max. N Fehlversuche in WINDOW Sekunden
_MAX_FAILS = 25
_WINDOW_SEC = 900  # 15 Minuten
_fails: Dict[str, Deque[float]] = {}


def _prune(client_ip: str) -> Deque[float]:
    now = time.time()
    q = _fails.get(client_ip)
    if not q:
        q = deque()
        _fails[client_ip] = q
    while q and now - q[0] > _WINDOW_SEC:
        q.popleft()
    return q


def is_login_rate_limited(client_ip: str) -> bool:
    """True wenn zu viele Fehlversuche in letzter Zeit."""
    if not client_ip or client_ip == "unknown":
        return False
    q = _prune(client_ip)
    return len(q) >= _MAX_FAILS


def record_failed_login(client_ip: str) -> None:
    if not client_ip or client_ip == "unknown":
        return
    q = _prune(client_ip)
    q.append(time.time())


def clear_login_fails(client_ip: str) -> None:
    _fails.pop(client_ip, None)
