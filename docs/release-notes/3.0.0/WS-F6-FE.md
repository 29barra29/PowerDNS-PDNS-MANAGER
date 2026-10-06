### Neue Webhook-Oberflaeche: Zustellprotokoll, Test und Verwaltung im Panel (WS-F6-FE)

#### Neu
(Einstellungen → API & Sicherheit → Webhooks, fuer jeden angemeldeten Benutzer)
- **Liste je Webhook** mit Ziel (nur Host, ohne Pfad/Token), Ausloeser („eigene Aenderungen“ oder „alle Aenderungen in
  meinen Zonen“), abonnierten Ereignissen, Aktiv-Status, letzter erfolgreicher Zustellung bzw. letztem Fehler sowie
  Hinweisen zu offenen, endgueltig fehlgeschlagenen Zustellungen und Fehlversuchen in Folge.
- **Anlegen und Bearbeiten im Dialog:** Ereignisse per Checkbox-Baum (alle, je Kategorie Records/Zonen/DNSSEC/DynDNS oder
  einzeln) statt Freitext; Pruefung der Eingaben direkt im Dialog. Beim Bearbeiten werden nur geaenderte Felder
  gespeichert. Aeltere, nicht mehr bekannte Filter erscheinen als eigene Chips und koennen entfernt werden.
- **Test senden** mit Ergebnis direkt unter dem Webhook (HTTP-Code und Dauer bzw. Fehlergrund).
- **Zustellprotokoll** als Seitenleiste: Filter nach Status und Ereignis, 25 Eintraege je Seite, Versuche, Antwort,
  naechster Versuch, aufklappbare Details (Zustell-ID zum Kopieren, Antwortauszug, gesendeter Payload und Header),
  „Erneut senden“ bzw. „Jetzt erneut versuchen“. Solange Zustellungen offen sind, aktualisiert sich die Ansicht alle
  5 Sekunden.
- **Aktivieren/Deaktivieren, Secret erneuern, Loeschen** je Webhook. Das neue Secret erscheint einmalig im
  Secret-Dialog (Kopieren mit Rueckmeldung, Schliessen nur per Bestaetigung).
- **Warnungen:** Hinweis, wenn die Zustellung auf dem Server abgeschaltet ist (`BACKGROUND_WORKERS_ENABLED=false`) oder
  der Zustelldienst nicht laeuft; Status „URL unlesbar“ bzw. „Secret nicht lesbar“ (z. B. nach Schluesselverlust) mit
  direktem Weg zum Neu-Eintragen bzw. Erneuern.
- Alle Texte in Deutsch, Englisch, Bosnisch, Kroatisch, Ungarisch und Serbisch; Zeiten im Format der gewaehlten Sprache.

#### Geändert / Breaking
- Die bisherige Eingabezeile „Ereignisse (kommagetrennt)“ und die einfache Liste entfallen.
- Webhooks lassen sich nur noch mit Browser-Anmeldung verwalten (siehe WS-F6-BE); die Karte zeigt das Ziel nur als
  Host, nie mit Pfad oder Token.

#### API
- Keine eigenen Endpunkte; die Oberflaeche nutzt die Webhook-Endpunkte aus WS-F6-BE.

#### Nach dem Update prüfen
- Einstellungen → API & Sicherheit → Webhooks: je Webhook „Test senden“ und das Zustellprotokoll ansehen.
- Webhooks mit dem Hinweis „URL unlesbar“ oder „Secret nicht lesbar“ neu eintragen bzw. das Secret erneuern.
- Aeltere Ereignis-Filter (eigene Chips) pruefen und bei Bedarf durch die Ereignis-Auswahl ersetzen.

#### Website-Seiten (DE/EN)
- `docs/features/webhooks`: Screenshots/Beschreibung der neuen Karte, des Dialogs (Ereignis-Baum, Ausloeser) und des
  Zustellprotokolls (Filter, Details, erneut senden, Auto-Aktualisierung); Hinweise „URL unlesbar“/„Secret nicht lesbar“.
- `docs/troubleshooting`: „Webhook kommt nicht an“ – zuerst das Zustellprotokoll in der Karte oeffnen.
- Veraltet: Eingabe „Ereignisse (kommagetrennt)“ und Screenshots der alten Webhook-Liste.
