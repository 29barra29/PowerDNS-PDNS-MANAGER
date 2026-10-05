### DNSSEC: Optionen, Schluesselverwaltung und sichere Schutzregeln (WS-F4-A, Backend; Oberflaeche folgt mit F4-B)

- **Aktivieren mit Optionen:** `POST /api/v1/dnssec/{server}/{zone}/enable` nimmt Schluesselmodell (`csk` oder
  `ksk_zsk`), Algorithmus (ECDSA P-256 als Standard, P-384, Ed25519, Ed448, RSA-SHA256/512 mit 2048/3072/4096 Bit) und
  NSEC oder NSEC3 (Iterationen, Salt, Opt-Out, Narrow) an. Neuer Standard ist NSEC3 `1 0 0 -` nach RFC 9276 (bisher
  `1 0 1 ab`); bestehende Zonen bleiben unveraendert. Das alte Feld `nsec3param` funktioniert weiter.
- **Kein doppelter Schluessel mehr:** Ist die Zone schon signiert, antwortet `enable` mit 200 und `already_enabled`,
  statt einen weiteren CSK anzulegen. Gibt es nur inaktive Schluessel, kommt 409. Scheitert ein Schritt, werden bereits
  angelegte Schluessel wieder entfernt.
- **Neue Endpunkte:** `GET …/status` (Schluessel mit Key-Tag und DS-Status, NSEC/NSEC3, Rollover-Phase,
  Vergleich mit anderen Servern, Hinweise, PowerDNS-Version), `POST …/keys` (Schluessel anlegen, z. B. fuer einen
  Schluesselwechsel), `PUT …/keys/{id}` (aktivieren/deaktivieren, veroeffentlichen/zurueckziehen ab PowerDNS 4.3),
  `PUT …/nsec3` (NSEC/NSEC3 nachtraeglich aendern). `activate`/`deactivate`/`DELETE` bleiben erhalten.
- **Schutzregeln:** Der letzte aktive Schluessel, der letzte aktive KSK/CSK und der letzte veroeffentlichte aktive
  KSK/CSK lassen sich nicht versehentlich abschalten oder loeschen (409 mit `detail.code`, z. B. `last_active_key`).
  Bewusst uebersteuern geht mit `force` – das steht dann im Audit-Log.
- **Deaktivieren in sicherer Reihenfolge:** erst NSEC3-Parameter entfernen, dann inaktive, dann aktive Schluessel;
  bricht PowerDNS mittendrin ab, nennt die Meldung, was schon geloescht ist.
- **„Speichern: Nein“ gilt jetzt auch fuer DNSSEC:** Alle schreibenden DNSSEC-Endpunkte antworten auf solchen Servern
  mit 403. Vorsignierte Zonen (PRESIGNED) werden nicht veraendert (409).
- **Secondaries bekommen Aenderungen mit:** Bei Zonen vom Typ Master/Producer erhoeht das Panel nach jeder
  Schluessel- oder NSEC-Aenderung den SOA-Serial und sendet NOTIFY (abschaltbar mit `bump_serial: false`). Beides steht
  im Audit-Log; scheitert NOTIFY, bleibt die Aenderung gueltig und die Antwort nennt den Fehler.
- **Audit und Webhooks:** Auch fehlgeschlagene Schluessel-Aktionen werden protokolliert. Neue Audit-Aktionen
  `KEY_CREATE`, `KEY_PUBLISH`, `KEY_UNPUBLISH`, `KEY_UPDATE`, `DNSSEC_NSEC3_UPDATE`; neue Webhook-Ereignisse
  `dnssec.key_created`, `dnssec.key_published`, `dnssec.key_unpublished`, `dnssec.nsec3_changed`.
- **Fehlertexte:** PowerDNS-Fehler erscheinen lesbar als `PowerDNS (<server>): <meldung>`; interne Server-Adressen
  werden in DNSSEC-Antworten nicht mehr angezeigt. Private Schluessel verlassen das Panel nie.
- **Hinweis fuer Skripte:** `POST …/enable` auf einer signierten Zone legt keinen zweiten Schluessel mehr an;
  `activate`/`deactivate`/`DELETE` koennen jetzt mit 409 (Schutzregel) bzw. 403 („Speichern: Nein“) antworten;
  `zone` in den Antworten ist normalisiert (klein, mit Punkt am Ende).
