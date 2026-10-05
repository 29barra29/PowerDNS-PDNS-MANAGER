### Geteilte Backend-Bausteine (W0-SHARED-BE, Grundlage)

- **Mehrere PowerDNS-Server:** Schreibvorgaenge laufen zuerst auf dem gewaehlten Server und erst nach dessen
  Erfolg auf den weiteren schreibbaren Servern. Aenderungen an einzelnen Records werden dabei je Server auf
  dessen eigenem Stand berechnet – ein Wert, der nur auf einem weiteren Server mit getrennter Datenbank
  existiert, bleibt beim Anlegen, Aendern oder Loeschen erhalten.
- **Zeitueberschreitung am Hauptserver:** Bricht die Verbindung zu PowerDNS nach dem Speichern ab, prueft das
  Panel den tatsaechlichen Stand nach. War die Aenderung angekommen, werden die weiteren Server normal
  geschrieben; ist der Stand unklar, meldet das Panel "unklar – bitte Zone neu laden" statt still einen Fehler.
  Abgebrochene Verbindungen fuehren nicht mehr zu einem Serverfehler (500).
- **Nicht geladene Server werden angezeigt:** Ein konfigurierter PowerDNS-Server, dessen API-Key nicht lesbar
  oder leer ist, erscheint in jedem Speicher-Ergebnis als "nicht geladen" statt einfach zu fehlen.
- **Besserer Schutz der Anmeldung:** Fehlversuche zaehlen jetzt pro IP-Adresse (bei IPv6 pro /64-Netz) und pro
  Benutzername. Nach 5 Fehlversuchen fuer denselben Benutzernamen ist dieser 15 Minuten gesperrt – egal von
  welcher Adresse. Ein erfolgreicher Login setzt nur den Zaehler des eigenen Benutzernamens zurueck. Ein gesperrter
  Benutzername loest keine Passwortpruefung (und spaeter keinen LDAP-Bind) mehr aus.
- **Access-Log ohne Geheimnisse:** Passwoerter und Token in URL-Parametern (`password=`, `token=`, `key=` ...)
  werden im Access-Log als `***` maskiert.
- Grundlagen fuer kommende 3.0-Funktionen: Prometheus-Metriken, LUA-Record-Pruefung, Record-Historie,
  DynDNS/PTR und Propagations-Check nutzen diese Bausteine.
