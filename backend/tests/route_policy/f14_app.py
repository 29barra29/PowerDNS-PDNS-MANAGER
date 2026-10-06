"""Route-Policy WS-F14-APP (F14 3.4, 3.6-3.8; Anhang A3 "Panel-Token-Verwaltung inkl. Admin-Sicht").

Die Bestandsrouten ``GET/POST /auth/me/panel-tokens`` und ``DELETE /auth/me/panel-tokens/{token_id}`` stehen
in ``base.py`` (Kategorie ``session``). Neu: Bearbeiten des eigenen Tokens (``get_session_user``) und die
Admin-Sicht auf fremde Tokens (``get_admin_session_user``) – alles nur mit Browser-Session, nie per Token.
"""

A = "/api/v1"

ROUTE_POLICY = {
    ("PUT", f"{A}/auth/me/panel-tokens/{{token_id}}"): "session",
    ("GET", f"{A}/auth/users/{{user_id}}/panel-tokens"): "session",
    ("DELETE", f"{A}/auth/users/{{user_id}}/panel-tokens/{{token_id}}"): "session",
    ("DELETE", f"{A}/auth/users/{{user_id}}/panel-tokens"): "session",
}
