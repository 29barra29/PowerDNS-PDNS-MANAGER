"""Gemeinsame Test-Fakes (In-Memory-PowerDNS, Fake-DB-Session).

``backend/tests`` ist kein Paket; pytest legt das Testverzeichnis (rootdir-Modus
"prepend") auf ``sys.path``. Import in Tests: ``from fakes.pdns import FakePowerDNSClient``.
"""
