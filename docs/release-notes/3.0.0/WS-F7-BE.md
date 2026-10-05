### Record-Historie, Zuruecksetzen und Audit-Log (WS-F7-BE, Backend zu F7)

- **Vorher/Nachher fuer jede Record-Aenderung:** Anlegen, Aendern, Loeschen und Bulk speichern im Audit-Log den
  vollstaendigen Zustand der betroffenen RRsets vor und nach der Aenderung (TTL, Werte, deaktiviert, Kommentare;
  `details.version = 2`). Der Vorher-Zustand stammt vom Server in der URL, der Nachher-Zustand wird nach dem
  Schreiben erneut gelesen. Die bisherigen Detailfelder (`type`, `old`, `new`, `content`, `records`,
  `created`/`deleted`) bleiben erhalten.
- **Zonenverlauf und Zuruecksetzen per API:** `GET /api/v1/zones/{server}/{zone}/history` (Filter: Aktion, Typ,
  Name, Benutzer, Status, Zeitraum, Volltext; mit Gesamtzahl), `…/history/{id}`, `…/history/{id}/rollback-preview`
  und `POST …/history/{id}/rollback`. Lesen darf jeder mit Zonenrecht, zuruecksetzen nur mit Schreibrecht. Wurde die
  Zone seitdem erneut geaendert, antwortet der Rollback mit `409` und setzt erst mit `force: true` zurueck. SOA,
  DNSSEC-Records (inkl. DS) und `_acme-challenge` werden nie zurueckgesetzt. Neues Webhook-Ereignis
  `record.rollback`.
- **Mehrere Server:** Aenderungen werden zuerst auf dem Server aus der URL geschrieben; scheitert er, bleiben die
  weiteren Server unberuehrt. Bei Einzel-Aenderungen und Bulk wird jeder weitere Server auf Basis seines eigenen
  Stands geaendert – Werte, die nur dort existieren, gehen nicht mehr verloren. Das Zuruecksetzen setzt dagegen alle
  schreibbaren Server auf den Vorher-Zustand des Servers aus der URL. Bricht die Verbindung nach dem Senden ab,
  prueft das Panel per erneutem Lesen nach, ob die Aenderung angekommen ist.
- **Webhooks fuer Records:** `record.created`, `record.updated` und `record.deleted` enthalten zusaetzlich
  `changes` (vorher/nachher), `fanout` (Ergebnis je Server) sowie `ttl`/`added` bzw. `old_content`/`new_content`.
- **Audit-Log:** `GET /api/v1/audit-log` liefert `total` und kennt die Filter `zone`, `user_id`, `status`,
  `date_from`, `date_to`, `q` und mehrere Aktionen per Komma; je Eintrag `zone_name`, `username`,
  `revert_of_id`, `reverted_by_id`. Neu: `GET /api/v1/audit-log/{id}`. Der CSV-Export nutzt dieselben Filter, hat
  am Ende die Spalten `zone_name;username;revert_of_id` und schuetzt alle Zellen gegen Formel-Injection in
  Tabellenprogrammen.
- **Aufbewahrung:** `GET/PUT /api/v1/audit-log/settings` (nur Admin im Browser, nicht per API-Token): 0 = unbegrenzt
  (Standard) oder 7–3650 Tage. Die Bereinigung laeuft stuendlich im Hintergrund und wird selbst protokolliert.
- **Datenschutz im Zonenverlauf:** Nicht-Admins sehen keine Client-IPs, keine Token-Kennungen und keine
  PowerDNS-Fehlertexte; Eintraege aus der Zeit vor einer endgueltigen Loeschung und Neuanlage der Zone sind fuer
  sie ausgeblendet. Die Volltextsuche (`q`) durchsucht fuer Nicht-Admins nur Record-Namen, -Typen und -Werte
  (sowie Kommentare), nie die ausgeblendeten Angaben oder Fehlertexte.
- **Hinweis fuer bestehende Installationen:** Eintraege aus Versionen vor 3.0 erscheinen im Verlauf, lassen sich
  aber nicht zuruecksetzen. Ist eine Aenderung am Server aus der URL nicht moeglich, weil die Zone dort fehlt,
  antworten die Record-Endpunkte jetzt mit `404` statt `502`.
