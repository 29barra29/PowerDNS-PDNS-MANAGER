### Start, Webhook-Warteschlange, Health und Metriken (W0-INT-BE2b, Grundlage fuer F6/F13)

- **Webhooks gehen nicht mehr verloren:** Jede Aenderung schreibt ihr Webhook-Ereignis in derselben Transaktion wie
  Aenderung und Audit-Eintrag in eine Warteschlange (`webhook_deliveries`): Was gespeichert ist, wird auch gemeldet –
  was zurueckgerollt wird, nicht. Die Payload hat Version 2 (u. a. `event_id`, `delivery_id`, `actor`, `zone`,
  `server`, `audit_log_id`; die bisherigen Felder bleiben in `data`), die Signatur `X-DNS-Manager-Signature` wird
  ueber genau die gespeicherten Bytes gebildet. Die fehlerhafte Direktzustellung aus 2.4.x (Ereignisse wurden wegen
  eines fehlenden `await` nie gesendet) ist entfernt; die Zustellung aus der Warteschlange mit Wiederholungen und
  Protokoll liefert F6.
- **Neue Ereignisse:** `zone.created`, `zone.updated`, `zone.deleted` sowie `dnssec.enabled`, `dnssec.disabled`,
  `dnssec.key_activated`, `dnssec.key_deactivated`, `dnssec.key_deleted` (zusaetzlich zu `record.*` und
  `zone.imported`).
- **Start in fester Reihenfolge:** Datenbank-Migration, dann Verschluesselung der Geheimnisse, dann Initial-Admin,
  PowerDNS-Server und Hintergrund-Aufgaben. Fehlt der Schluessel oder ist das Schema unvollstaendig, bricht der Start
  mit einer klaren Meldung im Log ab. PowerDNS-Server mit nicht lesbarem API-Key werden nicht geladen und im Log
  genannt.
- **`/health`:** Von aussen nur noch `status`, `database` und `servers`. Details (Verschluesselung, Migrationsfehler,
  Hintergrund-Aufgaben) gibt es nur bei Abfrage aus dem Container selbst (`127.0.0.1`), z. B. fuer `update.sh`.
- **Prometheus:** Neuer Endpunkt `GET /metrics` (nur mit Bearer-Scrape-Token; solange Metriken nicht eingeschaltet
  sind, antwortet er wie eine unbekannte Seite mit 404). `GET /api/v1/metrics` verlangt bei API-Tokens die
  Admin-Freigabe.
- **API-Tokens und Zonen:** Die Zonenliste zeigt einem eingeschraenkten Token nur seine Zonen; Zonen anlegen, loeschen
  und importieren pruefen zusaetzlich den Zonen-Scope. Wird eine Zone endgueltig geloescht, verschwindet sie auch aus
  den Scopes der Tokens.
- **Records:** Neuer Typ `LUA` (vorerst nur fuer Administratoren; Einstellung folgt mit F15). Generische
  Typangaben wie `TYPE65402` werden abgelehnt. Record-Endpunkte nehmen das Feld `manage_ptr` bereits an (Wirkung
  folgt mit F11).
