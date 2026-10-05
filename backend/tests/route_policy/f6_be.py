"""Route-Policy WS-F6-BE: neue Webhook-Routen (F6 3.5–3.8, Anhang A3).

Besitzer: WS-F6-BE. Liste, Anlegen, Aendern und Loeschen stehen (unveraendert) in ``base.py``.
Test senden, Zustellprotokoll und Retry sind Session-only: ein API-Token darf weder Daten an ein Ziel schicken
noch Protokolle mit Zonennamen/Antworten lesen.
"""

A = "/api/v1"
W = f"{A}/auth/me/webhooks/{{webhook_id}}"

ROUTE_POLICY = {
    ("POST", f"{W}/test"): "session",
    ("GET", f"{W}/deliveries"): "session",
    ("GET", f"{W}/deliveries/{{delivery_pk}}"): "session",
    ("POST", f"{W}/deliveries/{{delivery_pk}}/retry"): "session",
}
