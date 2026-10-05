"""Erkennung von Browser-Anfragen, die von einer fremden Seite ausgeloest wurden.

Browser cachen Basic-Credentials pro Origin. Ohne diese Pruefung koennte eine fremde
Seite per ``<img src=".../nic/update?myip=6.6.6.6">`` einen DNS-Eintrag umbiegen.
Router, curl und Skripte senden weder ``Sec-Fetch-Site`` noch ``Origin`` und sind nicht
betroffen.
"""
from urllib.parse import urlparse

from fastapi import Request

from app.core.config import settings


def is_cross_site_browser_request(request: Request) -> bool:
    """True bei ``Sec-Fetch-Site`` cross-site/same-site oder fremdem/``null``-Origin."""
    sfs = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if sfs and sfs not in ("same-origin", "none"):  # cross-site UND same-site (Nachbar-Subdomain) ablehnen
        return True
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return False  # Router/curl/Skripte
    if origin.lower() == "null":
        return True
    host = (urlparse(origin).netloc or "").lower()
    own = {(request.headers.get("host") or "").strip().lower()}
    if settings.TRUST_PROXY_HEADERS:
        fwd = ", ".join(request.headers.getlist("x-forwarded-host"))
        if fwd.strip():
            own.add(fwd.split(",")[-1].strip().lower())
    own.discard("")
    return host not in own
