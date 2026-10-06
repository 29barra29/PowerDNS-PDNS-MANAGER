"""Route-Policy WS-F10-APP-BE (F10: SSO-Router, Konto-Verknuepfung, Umwandlung, SSO-Einstellungen; Anhang A3).

- Browser-Flows der Login-Seite sind oeffentlich (kein Auth-Dependency): Anbieterliste, OIDC-Start, OIDC-Callback.
- Selbst-Verknuepfung (OIDC/LDAP) nur mit Browser-Session (``get_session_user``) + Passwort/TOTP im Body.
- Umwandlung in ein lokales Konto und alle ``/settings/sso*``-Routen nur fuer Admins mit Browser-Session
  (``get_admin_session_user``) – ein geleakter Admin-Token darf den Login nicht auf einen fremden IdP umbiegen.
"""

A = "/api/v1"

ROUTE_POLICY = {
    ("GET", f"{A}/auth/sso/providers"): "public",
    ("GET", f"{A}/auth/oidc/start"): "public",
    ("GET", f"{A}/auth/oidc/callback"): "public",
    ("POST", f"{A}/auth/me/sso/oidc/link"): "session",
    ("POST", f"{A}/auth/me/sso/ldap/link"): "session",
    ("POST", f"{A}/auth/users/{{user_id}}/convert-to-local"): "session",
    ("GET", f"{A}/settings/sso"): "session",
    ("PUT", f"{A}/settings/sso"): "session",
    ("POST", f"{A}/settings/sso/test"): "session",
}
