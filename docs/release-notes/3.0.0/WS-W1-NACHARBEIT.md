### Feinschliff nach der ersten Testrunde (WS-W1-NACHARBEIT)

#### Neu / Behoben
- **Zurücksetzen ohne falschen Konflikt:** Ist ein Record bereits wieder auf dem Stand vor der Änderung, meldet die
  Vorschau keinen Konflikt mehr und das Zurücksetzen verlangt keine Bestätigung; betrifft ein Eintrag mehrere
  Records, wird nur geschrieben, was sich wirklich ändert.
- **Bedienung per Tastatur in Dialogen:** Zurücksetzen-Vorschau, Aufbewahrung des Protokolls, Webhook anlegen/bearbeiten
  und das Zustellprotokoll setzen beim Öffnen den Fokus in den Dialog, halten die Tab-Taste darin, schließen mit ESC
  (nicht während gespeichert wird) und geben den Fokus danach an den auslösenden Button zurück.
- **Übersetzungen:** Im Protokoll heißen „App-Einstellungen geändert“, „Logo hochgeladen“ und der Typ „System“ jetzt in
  allen sechs Sprachen verständlich; die Prometheus-Beispiele im Tab „Monitoring“ zeigen den Platzhalter-Host
  (ohne Basis-URL) in der gewählten Sprache.
- **Keine Geheimnisse in Meldungen und Logs:** Die Kommandozeile `python -m app.cli.secrets` gibt bei Datenbankfehlern
  nur Fehlerart und -nummer aus; der Webhook-Versand protokolliert bei unerwarteten Fehlern nur den Ziel-Host, nie
  die vollständige Adresse oder einen Traceback.

#### Geändert / Breaking
- Keine.

#### API
- `POST /api/v1/zones/{server}/{zone}/history/{id}/rollback`: Ist der aktuelle Stand schon der Vorher-Stand, kommt
  `200` mit `details.noop = true` statt `409` (auch ohne `force`). Die Vorschau meldet solche Einträge mit
  `noop: true, conflict: false`.

#### Nach dem Update prüfen
- Nichts Besonderes.

#### Website-Seiten (DE/EN)
- `features/record-historie`: „Bereits auf diesem Stand“ ist kein Konflikt.
