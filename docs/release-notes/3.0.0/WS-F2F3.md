### Zonen-Export und NOTIFY, Benutzerverwaltung fuer Teams (WS-F2F3, F2/F3)

- **Zonenansicht:** Neue Buttons „Export“ (laedt die Zone als BIND-Zonendatei `<zone>.txt` herunter, auch fuer
  Nutzer mit Leserecht) und „NOTIFY senden“ (benachrichtigt die Secondaries; nur mit Schreibrecht und fuer Zonen vom
  Typ Master, Producer oder Slave). Beides wird im Audit-Log protokolliert (`ZONE_EXPORT`, `ZONE_NOTIFY`).
  Scheitert NOTIFY (z. B. bei einer Native-Zone), erklaert die Fehlermeldung die Voraussetzungen.
  `GET …/export` liefert zusaetzlich das Feld `filename`.
- **Dialog „Passwort & Sicherheit“** in der Benutzerverwaltung: Passwort setzen, Zufallspasswort erzeugen (wird genau
  einmal angezeigt und laesst sich kopieren), Reset-Link per E-Mail senden (24 Stunden gueltig, einmal verwendbar,
  unabhaengig von „Passwort vergessen“), 2FA zuruecksetzen und alle Passkeys entfernen (Hilfe bei verlorenem
  Smartphone oder Sicherheitsschluessel; auch der Weg, wenn ein 2FA-Geheimnis nicht mehr entschluesselt werden kann).
- **Erzwungener Passwortwechsel:** Beim Anlegen eines Benutzers und beim Admin-Reset kann (bzw. wird standardmaessig)
  verlangt werden, dass der Nutzer beim naechsten Login ein eigenes Passwort waehlt. Die Benutzerliste zeigt das als
  Badge, ebenso aktive 2FA und die Zahl der Passkeys.
- **Alle Zugaenge widerrufen:** Bei Verdacht auf eine Kontouebernahme widerruft der Admin mit einem Klick alle
  API-Tokens des Nutzers endgueltig, deaktiviert dessen DynDNS-Tokens und Webhooks (ausstehende Zustellungen werden
  verworfen) und setzt auf Wunsch 2FA und Passkeys zurueck. Der Dialog zeigt vorher, wie viele Zugaenge bestehen.
- **Aktivieren/Deaktivieren** von Benutzern direkt in der Liste; Rollenwechsel mit Rueckfrage.
- **Schutzregeln:** Das eigene Konto laesst sich in der Benutzerverwaltung weder deaktivieren, herabstufen noch per
  Admin-Werkzeug zuruecksetzen (dafuer gibt es die Einstellungen). Der letzte **aktive** Administrator kann nicht
  deaktiviert oder herabgestuft werden – deaktivierte Admins zaehlen nicht mehr mit.
- **Fixes:** Eine bereits vergebene E-Mail-Adresse beim Anlegen oder Aendern eines Benutzers fuehrt zu einer klaren
  Meldung (409) statt zu einem Serverfehler. Neue Zonen bekommen im SOA als Hostmaster-Adresse `hostmaster.<zone>`
  (bisher wurde bei zwei Nameservern faelschlich der zweite eingetragen); der von PowerDNS vergebene Serial bleibt
  erhalten. Beim Anlegen einer Zone auf ausdruecklich gewaehlten Servern wird „Speichern: Nein“ jetzt beachtet.
  Im Profil lassen sich Telefon, Adresse und Sprache wieder leeren.
- **Geaendert (API):** `PUT /api/v1/auth/users/{id}/reset-password` ohne Body erzwingt jetzt den Passwortwechsel beim
  naechsten Login (`{"must_change_password": false}` schaltet das ab). Neue Admin-Endpunkte (nur mit
  Browser-Anmeldung): `send-reset-link`, `reset-2fa`, `webauthn-credentials` (DELETE), `revoke-access`; lesend
  `GET …/access-summary`. Das Audit-Log kennt die neuen Aktionen `USER_PASSWORD_RESET_LINK`, `USER_2FA_RESET`,
  `USER_PASSKEYS_RESET`, `USER_ACCESS_REVOKE` und `PASSWORD_RESET` (Passwort per Link gesetzt).
