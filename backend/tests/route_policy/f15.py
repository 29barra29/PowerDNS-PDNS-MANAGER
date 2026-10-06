"""Route-Policy WS-F15 (Anhang A3): LUA-Policy/Server-Status (user) und LUA-Einstellungen (session).

``GET /lua/server-status`` ist ``user`` (``get_current_user`` direkt); der Handler verlangt zusaetzlich Admin oder
mindestens ein Zonenrecht [S5] – geprueft in ``tests/test_lua_records.py``.
"""

A = "/api/v1"

ROUTE_POLICY = {
    ("GET", f"{A}/lua/policy"): "user",
    ("GET", f"{A}/lua/server-status"): "user",
    ("GET", f"{A}/settings/lua"): "session",
    ("PUT", f"{A}/settings/lua"): "session",
}
