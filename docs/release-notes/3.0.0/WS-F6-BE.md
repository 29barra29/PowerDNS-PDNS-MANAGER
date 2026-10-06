### Webhooks werden zugestellt – mit Wiederholungen, Protokoll und Test (WS-F6-BE)

#### Neu
- **Zuverlaessige Zustellung:** Ein Hintergrunddienst stellt die Ereignisse aus der Warteschlange zu. Schlaegt ein
  Versuch fehl (Verbindungsfehler, Zeitueberschreitung, HTTP-Status ausser 2xx), folgt der naechste nach ca. 1, 2, 4, 8
  und 16 Minuten; nach 6 Versuchen (~31 min) gilt die Zustellung als endgueltig fehlgeschlagen. `Retry-After` bei 429/503
  wird beachtet (hoechstens 1 Stunde). HTTP 410 und Ziele im internen Netz (SSRF-Schutz) beenden die Zustellung sofort.
  Jeder Versuch sendet exakt denselben Body mit derselben Signatur und Delivery-ID – Empfaenger koennen per
  `X-DNS-Manager-Delivery` doppelte Zustellungen erkennen (Zustellung „mindestens einmal“, keine feste Reihenfolge).
- **Zustellprotokoll je Webhook:** Status (`queued`, `in_progress`, `succeeded`, `failed`, `dead`, `cancelled`), Versuche,
  HTTP-Code, Dauer, Fehlercode und die ersten 1024 Zeichen der Antwort; Detailansicht mit gesendetem Body und Headern.
  Eintraege werden nach 30 Tagen automatisch geloescht.
- **Erneut senden:** fehlgeschlagene Zustellungen sofort erneut versuchen, endgueltig fehlgeschlagene (oder auch
  erfolgreiche) mit einem zusaetzlichen Versuch neu einplanen.
- **Test senden:** schickt sofort ein `webhook.test`-Ereignis an genau diesen Webhook (auch wenn er deaktiviert ist)
  und zeigt das Ergebnis an; hoechstens alle 10 Sekunden je Webhook und ein Test gleichzeitig je Benutzer
  (bei vielen gleichzeitigen Tests antwortet das Panel kurz mit „bitte erneut versuchen“).
- **Verwaltung:** Webhooks lassen sich bearbeiten (Name, Ziel, Ereignisse, Ausloeser „nur eigene Aenderungen“ oder „alle
  Aenderungen in meinen Zonen“), aktivieren/deaktivieren und mit neuem Secret versehen. Deaktivieren verwirft offene
  Zustellungen; nach einer Secret-Erneuerung werden offene Zustellungen mit dem neuen Secret signiert.
- **Audit:** `WEBHOOK_CREATE`, `WEBHOOK_UPDATE`, `WEBHOOK_SECRET_ROTATE`, `WEBHOOK_DELETE`, `WEBHOOK_DELIVERY_RETRY`
  (nur der Host des Ziels, nie Pfad oder Secret).
- **Prometheus:** `pdnsmgr_webhook_deliveries_total{status="success|retry|dead|cancelled"}` zaehlt die Versuche.

#### Geändert / Breaking
- Webhooks werden ab 3.0 wirklich verschickt (in 2.3.7–2.4.x lief die Zustellung wegen eines Fehlers nie). Empfaenger vor
  dem Update pruefen.
- Anlegen, Aendern, Loeschen, Test, Zustellprotokoll und erneut senden nur noch mit Browser-Anmeldung, nicht per API-Token.
  Die Liste ist per Token weiter lesbar, dann ohne Ziel-URL.
- Ist die (jetzt verschluesselte) Ziel-URL nicht mehr lesbar, z. B. nach Verlust des Schluessels, wird nichts gesendet
  (`url_unreadable`); die Liste zeigt die URL dann als nicht lesbar – bitte neu eintragen.
- Wird beim Widerruf aller Zugaenge eines Benutzers ein Webhook deaktiviert, erscheinen seine wartenden und auf eine
  Wiederholung wartenden (fehlgeschlagenen) Zustellungen als `cancelled` und werden nicht mehr gesendet.
- Voraussetzung fuer externe Datenbanken: MariaDB ab 10.6 bzw. MySQL ab 8.0 (`SKIP LOCKED`). Es darf nur **ein**
  Backend-Prozess die Warteschlange abarbeiten (Standard). `BACKGROUND_WORKERS_ENABLED=false` sammelt Ereignisse nur.

#### API
Alle unter `/api/v1/auth/me/webhooks`:
- `GET ""` – Liste mit `url_display`, `has_url`, `has_secret`, `scope`, Zaehlern (`last_success_at`, `last_failure_at`,
  `consecutive_failures`), `stats` je Status sowie `available_events`, `worker_enabled`, `worker_running`,
  `max_attempts`, `retention_days`, `max_webhooks`.
- `POST ""` (201) – `name`, `url`, `events`, `scope` (`own`|`zones`), `is_active`; Antwort mit einmaligem `secret`.
  Hoechstens 20 Webhooks je Benutzer.
- `PUT /{id}` – nur gesetzte Felder; `rotate_secret: true` liefert `new_secret`.
- `DELETE /{id}` – loescht auch das Zustellprotokoll (`deleted_deliveries`).
- `POST /{id}/test` – `{success, message, delivery}`; 429 innerhalb von 10 s.
- `GET /{id}/deliveries?limit=&offset=&status=&event=` – `{total, limit, offset, deliveries}`, neueste zuerst.
- `GET /{id}/deliveries/{delivery_pk}` – mit `body` und `request_headers`.
- `POST /{id}/deliveries/{delivery_pk}/retry` – 409, wenn bereits eingeplant, der Webhook deaktiviert oder das Secret
  nicht lesbar ist.
- Fehlercodes im Protokoll: `http_status`, `redirect`, `gone`, `connect_error`, `timeout`, `total_timeout`,
  `dns_error`, `ssrf_blocked`, `invalid_url`, `internal_error`, `webhook_inactive`, `webhook_deleted`, `owner_inactive`,
  `interrupted`, `secret_unreadable`, `url_unreadable`.

#### Nach dem Update prüfen
- Einstellungen → API & Sicherheit → Webhooks: „Test senden“ fuer jeden Webhook; das Zustellprotokoll zeigt das Ergebnis.
- Bestehende Empfaenger auf Payload v2 und die Header `X-DNS-Manager-Event`, `-Delivery`, `-Attempt` einstellen.

#### Website-Seiten (DE/EN)
- `docs/features/webhooks`: Abschnitt „Zustellung (kein Retry)“ ersetzen durch „Zustellung, Wiederholungen & Protokoll“
  (Backoff-Tabelle 1/2/4/8/16 min, Status, Test, erneut senden, mindestens einmal, Deduplizierung per Delivery-ID, keine
  Reihenfolge, 410/SSRF endgueltig, Retry-After); „Secret rotieren“: Button im Panel, per API nur mit Browser-Sitzung
  (Panel-Token-Beispiel entfernen); Endpunkt-Tabelle wie oben.
- `docs/troubleshooting`: „Webhook kommt nicht an“ → zuerst Zustellprotokoll, Fehlercodes erklaeren.
- `docs/features/audit-log`: neue Aktionen `WEBHOOK_*`.
- `docs/installation`, `docs/konfiguration`: Zustellprotokoll liegt 30 Tage in der Datenbank; `BACKGROUND_WORKERS_ENABLED`;
  Datenbank-Voraussetzung MariaDB >= 10.6.
- `docs/update`: Hinweis „Webhooks werden ab jetzt wirklich zugestellt“.
