"""Route-Policy WS-F5-BE (Bauplan Regel 6, Anhang A3): Status der Geheimnis-Verschluesselung.

``GET /settings/secrets/status`` verlangt eine Admin-Browser-Session (``get_admin_session_user``): Panel-Tokens
bekommen 403, auch mit ``allow_admin``. Die uebrigen ``/settings/*``-Routen stehen unveraendert in ``base.py``.
"""
A = "/api/v1"

ROUTE_POLICY = {
    ("GET", f"{A}/settings/secrets/status"): "session",
}
