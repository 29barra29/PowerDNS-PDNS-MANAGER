### Schnellerer Start und verlaessliche Sprachwahl (W0-INT-FE1a)

- **Schnellerer erster Aufruf:** Die Oberflaeche laedt beim Start nur noch Anmeldung und Rahmen; jede Seite und jede
  Sprache (ausser Englisch) wird erst bei Bedarf nachgeladen. Der Start-Download sinkt von rund 850 kB auf rund
  300 kB. Waehrend eine Seite nachlaedt, zeigt der Inhaltsbereich einen Lade-Spinner, die Seitenleiste bleibt stehen.
- **Nach einem Update:** Ein noch offener Browser-Tab, der eine nicht mehr vorhandene Seitendatei anfordert, laedt
  die Seite einmal automatisch neu, statt mit einem Fehler stehen zu bleiben.
- **Sprachwahl bleibt erhalten:** Die Standardsprache des Servers gilt nur noch, solange im Browser keine eigene Wahl
  gespeichert ist; sie ueberschreibt die gewaehlte Sprache nicht mehr bei jedem Start. Die Sprache aus dem eigenen
  Profil hat Vorrang. Die Sprachwahl in der mobilen Kopfleiste wird jetzt wie in den Einstellungen im Profil gespeichert.
- **Benutzer- und Protokollseite nur fuer Administratoren:** Ruft ein normaler Benutzer `/users` oder `/audit` direkt
  auf, erscheint eine "Kein Zugriff"-Karte statt einer leeren oder fehlerhaften Seite.
- **Fehlermeldungen uebersetzt:** Meldungen wie "Server nicht erreichbar", "Sitzung abgelaufen" oder
  "Anmeldung fehlgeschlagen" sowie die Fehlerseite der Oberflaeche erscheinen in der gewaehlten Sprache.
  Fehler eines PowerDNS-Servers nennen wieder den betroffenen Server.
- **Grundlage fuer den erzwungenen Passwortwechsel:** Verlangt ein Administrator einen Passwortwechsel, zeigt die
  Oberflaeche nach der Anmeldung einen eigenen Dialog dafuer, bevor andere Seiten erreichbar sind.
