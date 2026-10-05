### Oberflaeche: Fehlerbehebungen und Feinschliff (WS-F8b)

**Records bearbeiten**
- TTL als Zahlenfeld mit Vorgaben und lesbarer Anzeige ("= 2 Stunden"), erlaubt sind 60 Sekunden bis 7 Tage.
  Ein Record mit 7200 s zeigt beim Bearbeiten wieder 7200 (bisher sprang die Auswahl auf "1 Min"). Hat ein
  RRset mehrere Werte, weist der Dialog darauf hin, dass die TTL fuer alle gilt.
- Deaktivierte Records sind in der Liste markiert und bleiben beim Bearbeiten deaktiviert; im Dialog laesst sich
  ein Record gezielt deaktivieren.
- Namen werden getrimmt und klein geschrieben; Leerzeichen, leere Labels, zu lange Namen oder Namen ausserhalb der
  Zone werden vor dem Speichern gemeldet. `contest.de` in der Zone `test.de` wird nicht mehr als fremder FQDN
  missverstanden.
- CAA: Flag und Tag sind vorbelegt (Speichern ohne Anfassen der Auswahl funktioniert), weitere Tags
  (`issuemail`, `contactemail`, `contactphone`), Anfuehrungszeichen im Wert bleiben erhalten.
- Records unbekannter Typen (z. B. CERT oder URI aus einem Import) lassen sich als Rohtext bearbeiten.
- Null-MX/SRV-Ziel `.`, Punycode-Domains und IPv6 mit eingebetteter IPv4 werden akzeptiert; einlabelige
  Hostnamen sind nur noch eine Warnung.
- Delegations-NS (z. B. `sub NS`) koennen wieder geloescht werden; geschuetzt sind nur SOA und Apex-NS.
- Die Loesch-Rueckfrage nennt den konkreten Wert und warnt, wenn es der letzte Wert des Eintrags ist. Konnte ein
  weiterer Server eine Aenderung nicht uebernehmen, sagt die Meldung das ausdruecklich.

**Suche, Zonen, Dashboard**
- Die Suche fragt alle Server gleichzeitig und genau einmal pro Eingabe ab, zeigt nicht erreichbare oder
  fehlerhafte Server und gekappte Ergebnislisten an und verlinkt jeden Treffer auf seine Zone.
- Zonen anlegen: Nameserver in Grossbuchstaben und Punycode-Domains werden akzeptiert. Fehler beim Import und
  beim Anlegen erscheinen im Dialog statt verdeckt dahinter.
- Das Dashboard meldet "Keine DNS-Server konfiguriert" statt "alle online", wenn noch kein Server eingetragen ist,
  und zeigt Ladefehler auch Nicht-Admins.

**Einstellungen**
- Sprache: Die Auswahl im Profil stellt nicht mehr ungefragt auf Deutsch um; schlaegt das Speichern fehl,
  springt sie zurueck und meldet den Fehler.
- Profil: Telefon, Adresse und Geburtsdatum lassen sich leeren; Basis-URL, Footer-Text und Creator ebenfalls,
  "Logo entfernen" entfernt das Logo wirklich. Konnten die App-Einstellungen nicht geladen werden, bleiben
  Systemtitel, Registrierung und Branding beim Speichern unveraendert (bisher wurden Standardwerte gespeichert).
  Scheitert nur das Speichern der App-Einstellungen, ist das Profil trotzdem gespeichert.
- E-Mail (SMTP): Ein leeres Passwortfeld behaelt das gespeicherte Passwort; zum Entfernen gibt es einen eigenen
  Haken.
- DNS-Server: "Anzeigen" holt den API-Key immer frisch; ein nur angezeigter Key wird beim Speichern nicht
  zurueckgeschickt. Fehler erscheinen im Dialog.
- Vorlagen: TTL-Felder mit denselben Grenzen wie Records; das Backend lehnt Vorlagen mit TTL ausserhalb von
  60 bis 604800 Sekunden ab (bisher scheiterten solche Vorlagen-Records erst beim Anlegen der Zone).
- Updates: Ist GitHub nicht erreichbar, steht dort "Versionspruefung fehlgeschlagen" mit "Erneut pruefen" statt
  dauerhaft "wird geladen"; die Liste der letzten Aenderungen unterscheidet "nicht gefunden", "GitHub-Limit" und
  sonstige Fehler. Die Versionspruefung laeuft nur noch fuer Admins und hoechstens alle 6 Stunden.
- ACME: Fehler beim Anlegen eines Tokens erscheinen im Dialog; der neue Token wird im Einmal-Dialog mit
  sichtbarer Kopier-Bestaetigung angezeigt.
- Datumsangaben folgen der gewaehlten Sprache; verbliebene deutsche Texte in englischer Oberflaeche sind
  uebersetzt.

**Datenschutz:** Die Schrift (Inter) wird jetzt vom Panel selbst ausgeliefert – keine Anfragen mehr an Google
Fonts, auch nicht auf der Login-Seite.

**Ersteinrichtung:** Unzulaessige Zeichen im Benutzernamen werden schon im ersten Schritt erklaert; SMTP-Angaben
aus dem Einrichtungsassistenten werden jetzt tatsaechlich gespeichert.
