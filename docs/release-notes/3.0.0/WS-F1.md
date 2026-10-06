### Bulk-Editor in der Zonenansicht (WS-F1, F1)

#### Neu
- **Mehrfachauswahl:** In den Record-Tabellen lassen sich Einträge per Checkbox auswählen (Kopf-Checkbox je Typ).
  Eine Leiste am unteren Rand bietet *Löschen*, *TTL setzen…*, *Deaktivieren*, *Aktivieren* und *Als Text
  bearbeiten*. SOA und von PowerDNS erzeugte DNSSEC-Records sind nicht auswählbar. Nur sichtbar mit Schreibrecht
  auf einem Server mit „Speichern: Ja“.
- **Text-Editor (BIND-Format):** Neuer Knopf „Text-Editor“ im Kopf der Zonenansicht. Drei Modi: *Ergänzen*
  (Standard, löscht nie), *Ersetzen* (genannte RRsets komplett ersetzen) und *Geladene RRsets bearbeiten*
  (zusätzlich werden geladene RRsets gelöscht, deren Zeilen entfernt wurden). `$ORIGIN`, `$TTL`, Kommentare,
  mehrzeilige Klammern, LUA/ALIAS und deaktivierte Werte (`;@disabled`) werden verstanden; Fehler erscheinen mit
  Zeilennummer, ein Klick springt in die Zeile.
- **Vorschau vor jedem Speichern:** Jede Sammeländerung zeigt zuerst je RRset vorher/nachher (neue Werte grün,
  entfernte rot, TTL- und Aktiv-Wechsel), Zusammenfassung, Hinweise und blockierende Probleme (CNAME-Konflikte,
  Apex-NS, SOA, LUA-Berechtigung). Löschungen müssen ausdrücklich bestätigt werden. Wurde die Zone zwischen
  Vorschau und Speichern geändert, wird nichts überschrieben (Hinweis „Vorschau neu laden“).
- **PTR-Pflege:** Bei A/AAAA-Änderungen bietet die Vorschau die Option „PTR in Reverse-Zone mitpflegen“; beim
  Löschen einzelner A/AAAA-Records gilt die zuletzt gewählte Einstellung der Zone. Das Panel sendet immer genau
  den angezeigten Wert. Nach dem Speichern stehen gesetzte/entfernte PTRs an der Erfolgsmeldung, übersprungene
  (z. B. fremder PTR, fehlendes Recht auf die Reverse-Zone) je IP mit Begründung in einem gelben Hinweis.

#### Geändert
- **Bulk auf mehreren Servern:** je Server genau eine atomare Änderung; zuerst der Server aus der URL, scheitert er,
  bleiben die anderen unberührt. Fehlen auf einem weiteren Server einzelne Werte, ist das kein Fehler mehr (Hinweis
  „übersprungen“). Teilen sich zwei Server eine Datenbank, wird nicht doppelt geschrieben. Verbindungsabbrüche
  zu einem weiteren Server brechen die Änderung nicht mehr ab.
- **Audit/Verlauf:** `BULK_UPDATE` enthält jetzt vorher/nachher je RRset und lässt sich im Zonenverlauf
  zurücksetzen. Webhook `record.bulk` enthält zusätzlich `source`, `mode`, `changes` (höchstens 200),
  `changes_total` und `fanout`.

#### Breaking
- `POST /api/v1/records/{server}/{zone}/bulk`: `create`-Einträge mit leerer `records`-Liste werden mit `422`
  abgelehnt (zum Löschen `delete` verwenden). Eine leere Anfrage ergibt `422` statt `400`; `create` und
  `delete` ohne `content` für dasselbe RRset in einer Anfrage sind nicht mehr erlaubt (`422`).
- Fehlt ein zu löschender Wert auf dem Server aus der URL, kommt `404` mit `detail.issues` (statt Text).

#### API
- Neu: `POST /api/v1/records/{server}/{zone}/bulk/preview` (Schreibrecht nötig; `{"ops": …}` oder
  `{"text": {"content", "mode", "scope", "default_ttl"}}`) – Antwort mit `changes`, `issues`, `summary`,
  `peers` und einem fertigen Body `ops` für `/bulk`.
- `/bulk` kennt zusätzlich `merge`, `set_ttl`, `set_disabled`, `default_ttl`, `expected` (Fingerprints aus der
  Vorschau für jedes berührte RRset, auch unveränderte; Abweichung oder ein geändertes RRset ohne Fingerprint →
  `409` mit `conflicts`), `force` (nur API, im Audit als `forced`), `source`, `mode`, `manage_ptr`.
  Antwort-`details`: `created`, `deleted`, `changed_rrsets`, `unchanged_rrsets`, `fanout`, `peer_drift`, `audit_id`,
  optional `ptr`. Neuer Fan-out-Status `skipped (no changes needed)`.
- Einzel-Endpunkte (Anlegen/Ändern/Löschen) und Bulk liefern bei PTR-Pflege `details.ptr`.

#### Nach dem Update prüfen
- Zonenansicht: Checkboxen und „Text-Editor“ erscheinen nur für Benutzer mit Schreibrecht.
- Einmal eine Bulk-TTL-Änderung an einem RRset mit PowerDNS-Kommentar ausführen und prüfen, dass der Kommentar
  erhalten bleibt (der Bulk-Editor sendet keine Kommentare; PowerDNS behält sie dann).
- Skripte, die `/bulk` mit `"records": []` zum Löschen nutzen, auf `delete` umstellen.

#### Doku-Impact (für WS-DOCS-REPO / WS-DOCS-WEBSITE)
- README „Was es kann“: Bulk-Editor erwähnen; Changelog-Stichpunkte wie oben (inkl. Breaking).
- `docs/PANEL-API.md`: Endpunkt `…/bulk/preview`, Bulk-Body mit allen Listen und `expected`, Semantik-Tabelle
  (create = REPLACE, merge = anhängen, delete mit/ohne content, set_ttl, set_disabled), Fehlercodes 404/409/422,
  Fan-out-Regeln (Primary zuerst, `skipped (no changes needed)`, `peer_drift`), `record.bulk`-Payload.
- `frontend/README.md`: Aufbau um `src/components/bulk/`, `src/zoneDetail/bulkModel.js` und
  `src/zoneDetail/header-actions/40-text-editor.action.jsx` ergänzen.
- Website DE/EN `docs/features/zonen-records`: Abschnitt „Bulk-Operationen (nur per API)“ durch „Bulk-Editor“
  ersetzen (Mehrfachauswahl, Text-Editor mit drei Modi und Beispiel, Vorschau, Mehrserver-Verhalten, TTL gilt je
  RRset); veraltete Aussage „Eine Mehrfachauswahl in der Record-Liste gibt es im Panel nicht“ / „there is no
  multi-select“ entfernen; Beschreibung (Z. 9) „Bulk-Editor“ statt „Bulk … per API“.
- Website `webhooks`: `record.bulk` kommt jetzt auch aus der Oberfläche; Payload-Felder ergänzen.
- Website `audit-log`: `BULK_UPDATE` mit vorher/nachher, zurücksetzbar (Verweis auf Record-Historie).
- Website `panel-api`: Endpunkt-Tabelle um `bulk/preview`.
