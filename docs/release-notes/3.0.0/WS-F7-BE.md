### Record-Historie, Zuruecksetzen und Audit-Log (WS-F7-BE, Backend zu F7)

#### Neu
- **Vorher/Nachher fuer jede Record-Aenderung:** Anlegen, Aendern, Loeschen und Bulk speichern im Audit-Log den
  vollstaendigen Zustand der betroffenen RRsets vor und nach der Aenderung (TTL, Werte, deaktiviert, Kommentare;
  `details.version = 2`). Der Vorher-Zustand stammt vom Server in der URL, der Nachher-Zustand wird nach dem
  Schreiben erneut gelesen. Die bisherigen Detailfelder (`type`, `old`, `new`, `content`, `records`,
  `created`/`deleted`) bleiben erhalten.
- **Zonenverlauf und Zuruecksetzen per API:** Lesen darf jeder mit Zonenrecht, zuruecksetzen nur mit Schreibrecht.
  Wurde die Zone seitdem erneut geaendert, antwortet der Rollback mit `409` und setzt erst mit `force: true` zurueck;
  RRsets, die bereits wieder auf dem Vorher-Stand sind, zaehlen dabei nicht als Konflikt und werden nicht erneut
  geschrieben. SOA, DNSSEC-Records (inkl. DS) und `_acme-challenge` werden nie zurueckgesetzt. Neues
  Webhook-Ereignis `record.rollback`.
- **Webhooks fuer Records:** `record.created`, `record.updated` und `record.deleted` enthalten zusaetzlich
  `changes` (vorher/nachher), `fanout` (Ergebnis je Server) sowie `ttl`/`added` bzw. `old_content`/`new_content`.
- **Audit-Log:** Filter nach Zone, Benutzer, Status, Zeitraum, Volltext und mehreren Aktionen; Gesamtzahl fuer das
  Blaettern; je Eintrag Zone, Benutzername und die Verweise „setzt zurueck“ / „zurueckgesetzt durch“. Der CSV-Export
  nutzt dieselben Filter und schuetzt alle Zellen gegen Formel-Injection in Tabellenprogrammen.
- **Aufbewahrung:** 0 = unbegrenzt (Standard) oder 7–3650 Tage, nur Admins mit Browser-Anmeldung. Die Bereinigung
  laeuft stuendlich im Hintergrund und wird selbst protokolliert (`AUDIT_PURGE`, Einstellung `AUDIT_SETTINGS_UPDATE`).
- **Datenschutz im Zonenverlauf:** Nicht-Admins sehen keine Client-IPs, keine Token-Kennungen und keine
  PowerDNS-Fehlertexte; Eintraege aus der Zeit vor einer endgueltigen Loeschung und Neuanlage der Zone sind fuer
  sie ausgeblendet. Die Volltextsuche (`q`) durchsucht fuer Nicht-Admins nur Record-Namen, -Typen und -Werte
  (sowie Kommentare), nie die ausgeblendeten Angaben oder Fehlertexte.

#### Geändert / Breaking
- **Mehrere Server:** Aenderungen werden zuerst auf dem Server aus der URL geschrieben; scheitert er, bleiben die
  weiteren Server unberuehrt. Bei Einzel-Aenderungen und Bulk wird jeder weitere Server auf Basis seines eigenen
  Stands geaendert – Werte, die nur dort existieren, gehen nicht mehr verloren. Bulk ist je Server atomar (ein
  Schreibvorgang). Das Zuruecksetzen setzt dagegen alle schreibbaren Server auf den Vorher-Zustand des Servers aus
  der URL. Bricht die Verbindung nach dem Senden ab, prueft das Panel per erneutem Lesen nach, ob die Aenderung
  angekommen ist.
- Fehlt die Zone auf dem Server aus der URL, antworten die Record-Endpunkte mit `404` statt `502`.
- Wird beim Loeschen eines einzelnen Werts dieser Wert auf einem weiteren Server nicht gefunden, meldet das Ergebnis
  dort `skipped (no matching content)` statt eines Fehlers.
- `details.zone` endet jetzt immer mit Punkt; Record-Eintraege haben `details.version = 2`.
- Eintraege aus Versionen vor 3.0 erscheinen im Verlauf, lassen sich aber nicht zuruecksetzen. Beim ersten Start
  wird die Zone bestehender Eintraege einmalig nachgetragen.

#### API
- Neu: `GET /api/v1/zones/{server}/{zone}/history` (Filter: Aktion, Typ, Name, Benutzer, Status, Zeitraum,
  Volltext ab 2 Zeichen; mit `total`), `GET …/history/{id}`, `GET …/history/{id}/rollback-preview`,
  `POST …/history/{id}/rollback` (Body optional, `{"force": true}`; `409` mit
  `detail = {message, code: "rollback_conflict", conflicts, already_reverted_by}`; bereits erledigt:
  `200` mit `details.noop = true`).
- Neu: `GET /api/v1/audit-log/{id}`, `GET|PUT /api/v1/audit-log/settings` (nur Admin mit Browser-Anmeldung, nicht per
  API-Token).
- Geaendert: `GET /api/v1/audit-log` liefert `total` und kennt `zone`, `user_id`, `status`, `date_from`, `date_to`,
  `q`, `full` und mehrere Aktionen per Komma; je Eintrag `zone_name`, `username`, `revert_of_id`, `reverted_by_id`.
  `GET /api/v1/audit-log/export` mit denselben Filtern und den zusaetzlichen Spalten `zone_name;username;revert_of_id`
  am Ende.
- Neues Webhook-Ereignis `record.rollback`; `record.created|updated|deleted` mit erweiterten Daten (siehe Neu).

#### Nach dem Update prüfen
- Audit-Log → „Aufbewahrung“: Dauer festlegen (Standard unbegrenzt; die Tabelle waechst sonst weiter). Das Loeschen
  alter Eintraege entfernt auch deren Verlauf und die Moeglichkeit zum Zuruecksetzen.
- Skripte, die bei fehlender Zone auf `502` reagieren, auf `404` umstellen.
- Webhook-Empfaenger fuer `record.*`: die zusaetzlichen Felder schaden nicht, `record.rollback` ggf. abonnieren.

#### Website-Seiten (DE/EN)
- **neu** `features/record-historie` („Record-Historie & Rollback“ / „Record history & rollback“): was gespeichert wird
  (Vorher/Nachher vom Server aus der URL), wer was darf (Lesen: Verlauf, Verwalten: Zuruecksetzen, Admin:
  Audit-Log), Ablauf mit Vorschau, Konflikt und `force`, Ausnahmen (SOA, DNSSEC inkl. DS, `_acme-challenge`),
  Verhalten bei mehreren Servern, Alt-Eintraege vor 3.0, Aufbewahrung, curl-Beispiele, Webhook `record.rollback`,
  Grenzen (kein Zonen-/DNSSEC-Rollback, Aenderungen ausserhalb des Panels nicht im Verlauf, aber in der
  Konfliktpruefung). Navigation „Features im Detail“ nach `audit-log` (DE und EN).
- `features/audit-log`: Aktionstabelle um `RECORD_ROLLBACK`, `AUDIT_PURGE`, `AUDIT_SETTINGS_UPDATE`; JSON-Beispiel auf
  Format v2 (`zone_name`, `username`, `revert_of_id`, `details.version/changes`); Filter-Parameter und `total`;
  CSV-Spalten; Abschnitt „Aufbewahrung“ durch die eingebaute Einstellung ersetzen.
- `features/zonen-records`: Absatz „Verlauf“ mit Link auf die neue Seite.
- `features/benutzer-rollen`: Rechte-Tabelle um „Verlauf sehen (Lesen)“ und „Aenderungen zuruecksetzen (Verwalten)“.
- `features/webhooks`: Ereignis `record.rollback`, erweiterte Daten der `record.*`-Ereignisse.
- `features/panel-api`: neue Endpunkte, Statuscode `409` („Konflikt, z. B. Rollback“), 404 statt 502 bei fehlender
  Zone; `resource_type=RECORD` → `record` korrigieren.
- Veraltet: „Das Audit-Log zeigt die letzten 200 Eintraege, Filter gibt es dort nicht“ (DE), „Audit-Events per
  Webhook an einen zentralen Log-Server“ (Webhooks liefern nur DNS-Ereignisse, keinen Audit-Stream).
