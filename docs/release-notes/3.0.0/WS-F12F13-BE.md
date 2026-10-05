### Propagations-Check und Prometheus-Metriken (WS-F12F13-BE, Backend zu F12/F13)

- **Propagations-Check je Zone:** `GET /api/v1/zones/{server}/{zone}/propagation` zeigt, ob eine Aenderung ueberall
  angekommen ist: Panel-Server (Serial, NOTIFY-Stand ueber die PowerDNS-API), die autoritativen Nameserver der Zone
  und oeffentliche Resolver. Optional mit Vergleich eines einzelnen Records (`name`, `type`) und des kompletten
  Zoneninhalts zwischen den Panel-Servern (`content=true`). Lesezugriff auf die Zone genuegt; API-Tokens mit
  Zonen-Scope duerfen pruefen. Hoechstens 8 Sekunden je Pruefung, 10 Pruefungen je Benutzer und Minute, Ergebnisse
  werden 10 Sekunden zwischengespeichert.
- **Getrennte Datenbanken:** Liegt eine Zone auf mehreren schreibbaren PowerDNS-Servern mit eigener Datenbank, gelten
  abweichende Serials nicht mehr als Fehler – entscheidend ist, ob der Zoneninhalt gleich ist (Hinweis
  "getrennte Datenbank"). Nameserver gelten als aktuell, wenn sie die Serial eines der Panel-Server ausliefern.
- **Externe DNS-Abfragen nur nach Freischaltung:** Nameserver und Resolver werden erst gefragt, wenn ein Admin es unter
  Einstellungen -> Monitoring erlaubt (`GET/PUT /api/v1/settings/propagation`; Resolver nur als IP-Adressen,
  hoechstens 10, Standard 1.1.1.1, 8.8.8.8, 9.9.9.9). Ohne Freischaltung werden nur die Panel-Server verglichen.
  Der Backend-Container braucht dafuer ausgehend UDP/TCP 53.
- **Prometheus-Metriken einschalten:** Unter Einstellungen -> Monitoring einen Scrape-Token erzeugen (wird nur einmal
  angezeigt und verschluesselt gespeichert) und `/metrics` aktivieren (`/api/v1/settings/metrics`,
  `/api/v1/settings/metrics/token`). Alternativ `METRICS_TOKEN` (mind. 24 Zeichen) in der Umgebung setzen; die
  Panel-Einstellung ist dann gesperrt. Neue Metriken u. a. `pdnsmgr_propagation_checks_total`,
  `pdnsmgr_migration_errors`, `pdnsmgr_background_task_running`, `pdnsmgr_secrets_unreadable_reads`; nicht geladene
  PowerDNS-Server erscheinen mit `pdnsmgr_pdns_server_up 0`.
- **Statusuebersicht fuer Admins:** `GET /api/v1/settings/monitoring/status` zeigt Hintergrund-Aufgaben,
  Migrationsfehler, nicht geladene Server und den Zustand der Verschluesselung (nur mit Browser-Anmeldung).
- Alle Aenderungen an diesen Einstellungen stehen im Audit-Log (`PROPAGATION_SETTINGS_UPDATE`,
  `METRICS_SETTINGS_UPDATE`, `METRICS_TOKEN_CREATE`, `METRICS_TOKEN_DELETE`), nie mit dem Token selbst.
