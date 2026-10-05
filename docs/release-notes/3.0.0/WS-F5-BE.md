### Gespeicherte Geheimnisse: Status, Wiederherstellung, Downgrade und Update-Skript (WS-F5-BE)

#### Neu
- **Status der Verschlüsselung** für Admins: `GET /api/v1/settings/secrets/status` zeigt Modus, Schlüsselquelle,
  Fingerprint, je Feld die Anzahl verschlüsselter, alter, unverschlüsselter, nicht lesbarer und leerer Werte und
  die Liste nicht lesbarer Einträge (Server, Webhook mit Besitzer, Einstellung, 2FA). Nie Werte. Die Karte in
  Einstellungen → Sicherheit folgt mit dem Frontend (F5-FE).
- **Kommandozeile im Backend-Container** `python -m app.cli.secrets`:
  - `status [--json]` – wie die Statuskarte,
  - `generate-key` – neuen Schlüssel für `SECRET_ENCRYPTION_KEY` erzeugen,
  - `reset-unreadable [--yes]` – nach Schlüsselverlust nicht lesbare Werte zurücksetzen (API-Keys leeren,
    Webhooks deaktivieren, SMTP/Captcha leeren, 2FA abschalten), bei Bedarf mit neuem Schlüssel,
  - `prepare-downgrade [--yes]` – Downgrade auf 2.4.x vorbereiten: Geheimnisse zurück in Klartext, Panel-Tokens
    mit Einschränkungen (Zonen, nur Lesen, Ablaufdatum, Admin ohne Freigabe) deaktivieren, Migrations-Marker
    löschen, Audit `DOWNGRADE_PREPARED`,
  - `decrypt-all` (Spezialfall) und `key-info` (Quelle/Fingerprint, für `update.sh`).
  Ohne `--yes` laufen die schreibenden Kommandos als Trockenlauf. Backend vorher stoppen.
- **update.sh** sichert den Schlüssel nach dem Start (wenn er nicht in der `.env` steht) als
  `<stack>-<fingerprint>.key` in `${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}` (Ordner 0700, Datei 0600) –
  nie neben dem DB-Dump und nie im Stack-Ordner, nur bei neuem Fingerprint. Neue Schalter
  `--backup-key-only` (nur Schlüssel sichern) und `--no-key-backup`.
- **setup.sh** erzeugt `SECRET_ENCRYPTION_KEY` in der `.env` (übernimmt vorhandene Werte inkl.
  `SECRET_ENCRYPTION_KEY_PREVIOUS`/`_FILE`); `install.sh` setzt ihn auch ohne `setup.sh`.
- Neue Audit-Einträge: `CAPTCHA_UPDATE`, `SMTP_TEST` (Verbindungstest und Test-Mail, nur Host/Port/Ergebnis),
  `APP_INFO_UPDATE` (nur Feldnamen), `APP_LOGO_UPLOAD`, `SECRETS_RESET_UNREADABLE`, `SECRETS_DECRYPT_ALL`,
  `DOWNGRADE_PREPARED`.

#### Geändert / Breaking
- **SMTP-Passwort**: `PUT /settings/smtp` ohne `password` (oder mit der Maske `••••••••`) behält das gespeicherte
  Passwort – bisher löschte ein fehlendes Feld es. `""` löscht. Wer Host, Port, Benutzer oder Verschlüsselung
  ändert, muss das Passwort neu eingeben (400 `secret_reentry_required`), sonst könnte ein geändertes Ziel das
  gespeicherte Passwort erhalten. Das gilt auch für `POST /settings/smtp/test`, der jetzt optional die noch nicht
  gespeicherten Formularwerte testet.
- **Basis-URL**: `PUT /settings/app-info` mit `app_base_url: ""` leert die Basis-URL (bisher ignoriert).
  `app_logo_url: ""` entfernt das Logo und löscht ein hochgeladenes `custom-logo.*`.
- **Nicht lesbare Geheimnisse** (Schlüssel fehlt/passt nicht für einzelne Werte): Server-Liste meldet
  `api_key_status: "unreadable"`, der Server wird nicht geladen (auch nicht nach dem Speichern ohne neuen Key);
  „API-Key anzeigen“ antwortet mit 409. SMTP meldet `password_unreadable`, Test und Versand brechen mit klarer
  Meldung ab. Captcha meldet `secret_key_unreadable`; die Login-Prüfung ist bis zur Neueingabe ausgesetzt
  (sonst wären alle gesperrt).
- **Downgrade auf 2.4.x** nach dem ersten 3.0-Start nur mit dem Dump von vorher oder mit
  `prepare-downgrade --yes`; beim erneuten Upgrade laufen die Token-Migration und der Audit-Backfill wieder
  (unter 2.4.x gelöschte Tokens bleiben widerrufen).
- `update.sh`: Dump mit Rechten 0600, Rechte von `/app/data` und Uploads werden vor dem Start korrigiert, das
  Skript wartet bis 120 s auf das Backend und zeigt bei einem Startabbruch die letzten Log-Zeilen (Exit 1).
  Beim Sprung auf 3.x erklärt die Major-Box Verschlüsselung, Schlüssel und Downgrade-Grenze.

#### API
- Neu: `GET /api/v1/settings/secrets/status` (Admin, nur Browser-Session).
- Geändert: `GET /settings/servers` (+`api_key_status`), `GET /settings/servers/{id}/api-key` (409 bei nicht
  lesbarem/fehlendem Key), `GET|PUT /settings/smtp` (+`password_unreadable`, Passwort-Semantik, Retarget-Schutz),
  `POST /settings/smtp/test` (optionaler Body, Retarget-Schutz), `GET|PUT /settings/captcha`
  (+`secret_key_unreadable`, Audit), `PUT /settings/app-info` (leere Basis-URL, Logo entfernen, Audit),
  `POST /settings/app-logo` (Audit). `POST/PUT /settings/servers` und `/servers/test`: `api_key` höchstens 500 Zeichen.

#### Nach dem Update prüfen
- Ausgabe von `./update.sh`: Pfad der Schlüsselkopie notieren und den Ordner getrennt vom Stack-Ordner sichern
  (bzw. die `.env`, wenn dort `SECRET_ENCRYPTION_KEY` steht).
- Einstellungen → SMTP: einmal „Testen“; bei „Passwort kann nicht entschlüsselt werden“ neu eintragen.
- Der Dump von vor dem Update enthält die Geheimnisse noch im Klartext – sicher verwahren oder löschen.

#### Website-Seiten (DE/EN)
- **neu** `features/verschluesselung`: CLI-Kommandos (`status`, `generate-key`, `reset-unreadable`,
  `prepare-downgrade`, `decrypt-all`, `key-info`) mit Exit-Codes, Ablage der Schlüsselkopie
  (`PDNSMGR_KEY_BACKUP_DIR`, `update.sh --backup-key-only`), Statusfelder und Issue-Codes.
- `update`: Abschnitt „Nach dem Update auf v3.0“ (Schlüsselkopie, Statuskarte, Klartext-Dump), Rollback mit
  `prepare-downgrade --yes` statt `decrypt-all`; neue Schalter von `update.sh`; Health-Wartezeit.
- `smtp-mails`: Passwort-Semantik (leer = behalten, Checkbox = löschen), Neueingabe bei geändertem Ziel.
- `branding`: Logo entfernen löscht die Datei; Basis-URL leeren.
- `multi-server`: Reveal nur per Browser-Session, 409 bei nicht lesbarem Key, Server „nicht geladen“.
- `troubleshooting`: „Passwort/Secret kann nicht entschlüsselt werden“, `reset-unreadable`, Startabbruch-Codes.
- Veraltet: „PUT /settings/smtp ohne password löscht das Passwort“, Downgrade-Anleitungen ohne
  `prepare-downgrade`.
