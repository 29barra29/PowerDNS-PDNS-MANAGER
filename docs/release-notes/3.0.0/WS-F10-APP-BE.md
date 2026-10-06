### Single Sign-On: Anmeldung über OIDC und LDAP/Active Directory (WS-F10-APP-BE)

#### Neu
- **Anmeldung über OpenID Connect** (Keycloak, Authentik, Microsoft Entra ID u. a.): Button auf der Login-Seite,
  Authorization Code Flow mit PKCE. Fehler (Anbieter nicht erreichbar, Konto nicht freigegeben, Abbruch beim
  Anbieter …) erscheinen als verständliche Meldung auf der Login-Seite.
- **Anmeldung mit LDAP/Active-Directory-Konten** im normalen Login-Formular: Erst wird das lokale Konto geprüft,
  dann – wenn LDAP aktiv ist – das Verzeichnis. Ist das Verzeichnis nicht erreichbar, meldet das Panel das klar
  (statt „falsches Passwort“).
- **2FA nach SSO**: Hat ein Konto die Panel-2FA eingerichtet, fragt das Panel den Code nach der OIDC-Anmeldung ab
  (abschaltbar). Ist das 2FA-Geheimnis nicht lesbar, gibt es keine Anmeldung ohne zweiten Faktor.
- **Konto verknüpfen**: Lokale Konten können sich unter Einstellungen → API & Sicherheit selbst mit OIDC oder LDAP
  verknüpfen (mit aktuellem Passwort und ggf. 2FA-Code). Danach melden sie sich nur noch über den Anmeldedienst an.
- **In lokales Konto umwandeln**: Admins können ein SSO-/LDAP-Konto wieder in ein lokales Konto mit einmalig
  angezeigtem Zufallspasswort umwandeln.
- **Notfallzugang**: Ist SSO aktiv, bleibt immer mindestens ein aktiver lokaler Admin bestehen (er kann nicht
  verknüpft, deaktiviert, herabgestuft oder gelöscht werden). Ist die lokale Anmeldung abgeschaltet, melden sich
  Admins unter `/login?local=1` weiter lokal an.
- **Bestätigung bei kritischen Änderungen**: Wer Anmeldedienst, Vertrauensanker, automatische Kontoanlage,
  Gruppen-/Rollenzuordnung oder die lokale Anmeldung ändert oder ein Konto umwandelt, muss sich erneut bestätigen:
  lokale Admins mit Passwort (und 2FA-Code), SSO-Admins mit einer höchstens 10 Minuten alten Anmeldung. Nach jeder
  solchen Änderung bekommen alle aktiven lokalen Admins eine E-Mail (sofern SMTP eingerichtet ist).
- Audit-Log: neue Aktionen `SSO_SETTINGS_UPDATE`, `SSO_SETTINGS_TEST`, `USER_SSO_LINK`, `USER_CONVERT_LOCAL`;
  `LOGIN`/`LOGIN_FAILED` enthalten die Methode (`password`, `passkey`, `ldap`, `oidc`, jeweils ggf. `+totp`, `setup`).
  Secrets, Passwörter, Tokens und Codes stehen nie im Audit-Log.

#### Geändert / Breaking
- **Fehlversuche zählen je IP und je Benutzername** – auch für LDAP: Nach 5 Fehlversuchen in 15 Minuten ist ein
  Benutzername gesperrt, egal von welcher Adresse; ein gesperrter Name erreicht das Verzeichnis nicht mehr (kein
  AD-Lockout über das Panel). Dieselben Zähler gelten für den 2FA-Schritt, die LDAP-Verknüpfung und die Bestätigung
  kritischer Änderungen.
- Konten mit SSO-/LDAP-Anmeldung haben kein Panel-Passwort: Passwort-Login, „Passwort vergessen“, Passwort ändern und
  Passkeys sind für sie gesperrt; Benutzername und E-Mail verwaltet der Anmeldedienst.
- Ist die Anmeldung mit lokalen Konten abgeschaltet, sind auch die öffentliche Registrierung und „Passwort
  vergessen“ für Nicht-Admins aus.
- Login-Timing verrät nicht mehr, ob ein Benutzername existiert (ohne LDAP).
- Die Ersteinrichtung (`POST /api/v1/setup/register`) liefert jetzt das vollständige Benutzerobjekt und schreibt einen
  `LOGIN`-Eintrag (`method: "setup"`).

#### API
- Neu (öffentlich): `GET /api/v1/auth/sso/providers`, `GET /api/v1/auth/oidc/start`, `GET /api/v1/auth/oidc/callback`.
- Neu (nur Browser-Sitzung, kein API-Token): `POST /api/v1/auth/me/sso/oidc/link`, `POST /api/v1/auth/me/sso/ldap/link`,
  `GET|PUT /api/v1/settings/sso`, `POST /api/v1/settings/sso/test`, `POST /api/v1/auth/users/{id}/convert-to-local`.
- Bestätigung: Bei 403 mit `detail.code` `stepup_required`/`stepup_failed` (bzw. `reauth_required` für SSO-Admins)
  denselben Aufruf mit `"step_up": {"current_password": "…", "totp_code": "…"}` im Body wiederholen.
- `POST /api/v1/auth/login/2fa`: `two_factor_token` ist optional (OIDC-Zwischenschritt liegt im HttpOnly-Cookie).
- `GET /api/v1/auth/users`: je Benutzer `external_issuer`/`external_id`, dazu Block `sso` (Rollen-Modus, JIT je Quelle);
  alle Benutzerobjekte enthalten `auth_source`.
- `GET /api/v1/settings/app-info`: `registration_enabled` ist `false`, solange die lokale Anmeldung abgeschaltet ist.

#### Nach dem Update prüfen
- Alle bestehenden Konten sind lokal (`auth_source = local`); Anmeldung wie bisher.
- Unter Einstellungen → Anmeldung / SSO sind OIDC und LDAP aus, die automatische Kontoanlage ist aus.
- Für OIDC muss die öffentliche Basis-URL gesetzt sein; Redirect-URI beim Anbieter:
  `https://<host>/api/v1/auth/oidc/callback`.
- Bei SSO die Sitzungsdauer (`AUTH_COOKIE_MAX_AGE`) auf 8–24 Stunden verkürzen: Eine Sperre beim Anmeldedienst wirkt
  erst bei der nächsten Anmeldung.

#### Website-Seiten (DE/EN)
- `features/sso` (neu): Einrichtung Keycloak/Authentik/AD/OpenLDAP, Gruppen und Rollen, JIT, Verknüpfen,
  Notfallzugang `/login?local=1`, Bestätigung kritischer Änderungen (Passwort bzw. frische Anmeldung), Grenzen.
- `features/benutzer-rollen`: Admin-Rolle optional aus Gruppen; Konten auch per SSO; Passwort-Reset und Passkeys nur
  für lokale Konten; „In lokales Konto umwandeln“.
- `sicherheit`: „SSO absichern“ (HTTPS, exakte Redirect-URI, LDAPS, Dienstkonto mit minimalen Rechten, Sitzungsdauer
  kürzen); Fehlversuch-Zähler je Benutzername; Bestätigung kritischer Änderungen; bekannte Grenzen.
- `konfiguration`: `SSO_ALLOW_INSECURE`; **veraltet**: Aussage zu `AUTH_COOKIE_SAMESITE=strict` (funktioniert auch
  mit OIDC, das State-Cookie ist immer `lax`).
- `troubleshooting`: Tabelle `sso_error`-Codes (u. a. `state`, `idp_error`, `token`, `id_token`, `not_allowed`,
  `no_account`, `totp_unreadable`), „LDAP nicht erreichbar“ (503), „ausgesperrt“ → `/login?local=1`.
- `features/audit-log`: neue Aktionen und Login-Methoden (siehe oben).
- `faq`: **veraltet**: „Benutzer mit zwei Rollen“ (jetzt optional mit SSO) und „Backend kontaktiert keine externen
  Dienste“ (außer dem konfigurierten Anmeldedienst).
- `panel-api`: neue Endpunkte und der Bestätigungs-Ablauf (`step_up`).
