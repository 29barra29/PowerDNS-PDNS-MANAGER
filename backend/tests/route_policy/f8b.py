"""Route-Policy WS-F8b (Welle 1): keine neuen Routen.

F8b aendert nur die Validierung bestehender Vorlagen-Routen (TTL-Grenzen 60..604800, F8 3.8); deren
Einordnung (``/api/v1/templates*``: GET user, POST/PUT/DELETE admin) steht unveraendert in ``base.py``.
"""

ROUTE_POLICY: dict[tuple[str, str], str] = {}
