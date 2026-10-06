### Verschlüsselung gespeicherter Geheimnisse – Anzeige im Panel (WS-F5-FE)

**Neu**
- **Statuskarte „Verschlüsselung gespeicherter Geheimnisse“** oben im Admin-Tab **Einstellungen → Sicherheit**: Status
  (grün/gelb/rot), Schlüsselquelle (`.env`, Schlüsseldatei oder beim Start erzeugt), Pfad der Schlüsseldatei,
  Fingerabdruck des aktiven Schlüssels (kein Geheimnis – zum Abgleich mit einer Sicherungskopie), Anzahl zusätzlicher
  Schlüssel mit „noch benötigt / nicht mehr benötigt“, Tabelle je Feld (verschlüsselt / Klartext / nicht lesbar / leer),
  Ergebnis des letzten Starts und Link zur Anleitung. Die Karte zeigt nie Schlüssel oder Werte.
- **Sicherungshinweis** in der Karte: Liegt der Schlüssel nur im Docker-Volume, steht dort der kopierbare Befehl
  `./update.sh --backup-key-only` (legt eine Kopie mit Rechten 600 in `~/.pdnsmgr-keys` bzw. `PDNSMGR_KEY_BACKUP_DIR` ab,
  nie im Projektordner neben dem Datenbank-Dump); steht er in der `.env`, der Hinweis „.env getrennt sichern“.
- **Nicht entschlüsselbare Einträge** werden einzeln aufgelistet – mit Sprung dorthin, wo der Wert neu eingetragen wird
  (Server-Tab, SMTP, Captcha, Anmeldung/SSO, Monitoring, Benutzerverwaltung für 2FA). Webhooks nennen den Besitzer.
- **Roter Admin-Hinweis über jeder Seite**, wenn Geheimnisse nicht entschlüsselt werden können oder das Backend die
  Verschlüsselung nicht aktivieren konnte (Klartext-Betrieb, mit dem Befehl zum Korrigieren der Rechte in der Karte).
  Der Hinweis nennt die PowerDNS-Server, deren API-Key nicht lesbar ist, und lässt sich für die Sitzung ausblenden;
  bei einer neuen Lage (weiterer unlesbarer Wert, anderer Schlüssel) erscheint er wieder.
- **Server-Liste**: Badges „API-Key nicht lesbar“ bzw. „API-Key fehlt“ und der Hinweis, dass ein solcher aktiver
  Server nicht geladen ist – Zonen-Änderungen werden dort übersprungen. Nach dem Neu-Eintragen des Keys erinnert ein
  Hinweis daran, die Zonen mit den anderen Servern abzugleichen.
- **Server-Dialog**: bei nicht lesbarem oder fehlendem Key kein „Vorhandenen Key anzeigen“, stattdessen eine
  Hinweisbox; ein aktiver Server lässt sich dann nur mit neuem Key speichern. Verlangt das Backend den Key nach einer
  URL-Änderung neu, erscheint der Hinweis direkt am Key-Feld. Der Dialog hat jetzt Fokusführung (ESC schließt,
  Tab bleibt im Dialog).
- **SMTP**: Warnung, wenn das gespeicherte Passwort nicht entschlüsselt werden kann; nach einer Änderung von Server,
  Port, Benutzer oder Verschlüsselung der Hinweis „Passwort erneut eingeben“. „Verbindung testen“ prüft jetzt die
  Werte im Formular, auch wenn sie noch nicht gespeichert sind.
- **Captcha**: Warnung, wenn das gespeicherte Secret nicht entschlüsselt werden kann (Captcha ist bis zur Neueingabe
  wirkungslos).
- Alle Texte in Deutsch, Englisch, Bosnisch, Kroatisch, Ungarisch und Serbisch.

**Geändert / Breaking**
- Keine Breaking Changes. Ein nicht lesbares Captcha-Secret wird beim Speichern des Captcha-Formulars ohne neue Eingabe
  nicht mehr still gelöscht (die Warnung bleibt, bis ein neues Secret eingetragen ist).

**API** (vom Panel genutzt, Backend siehe WS-F5-BE)
- `GET /api/v1/settings/secrets/status` (nur Admin mit Browser-Sitzung), `api_key_status` in
  `GET /api/v1/settings/servers`, `password_unreadable` in `GET /api/v1/settings/smtp`, `secret_key_unreadable` in
  `GET /api/v1/settings/captcha`, `POST /api/v1/settings/smtp/test` mit optionalem Body (ungespeicherte Werte),
  400 `secret_reentry_required` bei SMTP/Server.

**Nach dem Update prüfen**
- Einstellungen → Sicherheit öffnen: Status grün? Bei „Schlüssel nur im Docker-Volume“ den Schlüssel mit
  `./update.sh --backup-key-only` sichern und die Kopie getrennt vom Datenbank-Dump aufbewahren.
- Erscheint der rote Hinweis: die genannten Werte neu eintragen; bei PowerDNS-Servern danach die Zonen abgleichen.

**Website-Seiten (DE/EN)**
- `docs/features/verschluesselung` (neu): Screenshot und Beschreibung der Statuskarte (Felder, Farben, Issue-Hinweise),
  Sicherung per `./update.sh --backup-key-only` (statt `docker compose exec … cat /app/data/.secret_key > …` im
  Projektordner), roter Admin-Hinweis, Badges im Server-Tab, SMTP-/Captcha-Warnungen, Zonen-Abgleich nach dem
  Neu-Eintragen eines Server-Keys.
- `docs/features/multi-server`: Server mit unlesbarem Key ist „nicht geladen“, Zonen-Änderungen werden dort
  übersprungen; nach dem Neu-Eintragen abgleichen.
- `docs/features/smtp-mails`: Test prüft Formularwerte; Passwort nach Änderung von Server/Port/Benutzer/Verschlüsselung
  neu eingeben.
- `troubleshooting`: Abschnitt „Roter Hinweis: Geheimnisse nicht entschlüsselbar“ mit Verweis auf die Statuskarte.
