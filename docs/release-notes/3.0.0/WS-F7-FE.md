### Verlauf und Zurücksetzen in der Zonenansicht, neues Protokoll (WS-F7-FE, Oberfläche zu F7)

#### Neu
- **Tab „Verlauf“ in jeder Zone:** zeigt alle protokollierten Änderungen der Zone über alle Server – mit
  Vorher/Nachher-Ansicht je RRset (entfernte Werte rot, neue grün, TTL-Änderung, deaktiviert, Kommentare),
  Ergebnis pro Server, Filtern (Bereich, Aktion, Record-Typ, Suche, Benutzer, Status, Zeitraum) und Seitenwahl.
  Einträge aus Versionen vor 3.0 werden mit Hinweis angezeigt.
- **Verlauf eines einzelnen Records:** Das neue Uhr-Symbol in der Record-Zeile öffnet den Verlauf gefiltert auf
  diesen Namen und Typ.
- **Änderung zurücksetzen:** Wer Schreibrecht auf die Zone hat, kann eine Record-Änderung mit Vorschau
  zurücksetzen. Die Vorschau zeigt den aktuellen Stand, das Ziel, die betroffenen Server und was ausgenommen
  bleibt (SOA, DNSSEC, ACME-Challenges). Wurde die Zone seitdem erneut geändert, muss das ausdrücklich bestätigt
  werden; Einträge, die schon auf dem alten Stand sind, erscheinen als „bereits auf diesem Stand“. Ist ein
  Zurücksetzen nicht möglich, nennt ein Hinweis den Grund.
- **Kennzeichnungen:** Einträge zeigen, ob sie per API-Token entstanden sind, ob sie eine andere Änderung
  zurücksetzen bzw. zurückgesetzt wurden, ob der erste Server nur verspätet bestätigt hat oder das Ergebnis
  unklar ist, und (nur für Admins) ob sie aus der Zeit vor einer Neuanlage der Zone stammen.
- **Aufbewahrung einstellen:** Über „Aufbewahrung“ auf der Protokoll-Seite legen Admins fest, nach wie vielen Tagen
  Protokolleinträge automatisch gelöscht werden (0 = unbegrenzt, sonst 7–3650 Tage).

#### Geändert / Breaking
- **Protokoll-Seite neu (Admin):** Filter nach Aktion, Typ, Status, Benutzer, Zone, Server, Zeitraum und
  Volltext; die Filter stehen in der Adresse und lassen sich als Link weitergeben. Blättern mit Gesamtzahl,
  verständliche Bezeichnungen für alle Aktionen, Detailansicht mit Vorher/Nachher und Sprung in den
  Zonenverlauf. Der CSV-Export übernimmt die aktiven Filter. Ladefehler werden angezeigt statt verschluckt.
  Die bisherige Liste der letzten 200 Einträge ohne Filter entfällt.
- Datum und Uhrzeit erscheinen im Verlauf und im Protokoll im Format der gewählten Sprache.

#### API
- Keine eigenen Endpunkte; die Oberfläche nutzt die Verlaufs-, Rollback- und Audit-Endpunkte aus WS-F7-BE.

#### Nach dem Update prüfen
- Protokoll → „Aufbewahrung“: Wert festlegen (Standard unbegrenzt).
- Eine Zone öffnen und den Tab „Verlauf“ ansehen: Einträge aus der Zeit vor dem Update tragen den Hinweis
  „Eintrag aus einer Version vor 3.0“ und lassen sich nicht zurücksetzen.

#### Website-Seiten (DE/EN)
- `features/record-historie` (neu, siehe WS-F7-BE): Screenshots des Tabs „Verlauf“, des Rollback-Dialogs mit
  Konflikt-Bestätigung und der Kennzeichnungen.
- `features/audit-log`: neue Protokoll-Seite (Filter, teilbare Adresse, Detailansicht, „Im Zonenverlauf öffnen“,
  CSV mit aktiven Filtern, Dialog „Aufbewahrung“).
- `features/zonen-records`: Tab „Verlauf“ und Uhr-Symbol in der Record-Zeile.
- Veraltet: „Das Audit-Log zeigt die letzten 200 Einträge, Filter gibt es dort nicht“ (DE).
