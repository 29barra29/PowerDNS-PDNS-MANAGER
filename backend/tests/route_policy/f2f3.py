"""Route-Policy WS-F2F3 (F2/F3: Admin-Benutzersicherheit, Zugangs-Widerruf; Anhang A3).

NOTIFY und Export (``POST …/notify``, ``GET …/export``) bleiben in ``base.py`` (Kategorie ``zone``, unveraendert).
Neue Admin-Mutationen verlangen eine Browser-Session (``get_admin_session_user``), die reine Zaehler-Abfrage
``access-summary`` ist ``admin`` (Token nur mit ``allow_admin``), wie ``GET /auth/users``.
"""

A = "/api/v1"

ROUTE_POLICY = {
    ("POST", f"{A}/auth/users/{{user_id}}/send-reset-link"): "session",
    ("POST", f"{A}/auth/users/{{user_id}}/reset-2fa"): "session",
    ("DELETE", f"{A}/auth/users/{{user_id}}/webauthn-credentials"): "session",
    ("POST", f"{A}/auth/users/{{user_id}}/revoke-access"): "session",
    ("GET", f"{A}/auth/users/{{user_id}}/access-summary"): "admin",
}
