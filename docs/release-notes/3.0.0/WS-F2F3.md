### Zonen-Export und NOTIFY, Benutzerverwaltung fuer Teams (WS-F2F3, F2/F3)

#### Neu
- **Zonenansicht:** Neue Buttons „Export“ (laedt die Zone als BIND-Zonendatei `<zone>.txt` herunter, auch fuer
  Nutzer mit Leserecht) und „NOTIFY senden“ (benachrichtigt die Secondaries; nur mit Schreibrecht und fuer Zonen vom
  Typ Master, Producer oder Slave). Beides wird im Audit-Log protokolliert (`ZONE_EXPORT`, `ZONE_NOTIFY`).
  Scheitert NOTIFY (z. B. bei einer Native-Zone), erklaert die Fehlermeldung die Voraussetzungen.
- **Dialog „Passwort & Sicherheit“** in der Benutzerverwaltung: Passwort setzen, Zufallspasswort erzeugen (wird genau
  einmal angezeigt und laesst sich kopieren), Reset-Link per E-Mail senden (24 Stunden gueltig, einmal verwendbar,
  unabhaengig von „Passwort vergessen“), 2FA zuruecksetzen und alle Passkeys entfernen (Hilfe bei verlorenem
  Smartphone oder Sicherheitsschluessel; auch der Weg, wenn ein 2FA-Geheimnis nicht mehr entschluesselt werden kann).
- **Erzwungener Passwortwechsel:** Beim Anlegen eines Benutzers und beim Admin-Reset kann (bzw. wird standardmaessig)
  verlangt werden, dass der Nutzer beim naechsten Login ein eigenes Passwort waehlt. Die Benutzerliste zeigt das als
  Badge, ebenso aktive 2FA und die Zahl der Passkeys.
- **Alle Zugaenge widerrufen:** Bei Verdacht auf eine Kontouebernahme widerruft der Admin mit einem Klick alle
  API-Tokens des Nutzers endgueltig, deaktiviert dessen DynDNS-Tokens und Webhooks (wartende und fehlgeschlagene
  Zustellungen werden verworfen) und setzt auf Wunsch 2FA und Passkeys zurueck. Der Dialog zeigt vorher, wie viele
  Zugaenge bestehen.
- **Aktivieren/Deaktivieren** von Benutzern direkt in der Liste; Rollenwechsel mit Rueckfrage.

#### Geändert / Breaking
- `PUT /api/v1/auth/users/{id}/reset-password` ohne Body erzwingt jetzt den Passwortwechsel beim naechsten Login
  (`{"must_change_password": false}` schaltet das ab). Solange der Wechsel aussteht, antworten Anfragen aus der
  Browser-Sitzung mit 403 und dem Header `X-Password-Change-Required` (API-Tokens sind nicht betroffen).
- **Schutzregeln:** Das eigene Konto laesst sich in der Benutzerverwaltung weder deaktivieren, herabstufen noch per
  Admin-Werkzeug zuruecksetzen (dafuer gibt es die Einstellungen). Der letzte **aktive** Administrator kann nicht
  deaktiviert oder herabgestuft werden – deaktivierte Admins zaehlen nicht mehr mit.
- **Fixes:** Eine bereits vergebene E-Mail-Adresse beim Anlegen oder Aendern eines Benutzers fuehrt zu einer klaren
  Meldung (409) statt zu einem Serverfehler. Neue Zonen bekommen im SOA als Hostmaster-Adresse `hostmaster.<zone>`
  (bisher wurde bei zwei Nameservern faelschlich der zweite eingetragen); der von PowerDNS vergebene Serial bleibt
  erhalten. Beim Anlegen einer Zone auf ausdruecklich gewaehlten Servern wird „Speichern: Nein“ jetzt beachtet.
  Im Profil lassen sich Telefon, Adresse und Sprache wieder leeren.

#### API
- Neu (Admin, nur Browser-Anmeldung): `POST /api/v1/auth/users/{id}/send-reset-link`, `POST …/reset-2fa`,
  `DELETE …/webauthn-credentials`, `POST …/revoke-access`; lesend `GET …/access-summary`.
- Geaendert: `PUT /api/v1/auth/users/{id}/reset-password` (siehe oben); `GET /api/v1/zones/{server}/{zone}/export`
  liefert zusaetzlich `filename` und wird auditiert; `POST /api/v1/zones/{server}/{zone}/notify` wird auditiert und
  antwortet bei ungeeigneten Zonen mit einer Erklaerung.
- Neue Audit-Aktionen: `ZONE_EXPORT`, `ZONE_NOTIFY`, `USER_PASSWORD_RESET_LINK`, `USER_2FA_RESET`,
  `USER_PASSKEYS_RESET`, `USER_ACCESS_REVOKE`, `PASSWORD_RESET` (Passwort per Link gesetzt).

#### Nach dem Update prüfen
- Skripte, die `reset-password` ohne Body aufrufen: der Nutzer muss danach beim Login ein neues Passwort setzen –
  falls unerwuenscht, `{"must_change_password": false}` mitschicken.
- Fuer Reset-Links muessen SMTP und die oeffentliche Basis-URL eingerichtet sein (Einstellungen → E-Mail bzw. Profil).
- Benutzerliste ansehen: Badges „Passwortwechsel ausstehend“, 2FA und Passkeys stimmen mit den Erwartungen ueberein.

#### Website-Seiten (DE/EN)
- `features/zonen-records`: Abschnitte „Export einer Zone (nur per API)“ und „NOTIFY ausloesen (nur per API)“ sind
  **veraltet** – jetzt Buttons in der Zonenansicht (Datei `<zone>.txt`; NOTIFY mit Voraussetzungen: Master/Producer,
  Slave nur mit `secondary-do-renotify`, geht nur vom gewaehlten Server aus); die API bleibt als Alternative.
- `features/audit-log`: Zusatz „(NOTIFY wird nicht auditiert)“ entfernen; neue Aktionen (siehe API).
- `features/benutzer-rollen`: „User anlegen“ (optional E-Mail, erzwungener Wechsel), Passwort-Reset (der Satz „nur per
  API“ ist **veraltet**), neuer Absatz „Zugang verloren? Admin setzt 2FA/Passkeys zurueck“, neuer Absatz „Konten
  deaktivieren und Schutz des letzten Admins“, „Alle Zugaenge widerrufen“.
- `features/smtp-mails`: Admin kann einen Reset-Link (24 h) senden, unabhaengig von „Passwort vergessen erlauben“; der
  EN-Satz „A random password is generated only via the API“ ist **veraltet**.
- `troubleshooting`: neuer Eintrag „Nutzer hat Authenticator-App/Passkey verloren“ mit Verweis auf Benutzer →
  Passwort & Sicherheit; „Admin-Passwort vergessen“: Normalfall ist ein anderer Admin, die Konsole ist der Notfallweg.
