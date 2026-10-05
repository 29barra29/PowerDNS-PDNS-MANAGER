"""In-Memory-Gleitfenster-Limiter (geteilt von Login, DynDNS, Propagation).

Setzt einen Worker-Prozess voraus (wie das gesamte Panel): Zaehler liegen im Speicher
und gehen bei einem Neustart verloren.
"""
from __future__ import annotations

import ipaddress
import math
import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

MAX_KEY_LENGTH = 128
CLEANUP_INTERVAL_SEC = 60.0


def ip_key(ip: Optional[str]) -> str:
    """Schluessel fuer IP-basierte Limits.

    IPv4 bleibt unveraendert, IPv6 wird auf das /64-Netz reduziert (ein Anschluss hat
    typischerweise ein ganzes /64 – sonst koennte ein Angreifer pro Versuch eine neue
    Adresse waehlen). IPv4-gemappte IPv6-Adressen zaehlen als IPv4. ``None``/leer ->
    ``"unknown"``; unlesbare Werte werden unveraendert (getrimmt) zurueckgegeben.
    """
    v = (ip or "").strip()
    if not v:
        return "unknown"
    try:
        addr = ipaddress.ip_address(v)
    except ValueError:
        return v
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        net = ipaddress.IPv6Network((int(addr), 64), strict=False)
        return str(net)
    return str(addr)


class SlidingWindowLimiter:
    """Gleitfenster pro Schluessel: hoechstens ``max_events`` Ereignisse in ``window_sec``.

    - ``count``/``is_limited`` legen keine Eintraege an (reine Lese-Checks duerfen den
      Speicher nicht wachsen lassen).
    - Leere oder zu lange Schluessel (> 128 Zeichen) werden nie gezaehlt.
    - Abgelaufene Schluessel werden hoechstens alle 60 s komplett aufgeraeumt.
    - Ist ``max_keys`` erreicht, wird der aelteste Schluessel (Einfuegereihenfolge)
      verdraengt.
    """

    def __init__(
        self,
        max_events: int,
        window_sec: float,
        *,
        max_keys: int = 10_000,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events muss >= 1 sein")
        if window_sec <= 0:
            raise ValueError("window_sec muss > 0 sein")
        self.max_events = int(max_events)
        self.window_sec = float(window_sec)
        self.max_keys = max(1, int(max_keys))
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._last_cleanup = 0.0

    # ------------------------------------------------------------------
    def _now(self) -> float:
        return self._clock() if self._clock is not None else time.monotonic()

    @staticmethod
    def _valid(key: Optional[str]) -> bool:
        return bool(key) and len(key) <= MAX_KEY_LENGTH

    def _prune(self, q: Deque[float], now: float) -> None:
        limit = now - self.window_sec
        while q and q[0] <= limit:
            q.popleft()

    def _sweep(self, now: float) -> None:
        if now - self._last_cleanup < CLEANUP_INTERVAL_SEC:
            return
        self._last_cleanup = now
        for k in list(self._events.keys()):
            q = self._events[k]
            self._prune(q, now)
            if not q:
                del self._events[k]

    def _get(self, key: str, now: float) -> Optional[Deque[float]]:
        q = self._events.get(key)
        if q is None:
            return None
        self._prune(q, now)
        if not q:
            del self._events[key]
            return None
        return q

    # ------------------------------------------------------------------
    def count(self, key: str) -> int:
        """Anzahl Ereignisse im aktuellen Fenster (legt keinen Eintrag an)."""
        if not self._valid(key):
            return 0
        now = self._now()
        self._sweep(now)
        q = self._get(key, now)
        return len(q) if q else 0

    def is_limited(self, key: str) -> bool:
        """True, wenn bereits ``max_events`` Ereignisse im Fenster liegen."""
        return self.count(key) >= self.max_events

    def hit(self, key: str) -> bool:
        """Zaehlt ein Ereignis. True = nach diesem Ereignis ist das Limit ueberschritten (> max_events)."""
        if not self._valid(key):
            return False
        now = self._now()
        self._sweep(now)
        q = self._get(key, now)
        if q is None:
            while len(self._events) >= self.max_keys:
                oldest = next(iter(self._events))
                del self._events[oldest]
            q = deque()
            self._events[key] = q
        q.append(now)
        return len(q) > self.max_events

    def retry_after(self, key: str) -> int:
        """Sekunden, bis das aelteste Ereignis des Schluessels aus dem Fenster faellt (mindestens 1)."""
        if not self._valid(key):
            return 1
        now = self._now()
        q = self._get(key, now)
        if not q:
            return 1
        return max(1, int(math.ceil(q[0] + self.window_sec - now)))

    def reset(self, key: Optional[str] = None) -> None:
        """Einen Schluessel (oder mit ``None`` alle) zuruecksetzen."""
        if key is None:
            self._events.clear()
            self._last_cleanup = 0.0
            return
        self._events.pop(key, None)

    def __len__(self) -> int:  # Anzahl gespeicherter Schluessel (fuer Tests/Diagnose)
        return len(self._events)
