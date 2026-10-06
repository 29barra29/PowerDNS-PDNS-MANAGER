# PDNS Manager – API für Skripte

Die Weboberfläche nutzt dieselbe **REST-API** unter dem Pfad-Präfix `/api/v1`. Skripte, Terraform, CI oder Monitoring
sprechen sie mit einem **Panel-API-Token** an (`Authorization: Bearer dnsmgr_usr_…`). Für Router gibt es eigene
DynDNS-Tokens, für certbot ACME-Tokens und für Prometheus einen Scrape-Token.

Dieses Dokument beschreibt den Stand **3.0.0**: Zugangsarten und Rechte, Fehlerformate, das Verhalten bei mehreren
PowerDNS-Servern, die wichtigsten Endpunkte mit Beispielen, Webhooks, DynDNS und Metriken. Am Ende steht die
[vollständige Endpunkt-Übersicht](#endpunkt-übersicht). Was sich gegenüber 2.x geändert hat, fasst der
[Changelog im README](../README.md#v300--major) zusammen.

## Inhalt

- [Grundlagen](#grundlagen)
- [Authentifizierung und Rechte](#authentifizierung-und-rechte)
- [Fehlerformate und Statuscodes](#fehlerformate-und-statuscodes)
- [Mehrere PowerDNS-Server (Fan-out)](#mehrere-powerdns-server-fan-out)
- [Records](#records)
- [Bulk-Änderungen](#bulk-änderungen)
- [Zonen](#zonen)
- [DNSSEC](#dnssec)
- [Zonenverlauf und Rollback](#zonenverlauf-und-rollback)
- [Audit-Log](#audit-log)
- [Propagations-Check](#propagations-check)
- [LUA-Records](#lua-records)
- [DynDNS](#dyndns)
- [PTR-Pflege](#ptr-pflege)
- [Webhooks](#webhooks)
- [Metriken](#metriken)
- [ACME / DNS-01](#acme--dns-01)
- [Endpunkt-Übersicht](#endpunkt-übersicht)
- [Praxis-Tipps](#praxis-tipps)

---

## Grundlagen

- **Basis-URL:** `https://<dein-host>` (ohne Reverse-Proxy oft `http://localhost:5380`).
- **API-Präfix:** `/api/v1`. Ohne Präfix gibt es für Skripte nur `GET /health`, `GET /metrics`, `GET /nic/update` und
  `GET /api` (Name, Version, Präfix).
- **Format:** JSON (Ausnahmen: `/nic/update` antwortet mit Text, `/metrics` im Prometheus-Textformat, der Audit-Export als
  CSV). Zeitstempel sind ISO 8601 mit `+00:00`.
- **Zonennamen** in Pfaden mit Punkt am Ende (`example.com.`); das Backend normalisiert auf Kleinschreibung mit Punkt.
  Record-Namen als FQDN (`www.example.com.`).
- **Interaktive Doku:** `/docs` (Swagger) und `/redoc`, nur mit `DOCS_ENABLED=true` (Standard aus). Nicht darin
  enthalten sind `/nic/update`, `/metrics`, die OIDC-Endpunkte, `GET /api/v1/settings/app-info` und
  `GET /api/v1/acme/whoami`; diese stehen in der [Endpunkt-Übersicht](#endpunkt-übersicht).
- **Health:** `GET /health` → `{"status": "healthy"|"degraded"|"unhealthy", "database": "connected"|"disconnected",
  "servers": {"<name>": "healthy"|"unreachable"}}`. HTTP 503 nur, wenn die Datenbank nicht erreichbar ist. Weitere
  Felder nur bei Abfrage aus dem Container selbst (`127.0.0.1`, ohne Proxy-Header).

```bash
export DNSMGR='https://dns.example.com'
export TOKEN='dnsmgr_usr_…'                  # vollständiger Token aus dem Panel
H_AUTH="Authorization: Bearer $TOKEN"
curl -sS -H "$H_AUTH" "$DNSMGR/api/v1/auth/me"
```

---

## Authentifizierung und Rechte

### Zugangsarten

| Zugang | Erkennbar an | Wofür |
|---|---|---|
| **Browser-Session** | HttpOnly-Cookie `dns_manager_token` (oder dasselbe JWT als Bearer) | Weboberfläche; alles, was der Benutzer darf |
| **Panel-API-Token** | `Authorization: Bearer dnsmgr_usr_…` | Skripte; Rechte des Benutzers, zusätzlich einschränkbar |
| **ACME-Token** | `Authorization: Bearer dnsmgr_acme_…` | nur `_acme-challenge`-TXT in freigegebenen Zonen |
| **DynDNS-Token** | Basic-Auth-Passwort oder `Authorization: Bearer dnsmgr_ddns_…` | nur A/AAAA der freigegebenen Hostnamen |
| **Scrape-Token** | `Authorization: Bearer <token>` an `/metrics` | nur Prometheus-Metriken |

Panel-Tokens werden **nur** aus dem `Authorization`-Header gelesen, nie aus einem Cookie. Tokens werden nur als Hash
gespeichert; der Klartext erscheint genau einmal beim Anlegen.

### Zugangsklassen der Endpunkte

Jede Route ist einer dieser Klassen zugeordnet (Spalte „Zugang“ in der [Endpunkt-Übersicht](#endpunkt-übersicht)):

| Klasse | Bedeutung |
|---|---|
| **öffentlich** | ohne Anmeldung |
| **angemeldet** | Session oder Panel-Token; zonenunabhängig (Listen zeigen nur sichtbare Zonen) |
| **Zone: Lesen / Zone: Schreiben** | Session oder Panel-Token **und** Recht auf die Zone aus dem Pfad. Für „Lesen“ genügt das Zonenrecht `read`, „Schreiben“ verlangt `manage`, ein Panel-Token mit Schreibrecht und einen Server mit „Speichern: Ja“ (wo geschrieben wird). Admins haben jedes Zonenrecht; ein Token mit Zonen-Scope schränkt auch sie ein |
| **Admin** | Rolle Admin; per Panel-Token nur mit der Freigabe `allow_admin` |
| **Session** | nur mit Browser-Anmeldung – per API-Token **403** |
| **Session, Admin** | nur Admins mit Browser-Anmeldung |
| **ACME-/DynDNS-/Scrape-Token** | eigene Token-Prüfung im Endpunkt, keine Cookies, keine Panel-Tokens |

Nur mit Browser-Anmeldung gehen insbesondere: alle `/api/v1/settings/*`-Endpunkte (außer `GET /settings/app-info`),
die Verwaltung von Panel-Tokens, DynDNS-Tokens und Webhooks (die Webhook-Liste ist per Token lesbar, ohne Ziel-URL),
2FA, Passkeys, Profil und Passwort, die Aufbewahrung des Audit-Logs sowie die schreibende Benutzerverwaltung.

### Eingeschränkte Panel-Tokens

Ein Panel-Token handelt immer im Namen seines Besitzers. Seine Rechte sind die **Schnittmenge** aus den Rechten des
Benutzers und den Einschränkungen des Tokens:

| Feld | Werte | Wirkung |
|---|---|---|
| `scope_zones` | Liste von Zonennamen oder `null` | `null` = alle Zonen des Besitzers (bei Admins alle Zonen); sonst nur diese Zonen (höchstens 500). Andere Zonen → 403 |
| `permission` | `manage` (Standard) oder `read` | Ein Lese-Token bekommt bei **jeder** Anfrage außer `GET`/`HEAD`/`OPTIONS` sofort 403 |
| `expires_in_days` | 1–3650 oder `null` | Laufzeit ab jetzt; `null` = kein Ablauf. Abgelaufen → 401 |
| `allow_admin` | `true`/`false` | Admin-Endpunkte (Klasse **Admin**) per Token; nur für Admins und nur ohne `scope_zones` |
| `is_active` | `true`/`false` (nur `PUT`) | `false` = pausiert → 401, umkehrbar |

- Ohne die neuen Felder entsteht ein Token wie in 2.x: alle Zonen, Lesen & Schreiben, kein Ablauf, keine
  Admin-Freigabe. Nach dem Update auf 3.0 haben Bestandstokens von Admins `allow_admin = true`.
- Zonenlisten, Suche und die Zonenzahl der Server zeigen nur sichtbare Zonen. Server-Statistiken sind mit einem auf
  Zonen beschränkten Token nicht abrufbar (403); die interne PowerDNS-URL sehen nur Admins.
- Zonen anlegen, löschen und importieren prüft zusätzlich den Zonen-Scope. Wird eine Zone endgültig gelöscht,
  verschwindet sie aus den Scopes der Tokens.
- `DELETE /auth/me/panel-tokens/{id}` widerruft endgültig; widerrufene Tokens erscheinen nie mehr in Listen und lassen
  sich nicht reaktivieren. Höchstens 50 offene Tokens je Benutzer.
- Was ein Skript gerade darf, zeigt `GET /api/v1/auth/me` im Block `auth`:
  ```json
  "auth": {"via": "panel_token", "token": {"id": 7, "name": "ci-deploy", "token_prefix": "dnsmgr_usr_Ab3x9…",
           "scope_zones": ["example.com."], "permission": "manage", "allow_admin": false,
           "expires_at": "2027-01-04T09:12:00+00:00"}}
  ```
  Bei einer Browser-Session ist `via` = `"session"` und `token` = `null`.

Token anlegen (nur mit Browser-Session; die Antwort enthält `plaintext_token` genau einmal):

```http
POST /api/v1/auth/me/panel-tokens
{"name": "ci-deploy", "scope_zones": ["example.com."], "permission": "manage", "expires_in_days": 90}
```

Antworten der Token-Prüfung (`detail`): `Ungültiger API-Token` (401), `API-Token ist deaktiviert` (401),
`API-Token ist abgelaufen` (401), `Dieser API-Token hat nur Leserechte` (403),
`Dieser API-Token ist für die Zone „<zone>“ nicht freigegeben` (403), `Dieser API-Token hat keine Admin-Rechte – …`
(403), `Diese Aktion ist mit einem API-Token nicht erlaubt – bitte im Browser anmelden` (403).

### Besonderheiten der Browser-Session

- **CSRF-Schutz:** `POST`/`PUT`/`PATCH`/`DELETE` unter `/api/` ohne `Authorization: Bearer` müssen von der eigenen Seite
  kommen (`Sec-Fetch-Site`/`Origin`), sonst 403 `Cross-Site-Anfrage abgelehnt (CSRF-Schutz)`. Anfragen ohne diese
  Header (curl) und Bearer-Anfragen sind nicht betroffen.
- **Erzwungener Passwortwechsel:** Solange `must_change_password` gesetzt ist, sind nur `GET /auth/me`,
  `PUT /auth/me/password` und `POST /auth/logout` erlaubt; alles andere → 403 mit Header
  `X-Password-Change-Required: 1`. Panel-Tokens sind nicht betroffen.
- **Sitzungs-Widerruf:** Nach „Alle Zugänge widerrufen“, einem Admin-Passwort-Reset oder einer Passwortänderung
  bekommen ältere Sitzungen 401 `Sitzung abgelaufen – bitte erneut anmelden`.
- **Bestätigung kritischer Änderungen (Step-up):** Kritische SSO-Einstellungen (`PUT /settings/sso`) und das Umwandeln
  eines Kontos (`POST /auth/users/{id}/convert-to-local`) verlangen eine erneute Bestätigung. Fehlt sie, kommt 403 mit
  `{"detail": {"message": "…", "code": "stepup_required"}}` und Header `X-Step-Up-Required`; dieselbe Anfrage mit
  `"step_up": {"current_password": "…", "totp_code": "…"}` im Body wiederholen. Falsche Angaben → `stepup_failed`,
  zu viele Fehlversuche → 429. Konten mit SSO-/LDAP-Anmeldung bekommen `reauth_required` (Anmeldung älter als
  10 Minuten; abmelden und neu anmelden).

### Drosselung

| Was | Grenze | Antwort |
|---|---|---|
| Login, 2FA, Passkey, LDAP-Verknüpfung, Step-up | 25 Fehlversuche je IP (IPv6 je /64) **oder** 5 je Benutzername in 15 Minuten | 429 |
| OIDC-Start/-Rücksprung | 20 Fehlschläge je IP in 5 Minuten | Weiterleitung mit `sso_error=rate_limited` |
| Propagations-Check, DNSKEY- und Elternzonen-Prüfung | 10 nicht zwischengespeicherte Prüfungen je Benutzer und Minute (gemeinsam) | 429 mit `Retry-After` |
| Webhook „Test senden“ | ein Test je Webhook in 10 Sekunden | 429 |
| DynDNS | siehe [DynDNS](#dyndns) | `911`/`abuse` |

Anfragen mit Panel-Token sind sonst nicht gedrosselt.

---

## Fehlerformate und Statuscodes

Die meisten Fehler kommen als FastAPI-Standard mit `detail` (deutscher Text):

```json
{"detail": "Keine Berechtigung für diese Zone"}
```

Weitere Formen:

| Form | Wo | Beispiel |
|---|---|---|
| `detail` als Objekt mit `message` und `code` | DNSSEC-Schutzregeln, Rollback, Bulk, Step-up | `{"detail": {"message": "…", "code": "last_active_key", "force_possible": true}}` |
| `detail` als Liste | Validierungsfehler (422) | `{"detail": [{"loc": ["body", "ttl"], "msg": "…", "type": "…"}]}` |
| `code` und `fields` neben `detail` | geändertes Ziel ohne neues Geheimnis (400) | `{"detail": "…", "code": "secret_reentry_required", "fields": ["url"]}` |
| PowerDNS-Fehler | Endpunkte, die PowerDNS-Fehler durchreichen | `{"error": "PowerDNS API Error", "server": "master-fra1", "detail": "…"}` (bei 5xx ohne PowerDNS-Interna) |
| `error` | `GET` auf unbekannte Pfade unter `/api/`, unerwartete Eingabefehler | `{"error": "Not found"}` (404), `{"error": "…"}` (400) |

| Code | Bedeutung |
|---|---|
| 400 | Ungültige Anfrage, u. a. `secret_reentry_required`, „Nichts zum Widerrufen ausgewählt“ |
| 401 | Nicht angemeldet, Token ungültig, pausiert, abgelaufen oder widerrufen, Sitzung abgelaufen |
| 403 | Keine Berechtigung: Zonenrecht, Lese-Token, Zone außerhalb des Token-Scopes, Admin-Endpunkt ohne `allow_admin`, Session-Pflicht, Server mit „Speichern: Nein“, LUA-Policy, CSRF, Passwortwechsel ausstehend, Step-up |
| 404 | Server, Zone, Record, Wert, Eintrag oder Token nicht gefunden (fremde Webhooks und Tokens verhalten sich wie nicht vorhandene) |
| 409 | Konflikt: Zone geändert seit Vorschau/Eintrag (`expected`, Rollback), DNSSEC-Schutzregel, `parent_ds_present`, vorsignierte Zone, gesperrtes DynDNS-Secret, nicht lesbarer API-Key, Einstellung durch Umgebungsvariable gesperrt |
| 422 | Validierung (Body, Typ `TYPEnnn`, TTL, LUA-Inhalt), blockierende Bulk-Probleme, nicht rücksetzbarer Verlaufseintrag |
| 429 | Drosselung (siehe oben) |
| 502/504 | PowerDNS nicht erreichbar bzw. Zeitüberschreitung |
| 503 | Datenbank oder Verschlüsselung nicht verfügbar, LDAP nicht erreichbar, Audit-Eintrag für eine Key-Anzeige nicht schreibbar |

---

## Mehrere PowerDNS-Server (Fan-out)

Haben mehrere Server „Speichern: Ja“ (`allow_writes`) und führen dieselbe Zone, schreibt das Panel auf alle:

1. Zuerst auf den **Server aus der URL** („Primary“). Ist er auf „Speichern: Nein“ gesetzt → 403. Scheitert er, werden
   die übrigen Server **nicht** angefasst und die Anfrage endet mit dem Fehler (fehlende Zone → 404).
2. Danach auf jeden weiteren schreibbaren Server, **je auf Basis seines eigenen Stands**: Einzel-Endpunkte lesen das
   RRset des jeweiligen Servers und ändern nur den betroffenen Wert; Werte, die nur dort existieren, bleiben erhalten.
   Bulk wendet den Plan je Server als eine atomare Änderung an.
3. Bricht die Verbindung zum Primary nach dem Senden ab, liest das Panel nach, ob die Änderung angekommen ist.

DNSSEC-Operationen und NOTIFY laufen nur gegen den Server aus der URL. Das Ergebnis je Server steht in `details`:

| Status | Bedeutung |
|---|---|
| `saved` / `deleted` | geschrieben bzw. gelöscht |
| `skipped (zone not present)` | Server führt die Zone nicht |
| `skipped (read-only)` | Server steht auf „Speichern: Nein“ |
| `skipped (no matching content)` | der zu ändernde bzw. zu löschende Wert existiert dort nicht |
| `skipped (no changes needed)` | Server hat bereits den Zielstand |
| `skipped (not loaded: api key unreadable)`, `skipped (not loaded: api key empty)` | Server ist konfiguriert, aber nicht geladen (API-Key nicht entschlüsselbar bzw. leer) – dort wurde nichts geändert |
| `error: <Text>` | Fehler auf diesem Server |
| `error: unklar – bitte Zone neu laden` | Verbindungsabbruch am Primary, Ausgang nicht feststellbar |

```json
{
  "message": "Record 'www.example.com.' (A) created/updated in zone 'example.com.'",
  "details": {"master-fra1": "saved", "master-ams1": "saved", "backup-1": "skipped (not loaded: api key unreadable)"}
}
```

**`primary_outcome`** steht im Audit-Eintrag jeder Record-Änderung (`details.primary_outcome`) und in der Antwort eines
Rollbacks: `ok`, `verified_after_timeout` (Verbindungsabbruch, Änderung per Nachlesen bestätigt), `unknown` (Ausgang
unklar) oder `failed`.

---

## Records

| Aktion | Aufruf | Semantik |
|---|---|---|
| Lesen | `GET /records/{server}/{zone}` | `records` (je Wert eine Zeile) und `rrsets` (PowerDNS-Format) |
| Anlegen | `POST /records/{server}/{zone}` | Werte werden zum bestehenden RRset hinzugefügt (je Server auf dessen Stand); die TTL gilt für das ganze RRset |
| Ändern | `PUT /records/{server}/{zone}` | ersetzt genau einen Wert `old_content` durch `new_content` (+ `ttl`, `disabled`) |
| Löschen | `DELETE /records/{server}/{zone}/delete` | mit `content`: nur dieser Wert; ohne: ganzes RRset |

- **Typen:** A, AAAA, CNAME, MX, TXT, NS, SOA, SRV, CAA, PTR, ALIAS, DNAME, LOC, NAPTR, SSHFP, TLSA, DS, DNSKEY, NSEC,
  NSEC3, NSEC3PARAM, RRSIG, SPF, OPENPGPKEY, HTTPS, SVCB, LUA. Andere Typen → 422 `Unknown record type: …`, generische
  Angaben wie `TYPE65402` → 422.
- **TTL:** 60–604800 Sekunden (Standard 3600).
- **`manage_ptr`** (optional, nur A/AAAA): PTR in einer vom Panel verwalteten Reverse-Zone mitpflegen; fehlt das Feld,
  gilt der Admin-Standard (ab Werk aus). Lief die PTR-Pflege, enthält `details` zusätzlich `ptr` (Liste der Ergebnisse
  je IP). Siehe [PTR-Pflege](#ptr-pflege).
- **Fehler:** Zone fehlt auf dem Server aus der URL → 404; Wert nicht vorhanden → 404 (`Wert '…' nicht im RRset … vorhanden`
  bzw. `Original record content not found in …`); Server „Speichern: Nein“ → 403.
- Jede erfolgreiche Änderung schreibt einen Audit-Eintrag (`CREATE`, `UPDATE`, `DELETE`, Format v2) und das
  Webhook-Ereignis `record.created|updated|deleted`.

```bash
# Wert hinzufügen bzw. RRset anlegen
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" \
  -X POST "$DNSMGR/api/v1/records/master-fra1/example.com." \
  -d '{"name": "www.example.com.", "type": "A", "ttl": 300,
       "records": [{"content": "203.0.113.10", "disabled": false}]}'

# Einen Wert ersetzen
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" \
  -X PUT "$DNSMGR/api/v1/records/master-fra1/example.com." \
  -d '{"name": "www.example.com.", "type": "A", "ttl": 300,
       "old_content": "203.0.113.10", "new_content": "203.0.113.11"}'

# Einzelnen Wert löschen (ohne "content": ganzes RRset)
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" \
  -X DELETE "$DNSMGR/api/v1/records/master-fra1/example.com./delete" \
  -d '{"name": "www.example.com.", "type": "A", "content": "203.0.113.11"}'
```

---

## Bulk-Änderungen

`POST /records/{server}/{zone}/bulk` ändert viele RRsets in einem Schritt (je Server eine atomare Änderung).
`POST /records/{server}/{zone}/bulk/preview` rechnet dieselbe Änderung nur durch. Beide verlangen Schreibrecht auf
die Zone.

| Feld | Semantik |
|---|---|
| `create` | RRset **komplett ersetzen** (`name`, `type`, `ttl`, `records`; `records` darf nicht leer sein) |
| `delete` | mit `content`: einen Wert entfernen; ohne: ganzes RRset löschen |
| `merge` | Werte anhängen; `ttl: null` behält die bestehende TTL (neues RRset: `default_ttl`) |
| `set_ttl` | TTL eines RRsets setzen (`name`, `type`, `ttl`) |
| `set_disabled` | einen Wert (de)aktivieren (`name`, `type`, `content`, `disabled`) |
| `default_ttl` | TTL für neue RRsets aus `merge` (Standard 3600) |
| `expected` | Liste `{name, type, fingerprint}` aus der Vorschau; Abweichung → 409 |
| `force` | `expected`-Abweichungen übergehen (steht im Audit als `forced`) |
| `manage_ptr` | PTR-Pflege für A/AAAA (gilt für die ganze Anfrage) |
| `source`, `mode` | nur für Audit und Webhook (`api`/`selection`/`text`; `merge`/`replace`/`sync_scope`) |

Regeln und Grenzen: höchstens 5000 Einträge je Anfrage. Ein RRset darf entweder einmal „absolut“ (`create` bzw.
`delete` ohne `content`) oder beliebig oft „relativ“ (`delete` mit `content`, `merge`, `set_ttl`, `set_disabled`)
vorkommen, nicht beides. Leere Anfrage → 422.

**Vorschau:** Body `{"ops": {…wie /bulk…}}` oder `{"text": {"content": "<BIND-Zeilen>", "mode": "merge"|"replace"|"sync_scope",
"scope": [{name, type}], "default_ttl": 3600}}` (höchstens 1 000 000 Zeichen bzw. 20 000 Zeilen). Antwort: `changes`
(vorher/nachher je RRset), `issues` (mit `severity`, `code`, `line`), `summary`, `blocking`, `peers`,
`skipped_servers` und `ops` – der fertige Body für `/bulk` inklusive `expected` (Fingerprint je berührtem RRset).

**Antwort von `/bulk`:** `details` mit `created`, `deleted`, `changed_rrsets`, `unchanged_rrsets`, `fanout` (Status je
Server), `peer_drift` (je weiterem Server die Zahl der Operationen, die dort schon dem Ziel entsprachen), `audit_id`
und bei PTR-Pflege `ptr`. Hat sich nichts geändert, ist `audit_id` `null`.

| Fehler | Bedeutung |
|---|---|
| 404 `{"message", "issues"}` | zu löschender Wert bzw. RRset fehlt auf dem Primary |
| 409 `{"message", "conflicts"}` | Stand weicht von `expected` ab, oder ein geändertes RRset hat keinen Fingerprint |
| 422 `{"message", "issues"}` | leere Anfrage, ungültige Werte (statische Prüfung), blockierende Probleme (CNAME-Konflikt, Apex-NS, SOA, …) |
| 422 (Validierungsliste) | Body-Fehler wie `create` mit leerer `records`-Liste, doppelte oder widersprüchliche Operationen für ein RRset, mehr als 5000 Einträge |
| 403 | LUA-Policy verbietet die LUA-Änderung |

```bash
# Alle www-A-Werte ersetzen und einen TXT-Wert löschen
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" \
  -X POST "$DNSMGR/api/v1/records/master-fra1/example.com./bulk" \
  -d '{"create": [{"name": "www.example.com.", "type": "A", "ttl": 300,
                   "records": [{"content": "203.0.113.20"}, {"content": "203.0.113.21"}]}],
       "delete": [{"name": "example.com.", "type": "TXT", "content": "\"old-verification\""}]}'
```

---

## Zonen

- `GET /zones/{server}` – Zonen des Servers (nur sichtbare); `GET /zones/{server}/{zone}/detail` – Zone mit RRsets.
- `POST /zones` (Admin; per Token `allow_admin` und Zonen-Scope) – Body `name`, `kind` (`Native`|`Master`|`Slave`),
  `nameservers`, `masters`, `soa_edit_api`, `servers` (leer = alle schreibbaren), `enable_dnssec`, `dnssec_options`
  (wie der Body von DNSSEC `enable`, ungültig → 422). `details` je Server: `created`, `synced` (Zone existiert bereits,
  z. B. gemeinsame Datenbank), `skipped (read-only)`, `error: …`, `created; dnssec-skipped` (DNSSEC nur auf dem
  ersten angelegten Server), `created; dnssec-error: <Grund>`. Der SOA bekommt `hostmaster.<zone>` als Hostmaster.
- `PUT /zones/{server}/{zone}` – `kind`, `masters`, `account` nur Admins; Ereignis `zone.updated`.
- `DELETE /zones/{server}/{zone}` (Admin) – löscht auf diesem Server; Zonenrechte und Token-Scopes werden entfernt,
  wenn kein anderer Server die Zone noch führt.
- `POST /zones/{server}/{zone}/notify` (Schreibrecht) – NOTIFY vom angegebenen Server; nur für Zonen vom Typ
  Master/Producer (Slave mit `secondary-do-renotify`), sonst 422 mit Erklärung. Audit `ZONE_NOTIFY`.
- `GET /zones/{server}/{zone}/export` (Leserecht) – `{zone, server, format: "bind", content, filename}`; Audit
  `ZONE_EXPORT`.
- `POST /zones/import/preview` und `POST /zones/import` (Admin) – Body `name`, `content` (BIND-Zonendatei), `kind`,
  `nameservers`. Die Vorschau liefert den Vergleich mit der bestehenden Zone sowie `lua_count`, `lua_issues`,
  `lua_policy`, `lua_blocked` und `lua_blocked_lines` (höchstens 50). Der Import legt die Zone auf jedem
  schreibbaren Server an (`imported`, `synced`, `error: …`). Bei LUA-Policy `disabled` und LUA-Zeilen in der Datei →
  403 `Die Zonendatei enthält LUA-Records, LUA-Records sind in diesem Panel deaktiviert. Betroffene Zeilen: 4, 9.`
  (höchstens 10 Zeilen, dann „…“). Geprüft wird so, wie PowerDNS die Datei liest (auch `IN IN LUA`, `TYPE065402`,
  `$GENERATE`, `$INCLUDE`); im Zweifel wird gesperrt.

---

## DNSSEC

Alle DNSSEC-Endpunkte arbeiten nur auf dem Server aus der URL. Lesende Endpunkte brauchen Leserecht, schreibende
(und `GET …/keys/{id}`) Schreibrecht und einen Server mit „Speichern: Ja“ (sonst 403); vorsignierte Zonen → 409.
Private Schlüssel werden nie ausgegeben.

| Endpunkt | Zweck |
|---|---|
| `GET /dnssec/{server}/{zone}/status` | Zustand, Schlüssel mit Key-Tag und DS-Status, NSEC/NSEC3, Rollover-Phase, Vergleich mit anderen Servern, Hinweise, `capabilities` (u. a. `parent_ds_check`, `dnskey_check`) |
| `POST …/enable` | aktivieren; idempotent (`already_enabled`), nur inaktive Schlüssel → 409 |
| `POST …/disable` | deaktivieren (`force`, `bump_serial`) |
| `GET …/keys`, `GET …/keys/{id}` | Schlüssel |
| `POST …/keys` | Schlüssel anlegen: `keytype` (`csk`/`ksk`/`zsk`), `algorithm`, `bits`, `active` (Standard `false`), `published` (Standard `true`) |
| `PUT …/keys/{id}` | `active` und/oder `published` ändern (`force`) |
| `POST …/keys/{id}/activate`, `…/deactivate`, `DELETE …/keys/{id}` | wie bisher, mit Schutzregeln |
| `PUT …/nsec3` | NSEC/NSEC3 ändern |
| `GET …/ds` | DS-Records für den Registrar |
| `GET …/parent-ds` | DS der Elternzone bei den Resolvern, Abgleich je KSK/CSK |
| `GET …/dnskey-check?key_tag=…` | DNSKEY je autoritativem Nameserver (`all_ok`); höchstens 12 `key_tag` |

**Body von `enable`** (leerer Body = Standardwerte):

```json
{"key_model": "csk", "algorithm": "ECDSAP256SHA256", "bits": null,
 "nsec_mode": "nsec3", "nsec3_iterations": 0, "nsec3_salt": "-", "nsec3_optout": false, "nsec3narrow": false,
 "bump_serial": null}
```

- `key_model`: `csk` oder `ksk_zsk`. `algorithm`: `ECDSAP256SHA256` (Standard), `ECDSAP384SHA384`, `ED25519`,
  `ED448`, `RSASHA256`, `RSASHA512` (RSA mit `bits` 2048, 3072 oder 4096). NSEC3-Iterationen 0–50.
- Das 2.x-Feld `nsec3param` (z. B. `"1 0 1 ab"`, `""` = NSEC) wird weiter verstanden und überschreibt die NSEC-Felder.
- `bump_serial`: `null` = bei Zonen vom Typ Master/Producer nach der Änderung SOA-Serial erhöhen und NOTIFY senden,
  `false` = nicht. Antworten enthalten `serial_bumped`, `serial`, `serial_error`, `notified`, `notify_error`.

**Schutzregeln** (409, mit `{"force": true}` übersteuerbar, steht dann im Audit):

| `detail.code` | Bedeutung |
|---|---|
| `last_active_key` | letzter aktiver Schlüssel der Zone |
| `last_active_sep` | letzter aktiver KSK/CSK |
| `last_published_sep` | letzter veröffentlichter aktiver KSK/CSK |
| `parent_ds_present` | nur bei `disable`: Die Elternzone veröffentlicht laut Resolvern noch einen DS (Prüfung freigeschaltet) |

Format: `{"detail": {"message": "…", "code": "…", "force_possible": true}}`. Das Audit `DNSSEC_DISABLE` enthält
`parent_ds` = `not_checked`, `none` oder `present_forced`.

**Prüfungen per DNS** (`parent-ds`, `dnskey-check`, Schutzregel `parent_ds_present`): nur nach Freischaltung unter
Einstellungen → Monitoring (externe DNS-Abfragen; für `dnskey-check` zusätzlich „autoritative Nameserver prüfen“).
Ohne Freischaltung antworten die Endpunkte mit `"enabled": false`. Panel-Tokens mit Zonen-Scope dürfen prüfen.
PowerDNS-Fehler erscheinen als `PowerDNS (<server>): <meldung>`.

```bash
# DNSSEC mit Standardwerten aktivieren
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" -X POST \
  "$DNSMGR/api/v1/dnssec/master-fra1/example.com./enable" -d '{}'

# Deaktivieren, auch wenn die Elternzone noch einen DS zeigt (bewusst!)
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" -X POST \
  "$DNSMGR/api/v1/dnssec/master-fra1/example.com./disable" -d '{"force": true}'
```

---

## Zonenverlauf und Rollback

| Endpunkt | Recht | Zweck |
|---|---|---|
| `GET /zones/{server}/{zone}/history` | Lesen | Verlauf der Zone über alle Server, neueste zuerst |
| `GET …/history/{id}` | Lesen | ein Eintrag mit allen `changes` |
| `GET …/history/{id}/rollback-preview` | Schreiben | Plan gegen den aktuellen Stand des Servers aus der URL |
| `POST …/history/{id}/rollback` | Schreiben | zurücksetzen, Body optional `{"force": true}` |

Filter der Liste: `limit` (1–200, Standard 50), `offset`, `action` (Komma-Liste), `resource_type`, `type`, `name`,
`user_id`, `status` (`success`/`error`), `date_from`, `date_to`, `q` (ab 2 Zeichen). Die Antwort enthält `total`,
`entries` (je Eintrag `version`, `changes` – in Listen höchstens 20 –, `can_rollback`, `rollback_blocked_reason`,
`revert_of_id`, `reverted_by_id`) und `actors`.

- **Konflikt:** Wurde ein betroffenes RRset seitdem geändert → 409
  `{"detail": {"message", "code": "rollback_conflict", "conflicts", "already_reverted_by"}}`; mit `force: true`
  trotzdem. RRsets, die schon dem Vorher-Stand entsprechen, zählen nicht als Konflikt.
- **Nichts zu tun:** Entspricht alles schon dem Vorher-Stand → 200 mit `details.noop = true`.
- **Erfolg:** `details` mit `revert_audit_id`, `rolled_back`, `skipped`, `forced`, `fanout`, `primary_outcome`;
  Audit `RECORD_ROLLBACK` (`revert_of_id`), Ereignis `record.rollback`. Geschrieben wird auf den Server aus der URL und
  alle schreibbaren Server (Vorher-Stand des Servers aus der URL).
- **Nicht rücksetzbar** → 422 `{"detail": {"message", "code"}}` mit `code`: `legacy_format` (Eintrag vor 3.0),
  `failed_action`, `not_record_change`, `incomplete`, `no_changes`, `only_excluded_records`, `zone_recreated`,
  `dyndns_repair`. In Vorschau und Liste zusätzlich `no_write_permission`, `server_read_only`.
- **Nie zurückgesetzt:** SOA, DNSSEC-Records (inkl. DS) und `_acme-challenge`. LUA-Ziele unterliegen der LUA-Policy.
- **PTRs:** Ein Rollback pflegt keine PTRs. Die zugehörigen PTR-Änderungen stehen als eigene Einträge `PTR_SYNC` im
  Verlauf der Reverse-Zone und lassen sich dort zurücksetzen.
- **Datenschutz:** Nicht-Admins sehen keine Client-IPs, Token-Kennungen und PowerDNS-Fehlertexte; Einträge aus der
  Zeit vor einer endgültigen Löschung und Neuanlage der Zone sind für sie nicht sichtbar (404).

---

## Audit-Log

`GET /audit-log` (Admin): Filter `limit` (1–500, Standard 50), `offset`, `action` (Komma-Liste), `resource_type`,
`server_name`, `zone`, `user_id`, `status`, `date_from`, `date_to`, `q` (ab 2 Zeichen), `full` (ungekürzte Details,
dann höchstens 100 Einträge). Antwort `{count, total, offset, limit, entries}`; je Eintrag `id`, `timestamp`,
`action`, `resource_type`, `resource_name`, `server_name`, `zone_name`, `user_id`, `username`, `actor_username`,
`client_ip`, `details` (ohne `full`: `changes` auf 20 gekürzt, `details_truncated`), `status`, `error_message`,
`revert_of_id`, `reverted_by_id`.

- `GET /audit-log/{id}` (Admin) – ein Eintrag ungekürzt.
- `GET /audit-log/export` (Admin) – CSV (Semikolon, UTF-8 mit BOM) mit denselben Filtern und `max_rows` (bis 50 000).
  Spalten: `id;timestamp_utc;action;resource_type;resource_name;server_name;user_id;status;error_message;details_json;zone_name;username;revert_of_id`.
  Zellen, die mit `=`, `+`, `-`, `@`, Tab oder CR beginnen, bekommen ein `'` vorangestellt.
- `GET|PUT /audit-log/settings` (Admin, nur Session) – `retention_days`: 0 = unbegrenzt, sonst 7–3650. Die Bereinigung
  läuft stündlich und wird als `AUDIT_PURGE` protokolliert.

**Details-Format Version 2** (Record-Änderungen, Bulk, Rollback, DynDNS, PTR):

```json
{
  "version": 2,
  "zone": "example.com.",
  "changes": [
    {"name": "www.example.com.", "type": "A",
     "before": {"ttl": 300, "records": [{"content": "203.0.113.10", "disabled": false}], "comments": []},
     "after":  {"ttl": 300, "records": [{"content": "203.0.113.11", "disabled": false}], "comments": []}}
  ],
  "after_source": "reread",
  "fanout": {"master-fra1": "saved", "master-ams1": "saved"},
  "primary_outcome": "ok",
  "type": "A", "old": "203.0.113.10", "new": "203.0.113.11"
}
```

`before`/`after` = `null` heißt „RRset existierte nicht“. Die bisherigen Felder (`type`, `old`, `new`, `content`,
`records`, `created`/`deleted`) bleiben erhalten. Lief die Aktion über einen Token, steht in `details.auth`
`{"via", "token_id", "token_name", "token_prefix"}` (`via` z. B. `panel_token`, `acme_token`, `dyndns_token`).
Einträge aus Versionen vor 3.0 haben kein `details.version`; der Zonenverlauf führt sie als `version: 1` ohne `changes`
(nicht rücksetzbar).

**Aktionen** (Auswahl der neuen in 3.0):

| Bereich | Aktionen |
|---|---|
| Records | `CREATE`, `UPDATE`, `DELETE`, `BULK_UPDATE`, `RECORD_ROLLBACK`, `DYNDNS_UPDATE`, `PTR_SYNC` |
| Zone | `IMPORT`, `ZONE_EXPORT`, `ZONE_NOTIFY` |
| DNSSEC | `DNSSEC_ENABLE`, `DNSSEC_DISABLE`, `DNSSEC_NSEC3_UPDATE`, `KEY_CREATE`, `KEY_ACTIVATE`, `KEY_DEACTIVATE`, `KEY_PUBLISH`, `KEY_UNPUBLISH`, `KEY_UPDATE`, `KEY_DELETE` |
| Anmeldung, Tokens | `LOGIN`, `LOGIN_FAILED` (mit `method`: `password`, `passkey`, `ldap`, `oidc`, ggf. `+totp`, `setup`), `PANEL_TOKEN_CREATE`, `PANEL_TOKEN_UPDATE`, `PANEL_TOKEN_DELETE`, `DYNDNS_TOKEN_*`, `DYNDNS_AUTH_FAILED`, `USER_SSO_LINK` |
| Benutzer | `USER_PASSWORD_RESET`, `USER_PASSWORD_RESET_LINK`, `USER_2FA_RESET`, `USER_PASSKEYS_RESET`, `USER_ACCESS_REVOKE`, `USER_CONVERT_LOCAL`, `USER_ROLE_SYNC`, `PANEL_TOKEN_ADMIN_REVOKE`, `PASSWORD_RESET` |
| Einstellungen | `SMTP_TEST`, `CAPTCHA_UPDATE`, `APP_INFO_UPDATE`, `APP_LOGO_UPLOAD`, `SSO_SETTINGS_UPDATE`, `SSO_SETTINGS_TEST`, `DYNDNS_SETTINGS_UPDATE`, `PTR_SETTINGS_UPDATE`, `PROPAGATION_SETTINGS_UPDATE`, `METRICS_SETTINGS_UPDATE`, `METRICS_TOKEN_CREATE`, `METRICS_TOKEN_DELETE`, `LUA_SETTINGS_UPDATE`, `WEBHOOK_CREATE`, `WEBHOOK_UPDATE`, `WEBHOOK_SECRET_ROTATE`, `WEBHOOK_DELETE`, `WEBHOOK_DELIVERY_RETRY` |
| System | `AUDIT_PURGE`, `AUDIT_SETTINGS_UPDATE`, `SECRETS_MIGRATE`, `SECRETS_ROTATE`, `SECRETS_KEY_GENERATED`, `SECRETS_RESET_UNREADABLE`, `SECRETS_DECRYPT_ALL`, `DOWNGRADE_PREPARED` |

`resource_type`-Werte sind klein geschrieben (`record`, `zone`, `dnssec_key`, `user`, `settings`, …).
Geheimnisse, Passwörter, Tokens und Codes stehen nie im Audit-Log.

---

## Propagations-Check

`GET /zones/{server}/{zone}/propagation?name=&type=&content=` (Leserecht; auch Panel-Tokens mit Zonen-Scope).

- Ohne Parameter: Serial-Vergleich der Panel-Server (Serial, `notified_serial`), der autoritativen Nameserver der Zone
  und der eingestellten Resolver. `name` + `type` vergleichen zusätzlich einen Record, `content=true` den kompletten
  Zoneninhalt zwischen den Panel-Servern.
- Nameserver und Resolver werden erst nach Freischaltung unter Einstellungen → Monitoring gefragt (Standard-Resolver
  1.1.1.1, 8.8.8.8, 9.9.9.9; höchstens 10, nur IP-Adressen); bis dahin nur die Panel-Server.
- Höchstens 8 Sekunden je Prüfung, Ergebnisse 10 Sekunden zwischengespeichert (`cached`), 10 Prüfungen je Benutzer
  und Minute (429).
- Antwort: `expected_serial`, `separate_backends` (Zone auf mehreren schreibbaren Servern mit eigener Datenbank – dann
  zählt der gleiche Inhalt, nicht die gleiche Serial), `nameservers`, `external`, `sources` (je Quelle `status`,
  `serial`, `match`, `latency_ms`, `notes` …) und `summary` (`total`, `ok`, `mismatch`, `failed`, `skipped`, `in_sync`).
- Fehler: Referenz-Server nicht geladen → 503, nicht erreichbar → 503, Zeitüberschreitung → 504, Zone fehlt → 404,
  `name` ohne `type` oder nicht vergleichbarer Typ → 422.

---

## LUA-Records

- **Format:** Typ `LUA`, Inhalt `<Ziel-Typ> "<Lua-Code>"`, z. B. `A "ifportup(443, {'192.0.2.1', '192.0.2.2'})"`.
  Ziel-Typen: A, AAAA, CNAME, TXT, MX, SRV, PTR, CAA, NAPTR, LOC, SPF, HTTPS, SVCB, SSHFP, TLSA. Höchstens 4000 Zeichen
  insgesamt, einzeilig, keine doppelten Anführungszeichen im Code (Strings in `'…'`).
- **Policy** (Einstellungen → DNS-Optionen → LUA-Records): `admin` (Standard) – nur Admins, per Token nur mit
  `allow_admin`; `manage` – alle mit Schreibrecht auf die Zone; `disabled` – niemand. Löschen ist immer erlaubt.
  Verstöße → 403 `LUA-Records dürfen nur Administratoren anlegen oder ändern.` bzw.
  `LUA-Records sind in diesem Panel deaktiviert (Einstellungen → DNS-Optionen).`
- `GET /lua/policy` (angemeldet, auch Tokens): `policy`, `can_write`, `reason`, `target_types`, `max_content_length`.
- `GET /lua/server-status[?refresh=true]`: je PowerDNS-Server `lua_records` (`yes`/`shared`/`no`), `geoip_backend`,
  `edns_subnet_processing`, `exec_limit`; 60 Sekunden Cache, `refresh` wirkt nur für Admins; nur für Admins oder
  Benutzer mit mindestens einem Zonenrecht.
- `GET|PUT /settings/lua` (Admin, nur Session): `{"policy": "admin"|"manage"|"disabled"}`.

Wegen der Anführungszeichen den JSON-Body am besten per Heredoc übergeben:

```bash
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" \
  -X POST "$DNSMGR/api/v1/records/master-fra1/example.com." --data-binary @- <<'EOF'
{"name": "app.example.com.", "type": "LUA", "ttl": 60,
 "records": [{"content": "A \"ifportup(443, {'192.0.2.1', '192.0.2.2'})\""}]}
EOF
```

PowerDNS wertet LUA-Records nur mit `enable-lua-records=yes` (oder `shared`) in der `pdns.conf` aus.

---

## DynDNS

### Update

| Endpunkt | Antwort |
|---|---|
| `GET /nic/update?hostname=<fqdn>[,<fqdn>…]&myip=<ip>` | Text, eine Zeile je Hostname (dyndns2-kompatibel) |
| `GET /api/v1/dyndns/update?…` | JSON |
| `POST /api/v1/dyndns/update` | JSON; optionaler Body `{"hostname", "myip", "myipv4", "myipv6"}` überschreibt die Query-Werte |
| `GET /api/v1/dyndns/whoami` | prüft den Token ohne Änderung (`token_name`, `hostnames`, `allowed_types`, `ttl`, `update_ptr`, `client_ip`) |

- **Token** (`dnsmgr_ddns_…`) als **Basic-Auth-Passwort** (Benutzername beliebig) oder `Authorization: Bearer`.
  **Nie in die URL:** Steht ein Token oder ein Parameter wie `password`, `token`, `key`, `secret` im Query-String, wird
  die Anfrage mit `badauth` abgelehnt und ein gültiger Token sofort gesperrt (`DYNDNS_TOKEN_REVOKED`); er braucht dann
  ein neues Secret.
- **IP-Adressen:** `myip` (Komma-Liste, IPv4 und/oder IPv6), `myipv4`, `myipv6`; Platzhalter wie `<ipaddr>` werden
  ignoriert. Ohne Angabe gilt die Client-IP (hinter einem Proxy nur mit `TRUST_PROXY_HEADERS=true`). Private Adressen
  nur, wenn ein Admin sie erlaubt.
- Ohne `hostname` gilt der einzige Hostname des Tokens. Höchstens 20 Hostnamen.
- Unveränderte Werte schreiben nichts (`nochg`), erhöhen keinen Serial und erzeugen kein Audit. Führt die Zone auf
  mehreren schreibbaren Servern, wird ein veralteter Server nachgezogen (`nochg`, Audit mit `details.repair`).

| Code | Bedeutung | HTTP (Text / JSON) |
|---|---|---|
| `good <ip>[,<ip>]` | geändert | 200 |
| `nochg <ip>[,<ip>]` | unverändert | 200 |
| `badauth` | Token fehlt, falsch, deaktiviert, gesperrt; DynDNS global aus; Token in der URL; Browser-Anfrage von fremder Seite | 401 bzw. 403 |
| `nohost` | Hostname nicht im Token, keine verwaltete Zone oder kein Schreibrecht | 200 |
| `notfqdn` | kein gültiger Hostname | 200 / 400 |
| `numhost` | mehr als 20 Hostnamen | 200 / 400 |
| `badip` | IP ungültig, nicht erlaubt (z. B. privat) oder Typ für den Token nicht freigegeben | 200 / 400 |
| `dnserr` | PowerDNS hat abgelehnt (z. B. CNAME am Namen) | 200 |
| `911` | PowerDNS nicht erreichbar, interner Fehler oder zu viele Anfragen – der Client soll später wiederholen | 429 (Drosselung) bzw. 503 (alle Hostnamen `911`), mit `Retry-After` |
| `abuse` | Token überschreitet das Limit um ein Vielfaches | 429 |

Grenzen: Ein gültiger Token darf 30 Anfragen in 5 Minuten stellen, darüber `911`, ab 300 `abuse`. Anfragen ohne
gültigen Token: nach 20 Fehlversuchen in 15 Minuten (bzw. 120 Anfragen in 5 Minuten) wird die IP (IPv6 je /64)
gedrosselt; ein gültiger Token funktioniert von dieser IP weiter. Die Grenzen gelten je Backend-Prozess.

JSON-Antwort:

```json
{"result": "good", "client_ip": "198.51.100.7", "ip_source": "param",
 "hosts": [{"hostname": "home.example.com.", "zone": "example.com.", "result": "good", "ips": ["198.51.100.7"],
            "changes": [{"type": "A", "status": "updated", "old": ["198.51.100.4"], "new": "198.51.100.7"}],
            "fanout": {"master-fra1": "saved"}}]}
```

```bash
# FRITZ!Box-Update-URL (Benutzername beliebig, Kennwort = Token):
#   https://dns.example.com/nic/update?hostname=<domain>&myip=<ipaddr>,<ip6addr>
curl -sS -u "dyndns:dnsmgr_ddns_…" "https://dns.example.com/nic/update?hostname=home.example.com"
```

### Tokens verwalten (nur Session)

`GET|POST /api/v1/dyndns/tokens`, `PUT|DELETE /api/v1/dyndns/tokens/{id}`, `POST …/{id}/rotate` (neues Secret),
`GET /api/v1/dyndns/zones`; Admins zusätzlich `GET /api/v1/dyndns/admin/tokens` (fremde Tokens nur
aktivieren/deaktivieren/löschen). Anlegen: `{"name", "hostnames": […], "allowed_types": ["A", "AAAA"], "ttl": 60,
"update_ptr": false}` (TTL 60–86400, höchstens 50 Tokens je Benutzer); der Klartext steht einmal in
`plaintext_token`. Ein Token, dessen Secret gesperrt ist (Token in der URL oder „Alle Zugänge widerrufen“), lässt sich
erst nach `rotate` wieder aktivieren (`PUT` mit `is_active: true` → 409). Global an/aus und private IPs:
`GET|PUT /api/v1/settings/dyndns` (Admin, Session).

---

## PTR-Pflege

- `manage_ptr` an den Record-Endpunkten und `/bulk` (nur A/AAAA): Für neue Werte wird der PTR gesetzt, für entfernte
  bzw. deaktivierte Werte entfernt – nur in Reverse-Zonen, die ein schreibbarer Panel-Server führt, und nur mit
  Schreibrecht des Benutzers (inkl. Token-Scope). Ein PTR, der auf einen anderen Namen zeigt, wird nie überschrieben
  oder entfernt; Classless-Delegationen (RFC 2317) werden erkannt und ausgelassen. PTR-Probleme brechen die
  eigentliche Änderung nie ab.
- Jede geschriebene Reverse-Zone bekommt einen eigenen Audit-Eintrag `PTR_SYNC` (rücksetzbar) und das Ereignis
  `record.ptr_synced`. Ein Rollback der Forward-Änderung setzt PTRs nicht mit zurück.
- `GET /ptr/config` – Admin-Standard `auto_default` und Zahl der beschreibbaren Reverse-Zonen;
  `GET /ptr/lookup?ip=&name=&server=` – was die PTR-Pflege für eine IP täte (`status`: `ok`, `no_reverse_zone`,
  `classless`, `forbidden`, `error`; `would`: `set`, `unchanged`, `conflict`). Zonennamen und vorhandene PTRs nur mit
  Leserecht auf die Reverse-Zone.
- Admin-Standard: `GET|PUT /settings/ptr` (`{"auto_default": false}`, Session).

---

## Webhooks

### Verwaltung

Alle Pfade unter `/api/v1/auth/me/webhooks`; außer der Liste nur mit Browser-Session.

| Aufruf | Zweck |
|---|---|
| `GET ""` | Liste mit `url_display` (nur Host), `has_url`, `has_secret`, `scope`, Zählern, `stats` je Status, `available_events`, `worker_enabled`, `worker_running`, `max_attempts`, `retention_days`, `max_webhooks`. Per Token ohne `url` |
| `POST ""` | anlegen: `name`, `url`, `events` (Standard `["*"]`), `scope` (`own` oder `zones`), `is_active`; Antwort mit einmaligem `secret`. Höchstens 20 Webhooks je Benutzer |
| `PUT /{id}` | nur gesendete Felder ändern; `rotate_secret: true` liefert `new_secret` |
| `DELETE /{id}` | löscht auch das Zustellprotokoll |
| `POST /{id}/test` | `webhook.test` sofort senden, auch an deaktivierte Webhooks; Antwort `{success, message, delivery}` |
| `GET /{id}/deliveries?limit=&offset=&status=&event=` | Zustellprotokoll, neueste zuerst |
| `GET /{id}/deliveries/{delivery}` | mit gesendetem `body` und `request_headers` |
| `POST /{id}/deliveries/{delivery}/retry` | erneut senden; 409, wenn bereits eingeplant, der Webhook deaktiviert oder das Secret nicht lesbar ist |

- **`scope`:** `own` = nur Änderungen des Besitzers; `zones` = zusätzlich Änderungen anderer in Zonen, auf die der
  Besitzer ein Zonenrecht hat (Admins: alle Zonen). Maßgeblich sind die Rechte beim Einreihen des Ereignisses.
- **`events`:** `*`, eine Kategorie (`record`, `zone`, `dnssec`, `dyndns` bzw. `record.*`) oder einzelne Ereignisse aus
  dem Katalog; höchstens 30 Einträge. Unbekannte Namen → 400.
- Ziel-URLs in privaten oder internen Netzen werden abgelehnt (bei jedem Versuch geprüft), außer mit
  `WEBHOOK_ALLOW_PRIVATE_URLS=true`. Keine Weiterleitungen.

### Zustellung

- Jedes Ereignis wird in derselben Transaktion wie die Änderung in eine Warteschlange geschrieben und im Hintergrund
  zugestellt (`POST`, `Content-Type: application/json; charset=utf-8`).
- **Wiederholungen:** Bei Verbindungsfehler, Zeitüberschreitung oder einem HTTP-Status außer 2xx folgt der nächste
  Versuch nach ca. 1, 2, 4, 8 und 16 Minuten; nach 6 Versuchen ist die Zustellung `dead`. `Retry-After` bei 429/503 wird
  beachtet (höchstens 1 Stunde). HTTP 410 und gesperrte Ziele beenden sofort.
- **Mindestens einmal, ohne feste Reihenfolge:** Jeder Versuch sendet exakt denselben Body mit derselben Signatur.
  Doppelte Zustellungen am Header `X-DNS-Manager-Delivery` (bzw. `delivery_id`) erkennen. Ein Ereignis an mehrere
  Webhooks hat dieselbe `event_id`.
- Status: `queued`, `in_progress`, `succeeded`, `failed` (wartet auf Wiederholung), `dead`, `cancelled` (Webhook
  deaktiviert/gelöscht oder Zugänge widerrufen). Das Protokoll wird nach 30 Tagen gelöscht.
- Fehlercodes: `http_status`, `redirect`, `gone`, `connect_error`, `timeout`, `total_timeout`, `dns_error`,
  `ssrf_blocked`, `invalid_url`, `internal_error`, `webhook_inactive`, `webhook_deleted`, `owner_inactive`,
  `interrupted`, `secret_unreadable`, `url_unreadable`.
- Mit `BACKGROUND_WORKERS_ENABLED=false` werden Ereignisse nur gesammelt.

### Header

| Header | Inhalt |
|---|---|
| `X-DNS-Manager-Signature` | `sha256=<hex>` – HMAC-SHA256 über den rohen Body mit dem Webhook-Secret |
| `X-DNS-Manager-Event` | Ereignisname |
| `X-DNS-Manager-Delivery` | Zustell-ID (UUID, bleibt über Wiederholungen gleich) |
| `X-DNS-Manager-Attempt` | Nummer des Versuchs (ab 1) |
| `User-Agent` | `PDNS-Manager-Webhook/<version>` |

### Payload (Version 2)

```json
{
  "v": 2,
  "event": "record.created",
  "event_id": "1b0c6c9e-6d0f-4f7e-9a51-0b7a6f3d2e11",
  "delivery_id": "8f3e2a41-2c55-4a0b-8d0e-5b6f9e7c1a22",
  "timestamp": "2026-10-01T09:30:12.123456+00:00",
  "app": "PDNS Manager",
  "app_version": "3.0.0",
  "actor_user_id": 3,
  "actor": {"user_id": 3, "username": "jdoe", "via": "panel_token"},
  "zone": "example.com.",
  "server": "master-fra1",
  "audit_log_id": 1234,
  "data": {
    "server": "master-fra1", "zone": "example.com.", "name": "www.example.com.", "type": "A", "ttl": 300,
    "added": ["203.0.113.10"],
    "changes": [{"name": "www.example.com.", "type": "A", "before": null,
                 "after": {"ttl": 300, "records": [{"content": "203.0.113.10", "disabled": false}]}}],
    "fanout": {"master-fra1": "saved", "master-ams1": "saved"}
  }
}
```

`timestamp` ist der Zeitpunkt des Ereignisses, nicht des Versands. `actor_user_id` bleibt aus Version 1 erhalten,
ebenso die bisherigen Felder in `data`. Der Body ist ASCII-JSON. Ist er größer als 512 KiB, werden zuerst die
`changes` entfernt (`changes_truncated`, `changes_count`), notfalls `data` durch `{"truncated": true}` ersetzt.
`changes` enthält höchstens 200 Einträge (sonst `changes_truncated`/`changes_count`).

### Ereignisse

| Ereignis | `data` (zusätzlich zu `changes`/`fanout`, wo vorhanden) |
|---|---|
| `record.created` | `server`, `zone`, `name`, `type`, `ttl`, `added`, ggf. `ptr` |
| `record.updated` | `server`, `zone`, `name`, `type`, `ttl`, `old_content`, `new_content`, ggf. `ptr` |
| `record.deleted` | `server`, `zone`, `name`, `type`, `content` (`null` = ganzes RRset), ggf. `ptr` |
| `record.bulk` | `server`, `zone`, `created`, `deleted`, `source`, `mode`, `changes_total`, ggf. `ptr` |
| `record.rollback` | `server`, `zone`, `reverted_audit_log_id`, `forced` |
| `record.ptr_synced` | `server`, `zone` (Reverse-Zone), `source` (auslösende Aktion) |
| `zone.created` | `zone`, `kind`, `nameservers`, `dnssec`, `results` |
| `zone.updated` | `zone`, `server`, `changed` |
| `zone.deleted` | `zone`, `server`, `zone_still_on_other_server` |
| `zone.imported` | `zone`, `kind`, `content_length`, `results` |
| `dnssec.enabled` | `zone`, `server`, `algorithm`, `key_model`, `nsec3param`, `keys` (ohne private Teile, mit `ds`) |
| `dnssec.disabled` | `zone`, `server`, `deleted_key_ids` |
| `dnssec.key_created` | `zone`, `server`, `key_id`, `keytype`, `algorithm`, `bits`, `active`, `published`, `key_tag`, `ds` |
| `dnssec.key_activated`, `…_deactivated`, `…_published`, `…_unpublished` | Schlüsselangaben (`key_id`, `key_tag`, …) |
| `dnssec.key_deleted` | `zone`, `server`, `key_id`, `key_tag`, `keytype`, `algorithm`, `force` |
| `dnssec.nsec3_changed` | `zone`, `server`, `nsec3param`, `nsec3narrow`, `before` |
| `dyndns.updated` | `server`, `zone`, `hostname`, `type`, `ttl`, `old`, `new`, `token` (`id`, `prefix`, `name`), ggf. `ptr` – nur bei echter Änderung |
| `webhook.test` | `webhook_id`, `webhook_name`, `message` – nur über „Test senden“, nicht abonnierbar |

Ereignisse aus den DNSSEC-Endpunkten enthalten zusätzlich `serial_bumped`. Login-, Benutzer-, Token- und Einstellungsänderungen lösen
keine Webhooks aus.

### Signatur prüfen

Immer den **rohen** Request-Body verwenden, nicht das geparste JSON.

```python
import hmac, hashlib

SECRET = b"dein-webhook-secret"  # aus dem Panel

def verify(raw_body: bytes, signature_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(SECRET, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
```

```js
import crypto from "node:crypto";

const SECRET = "dein-webhook-secret";

export function verify(rawBody, signatureHeader) {
  if (!signatureHeader?.startsWith("sha256=")) return false;
  const expected = crypto.createHmac("sha256", SECRET).update(rawBody).digest("hex");
  const given = signatureHeader.slice(7);
  if (given.length !== expected.length) return false;
  return crypto.timingSafeEqual(Buffer.from(expected, "hex"), Buffer.from(given, "hex"));
}
```

---

## Metriken

### Prometheus `GET /metrics`

- Aus, solange kein Scrape-Token besteht: dann 404. Token im Panel unter Einstellungen → Monitoring erzeugen (wird
  einmal angezeigt und verschlüsselt gespeichert) oder `METRICS_TOKEN` (mindestens 24 Zeichen) setzen – dann ist die
  Panel-Einstellung gesperrt (409).
- Aufruf mit `Authorization: Bearer <token>`; falscher oder fehlender Token → 401. Antwort im Prometheus-Textformat.
- Labels verwenden Routen-Vorlagen, nie konkrete Zonennamen.

| Metrik | Typ | Labels |
|---|---|---|
| `pdnsmgr_info` | Gauge | `version` |
| `pdnsmgr_http_requests_total` | Counter | `method`, `route`, `status` |
| `pdnsmgr_http_request_duration_seconds` | Histogram | `method`, `route` |
| `pdnsmgr_pdns_api_requests_total` | Counter | `server`, `method`, `status` |
| `pdnsmgr_pdns_api_request_duration_seconds` | Histogram | `server` |
| `pdnsmgr_pdns_server_up` | Gauge | `server` (nicht geladene Server = 0) |
| `pdnsmgr_pdns_zones` | Gauge | `server` |
| `pdnsmgr_pdns_servers_configured` | Gauge | – |
| `pdnsmgr_database_up` | Gauge | – |
| `pdnsmgr_webhook_deliveries_total` | Counter | `status` (`success`, `retry`, `dead`, `cancelled`) |
| `pdnsmgr_webhook_deliveries_pending` | Gauge | – |
| `pdnsmgr_dyndns_updates_total` | Counter | `result` (`good`, `nochg`, `badauth`, `nohost`, `notfqdn`, `numhost`, `badip`, `abuse`, `dnserr`, `911`, …) |
| `pdnsmgr_login_attempts_total` | Counter | `method`, `result` |
| `pdnsmgr_propagation_checks_total` | Counter | `result` (`in_sync`, `out_of_sync`, `failed`, `rate_limited`, `cached`) |
| `pdnsmgr_metrics_auth_failures_total` | Counter | – |
| `pdnsmgr_migration_errors` | Gauge | – |
| `pdnsmgr_background_task_running` | Gauge | `task` |
| `pdnsmgr_secrets_unreadable_reads` | Gauge | `field` |

Dazu die Standard-Prozessmetriken von `prometheus_client` (`process_*`, `python_*`).

### Weitere

- `GET /api/v1/metrics` (Admin; per Token nur mit `allow_admin`): `{app, version, uptime_seconds, api_request_count}`.
- `GET /api/v1/settings/monitoring/status` (Admin, Session): Hintergrund-Aufgaben, Migrationsfehler, nicht geladene
  Server, Zustand der Verschlüsselung.
- `GET /health` für Uptime-Checks (siehe [Grundlagen](#grundlagen)).

---

## ACME / DNS-01

ACME-Tokens (`dnsmgr_acme_…`) legen Admins unter **Einstellungen → ACME / Auto-TLS** an, mit einer Liste erlaubter
Zonen. Sie dürfen ausschließlich `_acme-challenge.*`-TXT-Records in diesen Zonen anlegen und entfernen.

- `POST /api/v1/acme/present` und `POST /api/v1/acme/cleanup` mit `{"domain": "smtp.example.com", "validation": "<Wert>"}`.
- `GET /api/v1/acme/whoami` prüft den Token.
- Fertiger certbot-Hook: `scripts/certbot-dns-dnsmanager.sh`.

---

## Endpunkt-Übersicht

Alle 166 Routen des Backends (ohne `HEAD` und die Auslieferung der Oberfläche). Pfade mit `/api/v1`, Platzhalter
`{server}` = Servername, `{zone}` = Zonenname mit Punkt. Spalte „Zugang“ siehe
[Zugangsklassen](#zugangsklassen-der-endpunkte).

#### Einrichtung und Anmeldung

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/setup/status` | öffentlich | Einrichtungsstatus |
| POST | `/api/v1/setup/register` | öffentlich | ersten Admin anlegen (nur während der Einrichtung) |
| POST | `/api/v1/auth/login` | öffentlich | Anmeldung (Formular: `username`, `password`, optional `totp_code`, `captcha_token`); setzt das Session-Cookie |
| POST | `/api/v1/auth/login/2fa` | öffentlich | 2FA-Schritt (`totp_code`, `two_factor_token` optional) |
| POST | `/api/v1/auth/logout` | öffentlich | Abmelden (Cookie löschen) |
| POST | `/api/v1/auth/register` | öffentlich | Selbst-Registrierung (wenn freigeschaltet) |
| POST | `/api/v1/auth/forgot-password` | öffentlich | Reset-Link anfordern |
| POST | `/api/v1/auth/reset-password` | öffentlich | Passwort per Reset-Link setzen |
| POST | `/api/v1/auth/webauthn/login/begin` | öffentlich | Passkey-Anmeldung (Start) |
| POST | `/api/v1/auth/webauthn/login/complete` | öffentlich | Passkey-Anmeldung (Abschluss) |
| GET | `/api/v1/auth/sso/providers` | öffentlich | SSO-Anbieter für die Login-Seite (ohne Secrets) |
| GET | `/api/v1/auth/oidc/start` | öffentlich | OIDC-Anmeldung starten (Weiterleitung) |
| GET | `/api/v1/auth/oidc/callback` | öffentlich | OIDC-Rücksprung (Redirect-URI beim Anbieter) |

#### Eigenes Konto

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/auth/me` | angemeldet | eigenes Konto inkl. Block `auth` (Zugangsart, Token-Scope) |
| PUT | `/api/v1/auth/me` | Session | Profil ändern |
| PUT | `/api/v1/auth/me/password` | Session | eigenes Passwort ändern |
| GET | `/api/v1/auth/me/totp/status` | Session | 2FA-Status |
| POST | `/api/v1/auth/me/totp/begin` | Session | 2FA einrichten (Start) |
| POST | `/api/v1/auth/me/totp/enable` | Session | 2FA aktivieren |
| POST | `/api/v1/auth/me/totp/disable` | Session | 2FA abschalten |
| GET | `/api/v1/auth/me/webauthn/credentials` | Session | eigene Passkeys |
| POST | `/api/v1/auth/me/webauthn/register/begin` | Session | Passkey registrieren (Start) |
| POST | `/api/v1/auth/me/webauthn/register/complete` | Session | Passkey registrieren (Abschluss) |
| DELETE | `/api/v1/auth/me/webauthn/credentials/{cred_id}` | Session | Passkey löschen |
| POST | `/api/v1/auth/me/sso/oidc/link` | Session | eigenes Konto mit OIDC verknüpfen |
| POST | `/api/v1/auth/me/sso/ldap/link` | Session | eigenes Konto mit LDAP verknüpfen |

#### Panel-API-Tokens

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/auth/me/panel-tokens` | Session | eigene Panel-Tokens |
| POST | `/api/v1/auth/me/panel-tokens` | Session | Panel-Token anlegen (Klartext einmal in der Antwort) |
| PUT | `/api/v1/auth/me/panel-tokens/{token_id}` | Session | Panel-Token ändern, pausieren, Laufzeit setzen |
| DELETE | `/api/v1/auth/me/panel-tokens/{token_id}` | Session | Panel-Token widerrufen (endgültig) |
| GET | `/api/v1/auth/users/{user_id}/panel-tokens` | Session, Admin | Tokens eines Benutzers ansehen |
| DELETE | `/api/v1/auth/users/{user_id}/panel-tokens/{token_id}` | Session, Admin | einen Token eines Benutzers widerrufen |
| DELETE | `/api/v1/auth/users/{user_id}/panel-tokens` | Session, Admin | alle Tokens eines Benutzers widerrufen |

#### Benutzerverwaltung

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/auth/users` | Admin | Benutzerliste (mit `auth_source`, `panel_token_count`, Block `sso`) |
| POST | `/api/v1/auth/users` | Session, Admin | Benutzer anlegen |
| PUT | `/api/v1/auth/users/{user_id}` | Session, Admin | Benutzer ändern (nicht das eigene Konto deaktivieren/herabstufen) |
| DELETE | `/api/v1/auth/users/{user_id}` | Session, Admin | Benutzer löschen |
| PUT | `/api/v1/auth/users/{user_id}/reset-password` | Session, Admin | Zufallspasswort; ohne Body mit erzwungenem Wechsel |
| POST | `/api/v1/auth/users/{user_id}/send-reset-link` | Session, Admin | Reset-Link per E-Mail (24 h gültig) |
| POST | `/api/v1/auth/users/{user_id}/reset-2fa` | Session, Admin | 2FA eines Benutzers zurücksetzen |
| DELETE | `/api/v1/auth/users/{user_id}/webauthn-credentials` | Session, Admin | alle Passkeys eines Benutzers entfernen |
| GET | `/api/v1/auth/users/{user_id}/access-summary` | Admin | Zähler der Zugänge (Tokens, DynDNS, Webhooks, Passkeys, 2FA) |
| POST | `/api/v1/auth/users/{user_id}/revoke-access` | Session, Admin | „Alle Zugänge widerrufen“ |
| POST | `/api/v1/auth/users/{user_id}/convert-to-local` | Session, Admin | SSO-/LDAP-Konto in lokales Konto umwandeln (Bestätigung nötig) |
| PUT | `/api/v1/auth/users/{user_id}/zones` | Session, Admin | Zonenrechte setzen (`read`/`manage`) |
| GET | `/api/v1/auth/users/{user_id}/zones` | Admin | Zonenrechte lesen |

#### Webhooks

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/auth/me/webhooks` | angemeldet | eigene Webhooks (per Token ohne Ziel-URL) |
| POST | `/api/v1/auth/me/webhooks` | Session | Webhook anlegen (Secret einmal in der Antwort) |
| PUT | `/api/v1/auth/me/webhooks/{webhook_id}` | Session | Webhook ändern, `rotate_secret` |
| DELETE | `/api/v1/auth/me/webhooks/{webhook_id}` | Session | Webhook löschen (inkl. Zustellprotokoll) |
| POST | `/api/v1/auth/me/webhooks/{webhook_id}/test` | Session | `webhook.test` sofort senden (höchstens alle 10 s) |
| GET | `/api/v1/auth/me/webhooks/{webhook_id}/deliveries` | Session | Zustellprotokoll (`limit`, `offset`, `status`, `event`) |
| GET | `/api/v1/auth/me/webhooks/{webhook_id}/deliveries/{delivery_pk}` | Session | eine Zustellung mit Body und Headern |
| POST | `/api/v1/auth/me/webhooks/{webhook_id}/deliveries/{delivery_pk}/retry` | Session | Zustellung erneut senden |

#### Server und Suche

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/servers` | angemeldet | PowerDNS-Server mit `allow_writes`; `url` nur für Admins |
| GET | `/api/v1/servers/{server}` | angemeldet | Server-Info; `url` nur für Admins |
| GET | `/api/v1/servers/{server}/statistics` | angemeldet | PowerDNS-Statistik (nur Admins, nicht mit Zonen-Token) |
| GET | `/api/v1/search/{server}` | angemeldet | Suche auf einem Server (`q`, `max_results`, `object_type`) |
| GET | `/api/v1/search` | angemeldet | Suche über alle Server |

#### Zonen

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/zones/{server}` | angemeldet | Zonen eines Servers (nur sichtbare) |
| GET | `/api/v1/zones/{server}/{zone}/detail` | Zone: Lesen | Zone mit RRsets |
| POST | `/api/v1/zones` | Admin | Zone anlegen (auf allen schreibbaren bzw. gewählten Servern) |
| PUT | `/api/v1/zones/{server}/{zone}` | Zone: Schreiben | Zone ändern; `kind`, `masters`, `account` nur Admins |
| DELETE | `/api/v1/zones/{server}/{zone}` | Admin | Zone auf diesem Server löschen |
| POST | `/api/v1/zones/{server}/{zone}/notify` | Zone: Schreiben | NOTIFY senden (nur dieser Server) |
| GET | `/api/v1/zones/{server}/{zone}/export` | Zone: Lesen | Export als BIND-Zonendatei (`content`, `filename`) |
| POST | `/api/v1/zones/import/preview` | Admin | Import-Vorschau (Diff, LUA-Felder) |
| POST | `/api/v1/zones/import` | Admin | Zonendatei importieren |

#### Records und Bulk

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/records/{server}/{zone}` | Zone: Lesen | Records einer Zone |
| POST | `/api/v1/records/{server}/{zone}/bulk/preview` | Zone: Schreiben | Bulk-Vorschau (schreibt nichts) |
| POST | `/api/v1/records/{server}/{zone}/bulk` | Zone: Schreiben | Bulk-Änderung |
| POST | `/api/v1/records/{server}/{zone}` | Zone: Schreiben | Werte zu einem RRset hinzufügen bzw. RRset anlegen |
| DELETE | `/api/v1/records/{server}/{zone}/delete` | Zone: Schreiben | Wert (mit `content`) oder ganzes RRset löschen |
| PUT | `/api/v1/records/{server}/{zone}` | Zone: Schreiben | einen Wert ersetzen (`old_content` → `new_content`) |

#### Zonenverlauf, Rollback, Propagation

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/zones/{server}/{zone}/history` | Zone: Lesen | Zonenverlauf (Filter, `total`) |
| GET | `/api/v1/zones/{server}/{zone}/history/{id}/rollback-preview` | Zone: Schreiben | Rollback-Vorschau |
| POST | `/api/v1/zones/{server}/{zone}/history/{id}/rollback` | Zone: Schreiben | Änderung zurücksetzen (`force`) |
| GET | `/api/v1/zones/{server}/{zone}/history/{id}` | Zone: Lesen | ein Verlaufseintrag mit allen `changes` |
| GET | `/api/v1/zones/{server}/{zone}/propagation` | Zone: Lesen | Propagations-Check (`name`, `type`, `content`) |
| GET | `/api/v1/settings/propagation` | Session, Admin | externe DNS-Abfragen, Resolver |
| PUT | `/api/v1/settings/propagation` | Session, Admin | externe DNS-Abfragen freischalten, Resolver setzen |

#### DNSSEC

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/dnssec/{server}/{zone}/status` | Zone: Lesen | DNSSEC-Status, Schlüssel, Rollover-Phase, Hinweise |
| GET | `/api/v1/dnssec/{server}/{zone}/parent-ds` | Zone: Lesen | DS der Elternzone bei den Resolvern |
| GET | `/api/v1/dnssec/{server}/{zone}/dnskey-check` | Zone: Lesen | DNSKEY je autoritativem Nameserver (`key_tag`, max. 12) |
| GET | `/api/v1/dnssec/{server}/{zone}/ds` | Zone: Lesen | DS-Records für den Registrar |
| GET | `/api/v1/dnssec/{server}/{zone}/keys` | Zone: Lesen | Schlüssel (ohne private Teile) |
| POST | `/api/v1/dnssec/{server}/{zone}/keys` | Zone: Schreiben | Schlüssel anlegen |
| POST | `/api/v1/dnssec/{server}/{zone}/enable` | Zone: Schreiben | DNSSEC aktivieren (idempotent) |
| POST | `/api/v1/dnssec/{server}/{zone}/disable` | Zone: Schreiben | DNSSEC deaktivieren (`force`) |
| PUT | `/api/v1/dnssec/{server}/{zone}/nsec3` | Zone: Schreiben | NSEC/NSEC3 ändern |
| POST | `/api/v1/dnssec/{server}/{zone}/keys/{key_id}/activate` | Zone: Schreiben | Schlüssel aktivieren |
| POST | `/api/v1/dnssec/{server}/{zone}/keys/{key_id}/deactivate` | Zone: Schreiben | Schlüssel deaktivieren (`force`) |
| GET | `/api/v1/dnssec/{server}/{zone}/keys/{key_id}` | Zone: Schreiben | ein Schlüssel (ohne privaten Teil) |
| PUT | `/api/v1/dnssec/{server}/{zone}/keys/{key_id}` | Zone: Schreiben | `active`/`published` ändern (`force`) |
| DELETE | `/api/v1/dnssec/{server}/{zone}/keys/{key_id}` | Zone: Schreiben | Schlüssel löschen (`force`) |

#### LUA und PTR

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/lua/policy` | angemeldet | LUA-Policy und `can_write` für den Aufrufer |
| GET | `/api/v1/lua/server-status` | angemeldet | LUA-Status je PowerDNS-Server (mind. ein Zonenrecht; `refresh` nur Admins) |
| GET | `/api/v1/ptr/config` | angemeldet | PTR-Standard und Zahl beschreibbarer Reverse-Zonen |
| GET | `/api/v1/ptr/lookup` | angemeldet | Vorschau der PTR-Pflege für eine IP (`ip`, `name`, `server`) |

#### DynDNS

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/dyndns/update` | DynDNS-Token | DynDNS-Update, JSON-Antwort |
| POST | `/api/v1/dyndns/update` | DynDNS-Token | DynDNS-Update, optional JSON-Body |
| GET | `/api/v1/dyndns/whoami` | DynDNS-Token | DynDNS-Token prüfen, ohne Änderung |
| GET | `/api/v1/dyndns/info` | angemeldet | Grenzen und Basis-URL für die DynDNS-Karte |
| GET | `/api/v1/dyndns/zones` | Session | Zonen, in denen Hostnamen freigegeben werden können |
| GET | `/api/v1/dyndns/tokens` | Session | eigene DynDNS-Tokens |
| POST | `/api/v1/dyndns/tokens` | Session | DynDNS-Token anlegen (Klartext einmal) |
| PUT | `/api/v1/dyndns/tokens/{token_id}` | Session | DynDNS-Token ändern (Admins bei fremden nur `is_active`) |
| POST | `/api/v1/dyndns/tokens/{token_id}/rotate` | Session | neues Secret |
| DELETE | `/api/v1/dyndns/tokens/{token_id}` | Session | DynDNS-Token löschen |
| GET | `/api/v1/dyndns/admin/tokens` | Session, Admin | alle DynDNS-Tokens mit Besitzer |
| GET | `/nic/update` | DynDNS-Token | DynDNS-Update (dyndns2, Textantwort) |

#### ACME

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| POST | `/api/v1/acme/present` | ACME-Token | `_acme-challenge`-TXT anlegen (`domain`, `validation`) |
| POST | `/api/v1/acme/cleanup` | ACME-Token | `_acme-challenge`-TXT entfernen |
| GET | `/api/v1/acme/whoami` | ACME-Token | ACME-Token prüfen |

#### Vorlagen

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/templates` | angemeldet | Zonen-Vorlagen |
| POST | `/api/v1/templates` | Admin | Vorlage anlegen (TTL 60–604800) |
| PUT | `/api/v1/templates/{template_id}` | Admin | Vorlage ändern |
| DELETE | `/api/v1/templates/{template_id}` | Admin | Vorlage löschen |

#### Audit-Log

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/audit-log` | Admin | Audit-Log (Filter, `total`, `full`) |
| GET | `/api/v1/audit-log/export` | Admin | Audit-Log als CSV (gleiche Filter) |
| GET | `/api/v1/audit-log/settings` | Session, Admin | Aufbewahrung lesen |
| PUT | `/api/v1/audit-log/settings` | Session, Admin | Aufbewahrung setzen (`retention_days`) |
| GET | `/api/v1/audit-log/{id}` | Admin | ein Audit-Eintrag ungekürzt |

#### Einstellungen

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/api/v1/settings/app-info` | öffentlich | öffentliche App-Angaben (Name, Logo, Standardsprache, Registrierung) |
| GET | `/api/v1/settings/admin-info` | Session, Admin | Admin-Angaben (Installationspfad, Basis-URL) |
| PUT | `/api/v1/settings/app-info` | Session, Admin | App-Angaben, Basis-URL, Registrierung |
| POST | `/api/v1/settings/app-logo` | Session, Admin | Logo hochladen |
| GET | `/api/v1/settings/servers` | Session, Admin | Server-Konfiguration (`api_key_status`) |
| POST | `/api/v1/settings/servers` | Session, Admin | Server anlegen |
| PUT | `/api/v1/settings/servers/{server_id}` | Session, Admin | Server ändern (`url` nur mit neuem `api_key`) |
| DELETE | `/api/v1/settings/servers/{server_id}` | Session, Admin | Server löschen |
| GET | `/api/v1/settings/servers/{server_id}/api-key` | Session, Admin | API-Key anzeigen (auditiert) |
| POST | `/api/v1/settings/servers/test` | Session, Admin | Verbindung testen |
| GET | `/api/v1/settings/smtp` | Session, Admin | SMTP-Einstellungen (`password_unreadable`) |
| PUT | `/api/v1/settings/smtp` | Session, Admin | SMTP speichern |
| POST | `/api/v1/settings/smtp/test` | Session, Admin | SMTP-Verbindung testen (optional mit Formularwerten) |
| POST | `/api/v1/settings/smtp/test-email` | Session, Admin | Test-Mail senden |
| GET | `/api/v1/settings/captcha` | Session, Admin | Captcha-Einstellungen (`secret_key_unreadable`) |
| PUT | `/api/v1/settings/captcha` | Session, Admin | Captcha speichern |
| POST | `/api/v1/settings/captcha/test` | Session, Admin | Captcha-Token prüfen |
| GET | `/api/v1/settings/welcome-email` | Session, Admin | Welcome-Mail |
| PUT | `/api/v1/settings/welcome-email` | Session, Admin | Welcome-Mail speichern |
| POST | `/api/v1/settings/welcome-email/test` | Session, Admin | Welcome-Mail testen |
| GET | `/api/v1/settings/acme/tokens` | Session, Admin | ACME-Tokens |
| POST | `/api/v1/settings/acme/tokens` | Session, Admin | ACME-Token anlegen |
| DELETE | `/api/v1/settings/acme/tokens/{token_id}` | Session, Admin | ACME-Token löschen |
| GET | `/api/v1/settings/secrets/status` | Session, Admin | Status der Verschlüsselung (nie Werte) |
| GET | `/api/v1/settings/sso` | Session, Admin | SSO-Einstellungen |
| PUT | `/api/v1/settings/sso` | Session, Admin | SSO speichern (kritische Felder mit `step_up`) |
| POST | `/api/v1/settings/sso/test` | Session, Admin | SSO-Verbindung testen |
| GET | `/api/v1/settings/dyndns` | Session, Admin | DynDNS global an/aus, private IPs |
| PUT | `/api/v1/settings/dyndns` | Session, Admin | DynDNS-Einstellungen speichern |
| GET | `/api/v1/settings/ptr` | Session, Admin | PTR-Standard (`auto_default`) |
| PUT | `/api/v1/settings/ptr` | Session, Admin | PTR-Standard setzen |
| GET | `/api/v1/settings/metrics` | Session, Admin | Prometheus-Einstellungen |
| PUT | `/api/v1/settings/metrics` | Session, Admin | `/metrics` ein/aus (409 mit `METRICS_TOKEN`) |
| POST | `/api/v1/settings/metrics/token` | Session, Admin | Scrape-Token erzeugen (einmal angezeigt) |
| DELETE | `/api/v1/settings/metrics/token` | Session, Admin | Scrape-Token löschen |
| GET | `/api/v1/settings/monitoring/status` | Session, Admin | Systemstatus (Hintergrund-Aufgaben, Migrationen, Server, Verschlüsselung) |
| GET | `/api/v1/settings/lua` | Session, Admin | LUA-Policy |
| PUT | `/api/v1/settings/lua` | Session, Admin | LUA-Policy setzen (`admin`, `manage`, `disabled`) |

#### System

| Methode | Pfad | Zugang | Zweck |
|---|---|---|---|
| GET | `/vite.svg` | öffentlich | Icon der Oberfläche |
| GET | `/api/v1/metrics` | Admin | einfache Laufzeitwerte als JSON |
| GET | `/metrics` | Scrape-Token | Prometheus-Scrape |
| GET | `/api` | öffentlich | API-Info (Name, Version, Präfix) |
| GET | `/health` | öffentlich | Health-Check |

---

## Praxis-Tipps

- **Token sparsam anlegen:** nur die nötigen Zonen, Leserecht wo möglich, immer mit Laufzeit; `allow_admin` nur für
  Skripte, die Zonen anlegen/löschen oder das Audit-Log lesen. Den Token nicht ins Git, sondern z. B. in eine Datei mit
  Rechten 600 oder eine Umgebungsvariable des Skripts.
- **Prüfen, was ein Token darf:** `GET /api/v1/auth/me` (Block `auth`).
- **Ergebnis je Server auswerten:** Bei mehreren Servern immer `details` prüfen – `error: …` oder
  `skipped (not loaded: …)` zeigt, wo eine Änderung nicht angekommen ist.
- **Idempotenz:** `POST /records` fügt Werte hinzu, ein bereits vorhandener Wert wird nicht verdoppelt. Wer einen
  Zielzustand setzen will, nimmt `/bulk` mit `create` (ersetzt das RRset). `POST …/dnssec/…/enable` ist idempotent.
- **Gleichzeitige Änderungen:** Für Skripte, die nicht blind überschreiben sollen, erst `…/bulk/preview` aufrufen und die
  zurückgegebenen `ops` (mit `expected`) unverändert an `/bulk` senden; ein 409 heißt, dass jemand dazwischen geändert hat.
- **Webhooks entgegennehmen:** Signatur über den rohen Body prüfen, Duplikate über `X-DNS-Manager-Delivery` erkennen,
  schnell mit 2xx antworten und längere Arbeit asynchron erledigen.
- **Nicht mit Benutzername/Passwort skripten:** Login-Endpunkte sind gedrosselt und unterliegen 2FA und SSO; für
  Automatisierung einen Panel-Token verwenden.
