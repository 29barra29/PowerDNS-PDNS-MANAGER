### Restpunkte vor dem Release (WS-W3-NACHARBEIT)

#### Neu / Behoben
- **Dialoge in den Einstellungen liegen über der ganzen Seite:** Webhook-Formular, Webhook-Zustellprotokoll,
  API-Token- und DynDNS-Token-Dialog sowie alle Einmal-Anzeigen von Geheimnissen (Webhook-Secret, API-Token,
  DynDNS-Token, Prometheus-Token, Zufallspasswort) waren auf ihre Karte begrenzt; im Reiter „API & Sicherheit“
  verdeckte die DynDNS-Karte z. B. den Speichern-Knopf des Webhook-Formulars. Alle Dialoge werden jetzt über der
  ganzen Seite angezeigt.
- **Tastaturbedienung:**
  - Nach dem Abbrechen der Passwort-Bestätigung (Step-up) kehrt der Fokus zum auslösenden Knopf zurück, auch wenn
    dieser kurz gesperrt war.
  - „Neue Zone“ und „Zone importieren“ setzen den Fokus in den Dialog, halten die Tab-Taste darin, schließen mit ESC
    und geben den Fokus zurück.
  - Die geöffnete Seitenleiste auf schmalen Bildschirmen verhält sich wie ein Dialog (Fokus, Tab, ESC).
  - Die Sprachauswahl schließt mit ESC nur, wenn kein Dialog darüber liegt, und gibt den Fokus an ihren Knopf zurück.
  - Strg/Cmd+Enter sendet nur noch den obersten Dialog ab (vorher auch einen Dialog unter der Passwort-Bestätigung).
  - Kopieren-Knöpfe mit sichtbarem Text (DNSSEC-Assistent) werden von Screenreadern mit genau diesem Text angesagt.
- **DynDNS – gesperrte Secrets:** Tokens, deren Secret gesperrt wurde (Token stand in einer URL oder „Alle Zugänge
  widerrufen“), zeigen „Secret gesperrt“ mit Hinweis und „Neues Secret erzeugen“; „Aktivieren“ wird nicht mehr
  angeboten (auch nicht in der Admin-Liste), im Bearbeiten-Dialog ist „Aktiv“ gesperrt. Die Meldung beim Aktivieren
  eines gesperrten Tokens erscheint übersetzt.
- **LUA-Records:** Sperrtexte (Tooltip, Screenreader, Fehler im Record-Dialog und im Bulk-Editor) erscheinen in der
  gewählten Sprache. Die Karte „LUA-Records“ speichert nach einem Ladefehler nichts mehr (vorher konnte „Speichern“
  die gespeicherte Einstellung still auf „Nur Administratoren“ zurücksetzen); stattdessen „Erneut versuchen“.
- **DNSSEC-Rollover:** Sind alter und neuer Schlüssel gleichzeitig aktiv, lässt sich der alte erst abschalten, wenn
  alle Nameserver den neuen DNSKEY liefern (Prüfung bzw. Bestätigung) und – bei KSK/CSK – der neue DS beim
  Registrar steht. Vorher war das bei ZSK ohne jede Bedingung möglich.
- **Zonen-Import:** Eine Datei mit einem ungültigen Zeichen (einzelnes UTF-16-Surrogat, z. B. aus einem kaputten
  JSON-Export) ergibt eine klare Meldung mit Zeilenangabe statt eines Serverfehlers. LUA- und ALIAS-Records in
  RFC-3597-Schreibweise (`TYPE65402 \# …`, z. B. aus einem PowerDNS-Export) zeigt die Vorschau jetzt so an, wie
  PowerDNS sie liest.
- **Bulk-Editor:** Wird in einer Anfrage der letzte Wert eines RRsets gelöscht und ein neuer hinzugefügt, bleibt die
  TTL des RRsets erhalten (vorher Standard-TTL).
- **PTR-Pflege:** Der Protokolleintrag „PTR gepflegt“ nennt die auslösende Aktion (Anlegen, Ändern, Löschen,
  Bulk, Zurücksetzen) und die Zone. Das Zurücksetzen im Zonenverlauf pflegt PTRs wie jede andere Änderung, wenn
  „PTR-Pflege standardmäßig aktivieren“ eingeschaltet ist.
- **Sicherheit:**
  - Eine OIDC-Verknüpfung, die vor „Alle Zugänge widerrufen“ (oder einem Admin-Passwort-Reset) gestartet wurde,
    lässt sich danach nicht mehr abschließen.
  - Der LDAP-Verbindungstest mit Testpasswort zählt wie eine Anmeldung: Nach 5 Fehlversuchen ist der Testbenutzer
    15 Minuten gesperrt (kein Passwort-Raten über die Einstellungen, keine Kontosperren im Verzeichnis).
  - „Nach der OIDC-Anmeldung die 2FA (TOTP) des Panels abfragen“ lässt sich nur noch mit Passwort-Bestätigung
    (Step-up) ändern.
  - Die Anmeldung verrät nicht mehr, ob ein lokales Konto existiert (gleiche Laufzeit; bei gestörtem LDAP dieselbe
    Meldung für alle Namen).
  - Das Zugriffsprotokoll maskiert Geheimnisse auch bei URL-kodierten Parameternamen und Panel-/DynDNS-Tokens unter
    beliebigen Parameternamen oder im Pfad.
  - Die DNSKEY-Prüfung fragt höchstens 12 Nameserver, mit begrenzter Parallelität und Gesamtzeit; der LUA-Serverstatus
    hält währenddessen keine Datenbankverbindung und fragt jeden Server bei gleichzeitigen Aufrufen nur einmal.

#### Geändert / Breaking
- `POST /api/v1/auth/login`: Ist LDAP aktiv, aber gestört (nicht erreichbar oder falsch konfiguriert), bekommt auch
  ein lokales Konto mit falschem Passwort `503` mit dem LDAP-Hinweis (vorher `401`). Mit richtigem Passwort meldet
  ein lokales Konto weiter sofort an.
- `POST /api/v1/settings/sso/test` mit `target: "ldap"` und `test_password`: nicht bestätigtes Passwort zählt als
  Fehlversuch (IP und Testbenutzer); gesperrt → `429`.
- `PUT /api/v1/settings/sso`: Eine Änderung von `general.require_totp` verlangt `step_up` (sonst `403`
  `stepup_required`).
- `POST /api/v1/zones/import/preview` und `POST /api/v1/zones/import`: einzelnes UTF-16-Surrogat im Inhalt → `400`
  „… in Zeile N.“ (vorher `500`).
- `DELETE …/records/{server}/{zone}/delete` (`content`) sowie Bulk `delete[].content` und `set_disabled[].content`:
  höchstens 65535 Zeichen (darüber `422`).
- OIDC-Verknüpfung: Abschluss nach einem Sitzungs-Widerruf → Rücksprung mit `sso_error=link_failed`.

#### API
- `POST …/history/{id}/rollback`: bei PTR-Pflege `details.ptr` in der Antwort, `ptr` in den Audit-Details von
  `RECORD_ROLLBACK` und `data.ptr` im Webhook `record.rollback`.
- `PTR_SYNC` (Audit und Webhook `record.ptr_synced`): `source.action` ist `CREATE`, `UPDATE`, `DELETE`,
  `BULK_UPDATE` oder `ROLLBACK`, `source.zone` die Forward-Zone (vorher immer `BULK_UPDATE` ohne Zone).
- Fehler-Audit `BULK_UPDATE`: `details.applied = "unknown"`, wenn der Primary-Ausgang unklar ist
  (`primary_outcome: "unknown"`); `false` nur noch, wenn sicher nichts geschrieben wurde.
- `GET /api/v1/dnssec/{server}/{zone}/dnskey-check`: höchstens 12 Nameserver-Namen; weitere erscheinen mit
  `error: "Nicht geprüft (zu viele Nameserver)"`, `truncated: true`.

#### Nach dem Update prüfen
- Keine Schema-Änderung, keine neuen Einstellungen.
- Skripte, die `general.require_totp` per `PUT /settings/sso` ändern, müssen `step_up` mitsenden.
- Nach „Alle Zugänge widerrufen“ bleibt eine bestehende SSO-Verknüpfung des Kontos bestehen (nur noch nicht
  abgeschlossene Verknüpfungen werden ungültig). Admins prüfen in „Passwort & Sicherheit“ → „Externe Anmeldung“,
  ob die Verknüpfung bekannt ist, und wandeln das Konto sonst in ein lokales um.

#### Website-Seiten (DE/EN)
- `dyndns`: Anzeige „Secret gesperrt“, „Neues Secret erzeugen“ und danach wieder aktivieren.
- `sso`/`sicherheit`: LDAP-Test zählt als Anmeldeversuch; 2FA-Abfrage nach OIDC nur mit Passwort-Bestätigung; offene
  OIDC-Verknüpfungen enden mit „Alle Zugänge widerrufen“, bestehende bleiben.
- `troubleshooting`: Anmeldung bei gestörtem LDAP meldet für alle Konten „Anmeldedienst nicht erreichbar“ (lokale
  Admins mit richtigem Passwort kommen trotzdem hinein); Zonen-Import „ungültiges Zeichen in Zeile N“.
- `dnssec`: Rollover – Abschalten des alten Schlüssels bei „beide aktiv“ erst nach DNSKEY-Prüfung (und DS).
- `reverse-dns`/`record-historie`: Zurücksetzen pflegt PTRs (Admin-Default); PTR-Protokoll nennt Aktion und Zone.
- `panel-api`: Punkte aus „Geändert / Breaking“ und „API“.
- Repo `frontend/README.md`: Dialoge nutzen `components/common/ModalPortal.jsx` (Overlay an `document.body`) und
  `useDialogFocus` (inkl. `onSubmitShortcut` für Strg/Cmd+Enter statt eigener globaler Listener).
