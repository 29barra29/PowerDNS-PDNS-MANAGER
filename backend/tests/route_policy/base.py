"""Route-Policy der Bestandsrouten (2.4.1) und der Umzuege aus Welle 0b (F14 9.2 Nr. 1, Anhang A3).

Besitzer: W0-INT-BE2a. Neue Routen klassifiziert der jeweilige Workstream in seiner eigenen Datei
``route_policy/<ws>.py`` (diese Datei nicht erweitern).
"""

A = "/api/v1"
ZP = "{server_name}/{zone_id:path}"

ROUTE_POLICY = {
    # --- public: ohne Anmeldung -------------------------------------------------------------
    ("GET", f"{A}/setup/status"): "public",
    ("POST", f"{A}/setup/register"): "public",
    ("POST", f"{A}/auth/login"): "public",
    ("POST", f"{A}/auth/login/2fa"): "public",
    ("POST", f"{A}/auth/logout"): "public",
    ("POST", f"{A}/auth/register"): "public",
    ("POST", f"{A}/auth/forgot-password"): "public",
    ("POST", f"{A}/auth/reset-password"): "public",
    ("POST", f"{A}/auth/webauthn/login/begin"): "public",
    ("POST", f"{A}/auth/webauthn/login/complete"): "public",
    ("GET", f"{A}/settings/app-info"): "public",
    # Root-Routen (nur GET/HEAD): Health (Zusatzfelder nur fuer Loopback, BE2b), API-Info, SPA-Icon
    ("GET", "/health"): "public",
    ("GET", "/api"): "public",
    ("GET", "/vite.svg"): "public",

    # --- own_auth: eigene Token-Pruefung ----------------------------------------------------
    ("POST", f"{A}/acme/present"): "own_auth",
    ("POST", f"{A}/acme/cleanup"): "own_auth",
    ("GET", f"{A}/acme/whoami"): "own_auth",
    # Prometheus-Scrape (F13; Route legt W0-INT-BE2b an, Bearer-Pruefung ueber metrics_runtime)
    ("GET", "/metrics"): "own_auth",

    # --- user: angemeldet, zonenunabhaengig (Panel-Token erlaubt) -----------------------------
    ("GET", f"{A}/auth/me"): "user",
    ("GET", f"{A}/auth/me/webhooks"): "user",  # per Token ohne Ziel-URL
    ("GET", f"{A}/servers"): "user",  # zone_count gefiltert, url nur Admins
    ("GET", f"{A}/servers/{{server_name}}"): "user",  # Statistik/url nur Admins
    ("GET", f"{A}/servers/{{server_name}}/statistics"): "user",  # im Handler: assert_not_zone_scoped + assert_effective_admin
    ("GET", f"{A}/zones/{{server_name}}"): "user",  # Liste gefiltert ueber effective_zone_filter
    ("GET", f"{A}/search/{{server_name}}"): "user",
    ("GET", f"{A}/search"): "user",
    ("GET", f"{A}/templates"): "user",

    # --- session: nur Browser-Session ------------------------------------------------------
    ("PUT", f"{A}/auth/me"): "session",
    ("PUT", f"{A}/auth/me/password"): "session",
    ("GET", f"{A}/auth/me/totp/status"): "session",
    ("POST", f"{A}/auth/me/totp/begin"): "session",
    ("POST", f"{A}/auth/me/totp/enable"): "session",
    ("POST", f"{A}/auth/me/totp/disable"): "session",
    ("GET", f"{A}/auth/me/webauthn/credentials"): "session",
    ("POST", f"{A}/auth/me/webauthn/register/begin"): "session",
    ("POST", f"{A}/auth/me/webauthn/register/complete"): "session",
    ("DELETE", f"{A}/auth/me/webauthn/credentials/{{cred_id}}"): "session",
    ("GET", f"{A}/auth/me/panel-tokens"): "session",
    ("POST", f"{A}/auth/me/panel-tokens"): "session",
    ("DELETE", f"{A}/auth/me/panel-tokens/{{token_id}}"): "session",
    ("POST", f"{A}/auth/me/webhooks"): "session",
    ("PUT", f"{A}/auth/me/webhooks/{{webhook_id}}"): "session",
    ("DELETE", f"{A}/auth/me/webhooks/{{webhook_id}}"): "session",
    ("POST", f"{A}/auth/users"): "session",
    ("PUT", f"{A}/auth/users/{{user_id}}"): "session",
    ("DELETE", f"{A}/auth/users/{{user_id}}"): "session",
    ("PUT", f"{A}/auth/users/{{user_id}}/reset-password"): "session",
    ("PUT", f"{A}/auth/users/{{user_id}}/zones"): "session",
    ("GET", f"{A}/settings/admin-info"): "session",
    ("PUT", f"{A}/settings/app-info"): "session",
    ("POST", f"{A}/settings/app-logo"): "session",
    ("GET", f"{A}/settings/servers"): "session",
    ("POST", f"{A}/settings/servers"): "session",
    ("PUT", f"{A}/settings/servers/{{server_id}}"): "session",
    ("DELETE", f"{A}/settings/servers/{{server_id}}"): "session",
    ("GET", f"{A}/settings/servers/{{server_id}}/api-key"): "session",
    ("POST", f"{A}/settings/servers/test"): "session",
    ("GET", f"{A}/settings/smtp"): "session",
    ("PUT", f"{A}/settings/smtp"): "session",
    ("POST", f"{A}/settings/smtp/test"): "session",
    ("POST", f"{A}/settings/smtp/test-email"): "session",
    ("GET", f"{A}/settings/captcha"): "session",
    ("PUT", f"{A}/settings/captcha"): "session",
    ("POST", f"{A}/settings/captcha/test"): "session",
    ("GET", f"{A}/settings/welcome-email"): "session",
    ("PUT", f"{A}/settings/welcome-email"): "session",
    ("POST", f"{A}/settings/welcome-email/test"): "session",
    ("GET", f"{A}/settings/acme/tokens"): "session",
    ("POST", f"{A}/settings/acme/tokens"): "session",
    ("DELETE", f"{A}/settings/acme/tokens/{{token_id}}"): "session",

    # --- admin: get_admin_user (Token nur mit allow_admin) ---------------------------------
    ("GET", f"{A}/auth/users"): "admin",
    ("GET", f"{A}/auth/users/{{user_id}}/zones"): "admin",
    ("POST", f"{A}/zones"): "admin",
    ("DELETE", f"{A}/zones/{ZP}"): "admin",
    ("POST", f"{A}/zones/import/preview"): "admin",
    ("POST", f"{A}/zones/import"): "admin",
    ("GET", f"{A}/audit-log"): "admin",
    ("GET", f"{A}/audit-log/export"): "admin",
    ("POST", f"{A}/templates"): "admin",
    ("PUT", f"{A}/templates/{{template_id}}"): "admin",
    ("DELETE", f"{A}/templates/{{template_id}}"): "admin",
    ("GET", f"{A}/metrics"): "admin",  # JSON-Laufzeitmetriken

    # --- zone: get_current_user + assert_zone_access (Scope + Methodenregel) ---------------
    ("GET", f"{A}/zones/{ZP}/detail"): "zone",
    ("PUT", f"{A}/zones/{ZP}"): "zone",
    ("POST", f"{A}/zones/{ZP}/notify"): "zone",
    ("GET", f"{A}/zones/{ZP}/export"): "zone",
    ("GET", f"{A}/records/{ZP}"): "zone",
    ("POST", f"{A}/records/{ZP}/bulk"): "zone",
    ("POST", f"{A}/records/{ZP}"): "zone",
    ("DELETE", f"{A}/records/{ZP}/delete"): "zone",
    ("PUT", f"{A}/records/{ZP}"): "zone",
    ("GET", f"{A}/dnssec/{ZP}/keys"): "zone",
    ("GET", f"{A}/dnssec/{ZP}/keys/{{key_id}}"): "zone",
    ("POST", f"{A}/dnssec/{ZP}/enable"): "zone",
    ("POST", f"{A}/dnssec/{ZP}/disable"): "zone",
    ("POST", f"{A}/dnssec/{ZP}/keys/{{key_id}}/activate"): "zone",
    ("POST", f"{A}/dnssec/{ZP}/keys/{{key_id}}/deactivate"): "zone",
    ("DELETE", f"{A}/dnssec/{ZP}/keys/{{key_id}}"): "zone",
    ("GET", f"{A}/dnssec/{ZP}/ds"): "zone",
}

# Platzhalter: Route entsteht erst in W0-INT-BE2b (main.py, F13)
PLANNED = {
    ("GET", "/metrics"),
}

# Zonenbezogene Admin-Routen: zusaetzlich assert_token_scope( im Endpunkt (F14 5.6)
TOKEN_SCOPE_REQUIRED = {
    ("POST", f"{A}/zones"),
    ("DELETE", f"{A}/zones/{ZP}"),
    ("POST", f"{A}/zones/import/preview"),
    ("POST", f"{A}/zones/import"),
}

# Bekannte Luecken bis W0-INT-BE2b (zones.py und main.py gehoeren in 0b-3 BE2b). Nach deren Umbau meldet
# test_route_policy eine Warnung; dann diese Eintraege entfernen (Integrator am Wellenende 0b).
TRANSITIONAL = {
    ("GET", f"{A}/metrics"): "W0-INT-BE2b: main.py stellt auf Depends(get_admin_user) um (B.13)",
    ("POST", f"{A}/zones"): "W0-INT-BE2b: assert_token_scope(zone_data.name, write=True) in create_zone",
    ("DELETE", f"{A}/zones/{ZP}"): "W0-INT-BE2b: assert_token_scope(zone_id, write=True) in delete_zone",
    ("POST", f"{A}/zones/import/preview"): "W0-INT-BE2b: assert_token_scope(import_data.name) in import_zone_preview",
    ("POST", f"{A}/zones/import"): "W0-INT-BE2b: assert_token_scope(import_data.name, write=True) in import_zone",
}
