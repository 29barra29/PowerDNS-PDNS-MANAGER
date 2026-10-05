### Propagations-Check und Prometheus-Metriken (WS-F12F13-BE, Backend zu F12/F13)

#### Neu
- **Propagations-Check je Zone:** zeigt, ob eine Aenderung ueberall angekommen ist: Panel-Server (Serial,
  NOTIFY-Stand ueber die PowerDNS-API), die autoritativen Nameserver der Zone und oeffentliche Resolver. Optional mit
  Vergleich eines einzelnen Records (`name`, `type`) und des kompletten Zoneninhalts zwischen den Panel-Servern
  (`content=true`). Lesezugriff auf die Zone genuegt; API-Tokens mit Zonen-Scope duerfen pruefen. Hoechstens
  8 Sekunden je Pruefung, 10 Pruefungen je Benutzer und Minute, Ergebnisse werden 10 Sekunden zwischengespeichert.
- **Getrennte Datenbanken:** Liegt eine Zone auf mehreren schreibbaren PowerDNS-Servern mit eigener Datenbank, gelten
  abweichende Serials nicht mehr als Fehler – entscheidend ist, ob der Zoneninhalt gleich ist (Hinweis
  "getrennte Datenbank"). Nameserver gelten als aktuell, wenn sie die Serial eines der Panel-Server ausliefern.
- **Prometheus-Metriken:** Endpunkt `/metrics` (ohne API-Praefix, Textformat), nur mit Scrape-Token. Token im Panel
  erzeugen (wird nur einmal angezeigt und verschluesselt gespeichert) oder `METRICS_TOKEN` (mind. 24 Zeichen) setzen.
  Metriken u. a. zu HTTP, PowerDNS-API, Logins, Webhooks, `pdnsmgr_propagation_checks_total`,
  `pdnsmgr_migration_errors`, `pdnsmgr_background_task_running`, `pdnsmgr_secrets_unreadable_reads`; nicht geladene
  PowerDNS-Server erscheinen mit `pdnsmgr_pdns_server_up 0`.
- **Statusuebersicht fuer Admins:** Hintergrund-Aufgaben, Migrationsfehler, nicht geladene Server und Zustand der
  Verschluesselung (nur mit Browser-Anmeldung).
- Alle Aenderungen an diesen Einstellungen stehen im Audit-Log (`PROPAGATION_SETTINGS_UPDATE`,
  `METRICS_SETTINGS_UPDATE`, `METRICS_TOKEN_CREATE`, `METRICS_TOKEN_DELETE`), nie mit dem Token selbst.

#### Geändert / Breaking
- **Externe DNS-Abfragen nur nach Freischaltung:** Nameserver und Resolver werden erst gefragt, wenn ein Admin es unter
  Einstellungen → Monitoring erlaubt (Resolver nur als IP-Adressen, hoechstens 10, Standard 1.1.1.1, 8.8.8.8,
  9.9.9.9). Ohne Freischaltung werden nur die Panel-Server verglichen. Der Backend-Container braucht dafuer ausgehend
  UDP/TCP 53; die Resolver sehen die abgefragten Zonennamen.
- `/metrics` ist standardmaessig aus (404). Ist `METRICS_TOKEN` gesetzt, ist die Panel-Einstellung gesperrt.
- Neue Umgebungsvariable `METRICS_TOKEN` (optional). Der bestehende JSON-Endpunkt `/api/v1/metrics` bleibt.

#### API
- Neu: `GET /api/v1/zones/{server}/{zone}/propagation?name=&type=&content=` (Lesen),
  `GET|PUT /api/v1/settings/propagation`, `GET|PUT /api/v1/settings/metrics`,
  `POST|DELETE /api/v1/settings/metrics/token`, `GET /api/v1/settings/monitoring/status` (Admin, nur
  Browser-Anmeldung) sowie `GET /metrics` (Scrape-Token als `Authorization: Bearer …`).

#### Nach dem Update prüfen
- Wer `/metrics` nutzt: im Reverse-Proxy nur den Prometheus-Server zulassen.
- Fuer den Propagations-Check mit externen Abfragen: ausgehend Port 53 (UDP/TCP) vom Backend-Container erlauben und
  die Freischaltung bewusst setzen.
- Einstellungen → Monitoring → Systemstatus: keine Migrationsfehler, keine nicht geladenen Server.

#### Website-Seiten (DE/EN)
- **neu** `features/propagation` („Propagations-Check“ / „Propagation check“): Zweck, Quellen und Datenbasis,
  ausgelieferte vs. gespeicherte Serial (SOA-EDIT), getrennte vs. gemeinsame Datenbank, Record- und Inhaltsvergleich,
  Freischaltung externer Abfragen (Port 53, Resolver sehen Zonennamen), Status- und Hinweis-Tabelle, Grenzen
  (LUA/ALIAS, DNSSEC-Typen, IPv6 im Container). Navigation nach `multi-server`.
- **neu** `features/monitoring` („Monitoring (Prometheus)“): Aktivieren (Panel-Token, `METRICS_TOKEN`),
  `scrape_configs` mit `credentials_file`, Reverse-Proxy-Beschraenkung, Metrik-Tabelle, PromQL- und Alert-Beispiele,
  `/health` fuer Uptime-Checks. Navigation „Erweitert“ nach `webhooks`.
- `faq`: „Das Backend selbst kontaktiert keine externen Dienste“ ist **veraltet** – mit Freischaltung fragt es die
  Nameserver der Zone und die eingestellten Resolver.
- `troubleshooting` („Records im Panel, dig zeigt nichts“): zuerst den Propagations-Check nutzen.
- `features/multi-server`: „Serials bei getrennten Datenbanken“ mit Link auf `features/propagation`.
- `features/dnssec` (Verifikation) und `features/acme` (`_acme-challenge` im Propagations-Check pruefen): Hinweise.
- `sicherheit`: Abschnitte „/metrics absichern“ und „Ausgehende DNS-Abfragen (Propagations-Check)“.
- `konfiguration`: `METRICS_TOKEN` in Tabelle und Beispiel-`.env`; „Was nicht in der .env steht“ um Propagations- und
  Metrik-Einstellungen.
- `features/audit-log`: die vier neuen Aktionen.
