"""Einfache IP-basierte Drosselung bei fehlgeschlagenen Logins (ohne extra Dependencies)."""
import time
from collections import deque
from typing import Deque, Dict

# Gleitfenster: pro IP max. N Fehlversuche in WINDOW Sekunden
_MAX_FAILS = 25
_WINDOW_SEC = 900  # 15 Minuten
_fails: Dict[str, Deque[float]] = {}

# Regelmäßiges Aufräumen, damit das Dict nicht unbegrenzt wächst (IPs, die einmal
# fehlschlugen und nie wiederkommen, würden sonst für immer Speicher belegen).
_CLEANUP_INTERVAL_SEC = 300  # alle 5 Minuten
_last_cleanup = 0.0


def _sweep(now: float) -> None:
    """Entfernt abgelaufene Einträge aus ALLEN IPs (nicht nur der aktuellen)."""
    global _last_cleanup
    if now - _last_cleanup < _CLEANUP_INTERVAL_SEC:
        return
    _last_cleanup = now
    for ip in list(_fails.keys()):
        q = _fails[ip]
        while q and now - q[0] > _WINDOW_SEC:
            q.popleft()
        if not q:
            del _fails[ip]


def _prune(client_ip: str, create: bool = False):
    """Liefert die (bereinigte) Fehlversuchs-Queue der IP.

    Ohne ``create`` wird KEIN neuer Eintrag angelegt: reine Lese-Checks (jeder
    Login-Aufruf) duerfen das Dict nicht wachsen lassen (Speicher-DoS ohne Login).
    """
    now = time.time()
    _sweep(now)
    q = _fails.get(client_ip)
    if q is None:
        if not create:
            return None
        q = deque()
        _fails[client_ip] = q
    while q and now - q[0] > _WINDOW_SEC:
        q.popleft()
    if not q and not create:
        _fails.pop(client_ip, None)
    return q


def is_login_rate_limited(client_ip: str) -> bool:
    """True wenn zu viele Fehlversuche in letzter Zeit."""
    if not client_ip or client_ip == "unknown":
        return False
    q = _prune(client_ip)
    return bool(q) and len(q) >= _MAX_FAILS


def record_failed_login(client_ip: str) -> None:
    if not client_ip or client_ip == "unknown" or len(client_ip) > 64:
        return
    q = _prune(client_ip, create=True)
    q.append(time.time())


def clear_login_fails(client_ip: str) -> None:
    _fails.pop(client_ip, None)
