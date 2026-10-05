### Tests und Qualitaetssicherung (W0-TESTINFRA)

- Neue End-to-End-Tests (`scripts/e2e/`): Jede Version wird gegen echte Dienste geprueft – MariaDB 11, zwei
  PowerDNS-Server und einen Webhook-Empfaenger. Geprueft werden eine Neuinstallation und das **Update einer
  2.4.1-Datenbank** (Benutzer mit 2FA, Server, Panel-Tokens, Webhooks, Zonenrechte und alter Audit-Verlauf bleiben
  nutzbar, auch nach einem zweiten Start).
- Die Tests laufen vollstaendig isoliert (eigene Containernamen, eigenes Netz, keine festen Ports) und koennen neben
  einer laufenden Installation ausgefuehrt werden.
- Die automatischen Pruefungen bei jedem Pull Request umfassen jetzt auch die Vollstaendigkeit der Uebersetzungen
  (alle 6 Sprachen, Pluralformen, Platzhalter), Frontend-Unit-Tests und einen eigenen Datenbank-Migrationstest.
- Fuer Betreiber ist nichts zu tun.
