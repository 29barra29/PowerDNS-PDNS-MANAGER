### Zonenansicht, Benutzerliste und Protokoll-Aktionen umgebaut (W0-INT-FE2, Grundlage)

- Die Zonenansicht ist intern in Bausteine zerlegt (Tabs, Kopf- und Zeilen-Aktionen, Erweiterungen des
  Record-Dialogs). Neue Funktionen wie Verlauf, Propagations-Check, PTR- und LUA-Unterstützung docken dort an,
  ohne die Seite umzubauen. Bedienung und Aussehen bleiben wie in 2.4.1.
- Records und Zonen-Daten werden parallel geladen – die Zonenansicht öffnet spürbar schneller.
- Beim Löschen eines Records zeigt das Panel jetzt wie beim Anlegen und Ändern an, wenn ein weiterer Server die
  Änderung nicht übernehmen konnte (bisher blieb das beim Löschen unbemerkt).
- Ist ein konfigurierter PowerDNS-Server nicht geladen (z. B. API-Key nicht lesbar), erscheint nach dem Speichern
  oder Löschen ein gelber Hinweis mit den übersprungenen Servern.
- Beim Wechsel auf einen anderen Server derselben Zone startet die Ansicht mit frischem Zustand.
