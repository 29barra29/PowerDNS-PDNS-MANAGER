### Oberflaeche: gemeinsame Bausteine und Uebersetzungspruefung (W0-SHARED-FE)

- Einheitliche Bausteine fuer alle neuen Funktionen: Einmal-Anzeige fuer Tokens und Secrets (Kopieren wartet auf die
  Zwischenablage, der Wert bleibt sichtbar, bis du bestaetigst), Blaettern in langen Listen, Fehlermeldungen direkt im
  offenen Dialog, TTL-Eingabe mit Vorgaben und lesbarer Anzeige ("= 2 Stunden"), Ladeanzeige und eine "Kein Zugriff"-Seite
  fuer reine Admin-Bereiche.
- Datum und Uhrzeit werden in der gewaehlten Sprache angezeigt (statt fest deutsch); Serbisch in lateinischer Schrift.
- Neue Pruefung der Uebersetzungen (`npm run check:locales`): fehlende Texte, fehlende Pluralformen (z. B. die
  serbische/bosnische/kroatische "few"-Form), abweichende Platzhalter und nicht uebersetzte Texte fallen vor dem Release auf.
- `scripts/sync-locales.mjs` loescht Pluralformen der Zielsprache nicht mehr und ergaenzt fehlende Formen.
