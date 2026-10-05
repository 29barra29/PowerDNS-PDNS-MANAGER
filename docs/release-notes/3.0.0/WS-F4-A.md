### DNSSEC: Optionen, Schluesselverwaltung und sichere Schutzregeln (WS-F4-A, Backend; Oberflaeche folgt mit F4-B)

#### Neu
- **Aktivieren mit Optionen:** Schluesselmodell (`csk` oder `ksk_zsk`), Algorithmus (ECDSA P-256 als Standard, P-384,
  Ed25519, Ed448, RSA-SHA256/512 mit 2048/3072/4096 Bit) und NSEC oder NSEC3 (Iterationen, Salt, Opt-Out, Narrow).
  Neuer Standard ist NSEC3 `1 0 0 -` nach RFC 9276 (bisher `1 0 1 ab`); bestehende Zonen bleiben unveraendert. Das
  alte Feld `nsec3param` funktioniert weiter.
- **Schluesselverwaltung per API:** Status mit Key-Tag und DS-Status, NSEC/NSEC3, Rollover-Phase, Vergleich mit
  anderen Servern, Hinweisen und PowerDNS-Version; Schluessel anlegen (z. B. fuer einen Schluesselwechsel),
  aktivieren/deaktivieren, veroeffentlichen/zurueckziehen (ab PowerDNS 4.3); NSEC/NSEC3 nachtraeglich aendern.
- **Schutzregeln:** Der letzte aktive Schluessel, der letzte aktive KSK/CSK und der letzte veroeffentlichte aktive
  KSK/CSK lassen sich nicht versehentlich abschalten oder loeschen (409 mit `detail.code`, z. B. `last_active_key`).
  Bewusst uebersteuern geht mit `force` – das steht dann im Audit-Log.
- **Deaktivieren in sicherer Reihenfolge:** erst NSEC3-Parameter entfernen, dann inaktive, dann aktive Schluessel;
  bricht PowerDNS mittendrin ab, nennt die Meldung, was schon geloescht ist.
- **Secondaries bekommen Aenderungen mit:** Bei Zonen vom Typ Master/Producer erhoeht das Panel nach jeder
  Schluessel- oder NSEC-Aenderung den SOA-Serial und sendet NOTIFY (abschaltbar mit `bump_serial: false`). Beides steht
  im Audit-Log; scheitert NOTIFY, bleibt die Aenderung gueltig und die Antwort nennt den Fehler.
- **Audit und Webhooks:** Auch fehlgeschlagene Schluessel-Aktionen werden protokolliert. Neue Audit-Aktionen
  `KEY_CREATE`, `KEY_PUBLISH`, `KEY_UNPUBLISH`, `KEY_UPDATE`, `DNSSEC_NSEC3_UPDATE`; neue Webhook-Ereignisse
  `dnssec.key_created`, `dnssec.key_published`, `dnssec.key_unpublished`, `dnssec.nsec3_changed`.

#### Geändert / Breaking
- **Kein doppelter Schluessel mehr:** `POST …/enable` auf einer schon signierten Zone antwortet mit 200 und
  `already_enabled`, statt einen weiteren CSK anzulegen. Gibt es nur inaktive Schluessel, kommt 409. Scheitert ein
  Schritt, werden bereits angelegte Schluessel wieder entfernt.
- **„Speichern: Nein“ gilt jetzt auch fuer DNSSEC:** Alle schreibenden DNSSEC-Endpunkte antworten auf solchen Servern
  mit 403. Vorsignierte Zonen (PRESIGNED) werden nicht veraendert (409).
- `activate`/`deactivate`/`DELETE` koennen jetzt mit 409 (Schutzregel) bzw. 403 („Speichern: Nein“) antworten;
  `zone` in den Antworten ist normalisiert (klein, mit Punkt am Ende).
- DNSSEC-Aenderungen an Master-/Producer-Zonen erhoehen den SOA-Serial (siehe Neu).
- **Fehlertexte:** PowerDNS-Fehler erscheinen lesbar als `PowerDNS (<server>): <meldung>`; interne Server-Adressen
  werden in DNSSEC-Antworten nicht mehr angezeigt. Private Schluessel verlassen das Panel nie.

#### API
- Neu: `GET /api/v1/dnssec/{server}/{zone}/status`, `POST …/keys`, `GET|PUT …/keys/{id}`, `PUT …/nsec3`.
- Geaendert: `POST …/enable` (Body mit `key_model`, `algorithm`, `bits`, `nsec_mode` und NSEC3-Optionen; `nsec3param`
  weiter unterstuetzt; idempotent), `POST …/disable`, `POST …/keys/{id}/activate|deactivate`, `DELETE …/keys/{id}`
  (Schutzregeln, `force`, `bump_serial`).
- Mutationsantworten enthalten `serial_bumped`, `serial`, `serial_error`, `notified`, `notify_error`.
- Fehlerformat bei Schutzregeln: `{"detail": {"message", "code", "force_possible"}}` mit den Codes
  `last_active_key`, `last_active_sep`, `last_published_sep`.

#### Nach dem Update prüfen
- Skripte, die `enable` mehrfach aufrufen oder Schluessel per API abschalten: Antwort `already_enabled` bzw. 409 mit
  `detail.code` beachten; bewusstes Uebersteuern nur mit `force`.
- Bei Zonen mit Secondaries: nach einer Schluesselaenderung pruefen, ob NOTIFY ankam (Antwortfeld `notified`).
- Server mit „Speichern: Nein“: DNSSEC-Aenderungen dort sind jetzt gesperrt (403).

#### Website-Seiten (DE/EN)
- `features/dnssec`: Beschreibung „Schluessel-Operationen per API“ ersetzen; „DNSSEC aktivieren“ mit Optionen und
  Algorithmus-Tabelle, neuer NSEC3-Standard `1 0 0 -`; „Algorithmus 1 = SHA-1“ korrigieren zu **Digest-Typ**;
  Schutzregeln und `force`; Serial-Erhoehung und NOTIFY; Abschnitt „Mehrere Server“ (nur der Server aus der URL,
  getrennte Datenbanken: manueller Abgleich, Status-Warnung). Die UI-Teile (Dialog, Rollover-Assistent) folgen mit F4-B.
- `features/panel-api`: Endpunktliste um `status`, `keys` (POST/PUT/DELETE), `nsec3`; Beispiel mit Optionen und
  `force`; Fehlerformat bei Schutzregeln.
- `features/multi-server`: DNSSEC wird nicht verteilt, Warnungen bei abweichenden Schluesseln.
- `features/audit-log`: Aktionen `KEY_CREATE`, `KEY_PUBLISH`, `KEY_UNPUBLISH`, `KEY_UPDATE`, `DNSSEC_NSEC3_UPDATE`;
  Fehler-Eintraege bei Schluessel-Aktionen.
- `features/webhooks`: Ereignisse `dnssec.*`.
- `features/benutzer-rollen`: „Speichern: Nein“ blockiert alle DNSSEC-Aenderungen.
- Veraltet: „Creating additional keys is not offered by the panel API either“ (EN), „NSEC3PARAM 1 0 1 ab“ als Standard,
  „Schluessel-Rotation (nur per API)“ ohne Schutzregeln.
