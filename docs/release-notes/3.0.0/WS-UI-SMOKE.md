### Automatische Browser-Tests der Oberfläche (WS-UI-SMOKE)

#### Neu / Behoben
- **UI-Smoke-Tests:** Die Oberfläche wird jetzt automatisch im Browser geprüft (Playwright mit Chromium in einem
  Docker-Container, gegen die isolierte E2E-Umgebung). Abgedeckt sind Anmeldung/Abmeldung, Sprachwechsel,
  abgelaufene Sitzung, Einrichtungsassistent, Zonen und Records (inkl. Verteilung auf weitere Server und
  Fehleranzeigen), Bulk- und Text-Editor mit PTR-Pflege, Änderungsverlauf und Zurücksetzen, DNSSEC (Aktivieren,
  DS-Assistent, Schlüssel, Schlüsselwechsel), Export und NOTIFY, Benutzerverwaltung (Passwortzwang, Zufallspasswort,
  2FA-Reset, Zugänge widerrufen, SSO-Konten), alle Einstellungs-Reiter, SSO mit Passwort-Bestätigung, DynDNS,
  Webhooks mit echter Zustellung, API-Tokens, Audit-Log, Propagation sowie Tastaturbedienung der Dialoge.
- Jede besuchte Seite wird auf Fehler in der Browser-Konsole geprüft; Anfragen an fremde Server (z. B. Web-Schriften)
  lassen den Test scheitern.

#### Geändert / Breaking
- Keine (nur Test-Infrastruktur, keine Änderung am Panel).

#### API
- Keine.

#### Nach dem Update prüfen
- Nichts. Für Entwickler: `scripts/e2e/run-e2e.sh --ui` startet die Tests lokal (nur Docker nötig); im GitHub-Workflow
  „Test and Lint“ laufen sie im manuell gestarteten E2E-Job mit (Schalter „e2e_ui“).

#### Doku-Impact
- `CONTRIBUTING.md`/`scripts/e2e/README.md`: UI-Smoke-Tests (`scripts/e2e/ui/README.md`) und `run-e2e.sh --ui`
  erwähnen; neue Dialoge bzw. Abläufe bekommen eine Spec unter `scripts/e2e/ui/tests/`.
- Website: keine Nutzer-Seite betroffen.
