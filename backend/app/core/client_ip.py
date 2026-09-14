"""Ermittelt die echte Client-IP – optional aus Reverse-Proxy-Headern.

Hinter einem Reverse-Proxy ist ``request.client.host`` die IP des Proxys, nicht die
des echten Clients. Das verfälscht Login-Rate-Limiting und Audit-Logs. Wir werten
``X-Forwarded-For`` / ``X-Real-IP`` aber **nur** aus, wenn ``TRUST_PROXY_HEADERS``
aktiv ist – sonst könnte jeder diese Header fälschen.

Wichtig: Proxys (nginx ``$proxy_add_x_forwarded_for``, Traefik, Caddy, Cloudflare)
HÄNGEN die IP ihres Peers an einen evtl. vom Client mitgeschickten Header AN. Der
erste Eintrag ist also client-kontrolliert; vertrauenswürdig ist nur, was der eigene
Proxy angehängt hat. Deshalb wird von rechts gezählt: bei ``TRUSTED_PROXY_HOPS``
vertrauten Proxys ist der ``HOPS``-te Eintrag von rechts die echte Client-IP.
"""
import ipaddress
from typing import Optional

from fastapi import Request

from app.core.config import settings


def _clean_ip(value: Optional[str]) -> Optional[str]:
    """Normalisiert einen Header-Wert zu einer gültigen IP (oder None)."""
    v = (value or "").strip()
    if not v:
        return None
    if v.startswith("[") and "]" in v:          # "[::1]:1234"
        v = v[1 : v.index("]")]
    elif v.count(":") == 1 and "." in v:        # "1.2.3.4:1234"
        v = v.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def get_client_ip(request: Request) -> Optional[str]:
    """Liefert die Client-IP (echte IP hinter vertrauenswürdigem Proxy, sonst Peer-IP)."""
    peer = request.client.host if request.client else None
    if not settings.TRUST_PROXY_HEADERS:
        return peer

    hops = max(1, int(getattr(settings, "TRUSTED_PROXY_HOPS", 1) or 1))
    # getlist: Proxys wie HAProxy haengen ihren Eintrag als EIGENE Header-Zeile an;
    # headers.get() wuerde nur die erste (client-kontrollierte) Zeile liefern.
    xff = ", ".join(request.headers.getlist("x-forwarded-for"))
    if xff.strip():
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if len(parts) >= hops:
            ip = _clean_ip(parts[-hops])
            if ip:
                return ip
    real = _clean_ip(request.headers.get("x-real-ip"))
    if real:
        return real
    return peer
