"""Drosselung fehlgeschlagener Anmeldungen: pro IP (IPv6 je /64) und pro Benutzername.

- IP-Fenster: 25 Fehlversuche in 15 Minuten je ``rate_limit.ip_key`` (IPv6 /64).
- Benutzer-Fenster: 5 Fehlversuche in 15 Minuten je normalisiertem Benutzernamen
  (``strip().lower()[:100]``), unabhaengig von der IP. Damit laesst sich ein Passwort
  nicht von wechselnden Adressen durchprobieren, und ein gesperrter Benutzername loest
  weder Passwortvergleich noch LDAP-Bind aus (kein AD-Lockout ueber das Panel).
- ``clear_login_fails`` loescht nach erfolgreicher Anmeldung nur den Benutzer-Zaehler;
  der IP-Zaehler laeuft immer aus (ein erfolgreicher Login mit einem eigenen Konto darf
  die Fehlversuche gegen fremde Konten derselben IP nicht vergessen machen).

Dieselben Zaehler nutzen ``/auth/login``, ``/auth/login/2fa``, Passkey-Login, LDAP-Link
und die Step-up-Pruefung. Antworten bei Benutzer- und IP-Sperre sind identisch (429).
"""
from __future__ import annotations

import logging
from typing import Optional

from app.core.rate_limit import SlidingWindowLimiter, ip_key

logger = logging.getLogger(__name__)

IP_MAX_FAILS = 25
USER_MAX_FAILS = 5
WINDOW_SEC = 900  # 15 Minuten
USERNAME_MAX_LEN = 100
_MAX_IP_LEN = 64

_ip_limiter = SlidingWindowLimiter(IP_MAX_FAILS, WINDOW_SEC, max_keys=20_000)
_user_limiter = SlidingWindowLimiter(USER_MAX_FAILS, WINDOW_SEC, max_keys=20_000)
_deprecation_logged = False


def normalize_username(username: Optional[str]) -> str:
    """Schluessel des Benutzer-Zaehlers: getrimmt, klein, max. 100 Zeichen."""
    return (username or "").strip().lower()[:USERNAME_MAX_LEN]


def _ip_counter_key(client_ip: Optional[str]) -> Optional[str]:
    ip = (client_ip or "").strip()
    if not ip or ip == "unknown" or len(ip) > _MAX_IP_LEN:
        return None
    return ip_key(ip)


def is_login_rate_limited(client_ip: Optional[str], username: Optional[str] = None) -> bool:
    """True, wenn die IP (bzw. ihr /64) ODER der Benutzername gesperrt ist."""
    key = _ip_counter_key(client_ip)
    if key is not None and _ip_limiter.is_limited(key):
        return True
    user = normalize_username(username)
    if user and _user_limiter.is_limited(user):
        return True
    return False


def record_failed_login(client_ip: Optional[str], username: Optional[str] = None) -> None:
    """Zaehlt einen Fehlversuch fuer die IP und – falls angegeben – fuer den Benutzernamen."""
    key = _ip_counter_key(client_ip)
    if key is not None:
        _ip_limiter.hit(key)
    user = normalize_username(username)
    if user:
        _user_limiter.hit(user)


def clear_login_fails(client_ip: Optional[str], username: Optional[str] = None) -> None:
    """Nach erfolgreicher Anmeldung: loescht NUR den Benutzer-Zaehler; der IP-Zaehler bleibt.

    Aufruf ohne Benutzernamen (Altform aus 2.4.x) ist ein No-op mit Deprecation-Log,
    bis alle Aufrufer umgestellt sind.
    """
    global _deprecation_logged
    if username is None:
        if not _deprecation_logged:
            _deprecation_logged = True
            logger.warning(
                "clear_login_fails() ohne Benutzernamen ist veraltet und wirkungslos "
                "(IP-Zaehler werden nicht mehr geloescht)."
            )
        else:
            logger.debug("clear_login_fails() ohne Benutzernamen ignoriert")
        return
    user = normalize_username(username)
    if user:
        _user_limiter.reset(user)


def reset_for_tests() -> None:
    """Alle Zaehler leeren (nur fuer Tests)."""
    global _deprecation_logged
    _ip_limiter.reset()
    _user_limiter.reset()
    _deprecation_logged = False
