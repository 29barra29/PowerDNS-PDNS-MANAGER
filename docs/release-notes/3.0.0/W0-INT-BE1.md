### Datenbank-Migration und Konfiguration fuer 3.0 (W0-INT-BE1, Grundlage)

- **Automatische Migration beim Start:** Das Backend bringt eine 2.x-Datenbank beim ersten 3.0-Start selbst auf
  den neuen Stand (neue Tabellen fuer Webhook-Zustellungen und DynDNS-Tokens, neue Spalten fuer Benutzer, Webhooks,
  Audit-Log und API-Tokens, breitere Spalten fuer verschluesselte Geheimnisse). Die Migration ist wiederholbar und
  aendert bei weiteren Starts nichts mehr. Spalten werden nur angepasst, wenn es noetig ist (kein Tabellenumbau bei
  jedem Start).
- **Kein Start mit halbem Schema:** Fehlt nach der Migration eine benoetigte Spalte (z. B. weil dem Datenbank-Benutzer
  das Recht fuer `ALTER TABLE` fehlt), bricht der Start mit einer klaren Meldung ab, statt spaeter Aenderungen ohne
  Audit-Eintrag zu speichern. Nicht kritische Schritte (Indizes, Nachtrag alter Audit-Eintraege) werden geloggt und
  beim naechsten Start wiederholt.
- **Bestehende API-Tokens behalten ihr Verhalten:** Tokens von Administratoren duerfen weiterhin Admin-Funktionen
  nutzen; in 2.x geloeschte (inaktive) Tokens gelten als endgueltig widerrufen und lassen sich nicht reaktivieren.
- **Audit-Log:** Eintraege speichern jetzt Zone, Benutzername und Client-IP zum Zeitpunkt der Aktion (bleibt auch nach
  dem Loeschen eines Benutzers lesbar). Alte Eintraege bekommen die Zone einmalig nachgetragen. Ein fehlgeschlagener
  Audit-Eintrag bricht die eigentliche Aenderung nicht mehr ab. Der CSV-Export ist gegen Formel-Injection in
  Tabellenprogrammen geschuetzt.
- **Neue Einstellungen in `.env`/`compose.yaml`:** `SECRET_ENCRYPTION_KEY` (+ `_PREVIOUS`, `_FILE`) fuer die
  Verschluesselung gespeicherter Geheimnisse, `BACKGROUND_WORKERS_ENABLED` (Webhook-Zustellung und Audit-Bereinigung
  im Hintergrund), `METRICS_TOKEN` (optionaler Prometheus-Scrape-Token) und `SSO_ALLOW_INSECURE` (nur fuer Tests).
- **Geaenderte Voreinstellungen:** Der feste DNS-Eintrag `8.8.8.8/8.8.4.4` in `compose.yaml` entfaellt – der Container
  nutzt die DNS-Aufloesung des Docker-Hosts (interne Server-Namen bleiben aufloesbar; eigene Resolver per
  `compose.override.yaml`). Die Standard-Content-Security-Policy erlaubt keine Google-Fonts-Server mehr (die Schrift wird
  lokal ausgeliefert). Wer `CONTENT_SECURITY_POLICY` selbst gesetzt hat, behaelt seinen Wert.
