### Single Sign-On im Panel: Login mit Firmenkonto, Einstellungen und Konto-Verknuepfung (WS-F10-APP-FE)

**Neu**
- **Login-Seite:** Button „Anmelden mit …“ fuer den eingerichteten OIDC-Anbieter (Keycloak, Authentik, Microsoft
  Entra ID u. a.). LDAP-/Active-Directory-Konten melden sich im normalen Formular an (Hinweis „Du kannst dich mit
  deinem …-Konto anmelden“). Ist die lokale Anmeldung abgeschaltet, steht der SSO-Button im Vordergrund; Admins
  erreichen den Notfallzugang ueber den Link „Lokale Anmeldung fuer Administratoren“ (`/login?local=1`).
- **Verstaendliche Fehlermeldungen** nach einer gescheiterten SSO-Anmeldung (z. B. Anmeldung abgelaufen, keine
  Gruppenfreigabe, Konto deaktiviert, Anbieter nicht erreichbar, 2FA-Geheimnis nicht lesbar).
- **2FA nach SSO:** Wer im Panel TOTP eingerichtet hat, gibt nach der OIDC-Anmeldung nur noch den Code ein
  (abschaltbar, wenn der Anbieter selbst MFA erzwingt).
- **Einstellungen → Anmeldung / SSO** (nur Admins) mit drei Karten: Allgemein (Redirect-URI zum Kopieren, lokale
  Anmeldung, 2FA nach OIDC), OpenID Connect und LDAP/Active Directory. Vorlagen fuer Keycloak, Authentik, Entra ID,
  Active Directory, OpenLDAP und FreeIPA; „Verbindung testen“ prueft die Formularwerte vor dem Speichern (LDAP auf
  Wunsch mit Testbenutzer: gefundener DN, Gruppen, „Anmeldung erlaubt“, „Wird Admin“).
- **Schutz vor Fehlkonfiguration:** Automatische Kontoanlage ohne erlaubte Gruppen bzw. E-Mail-Domains nur mit
  ausdruecklicher Bestaetigung; Hinweis bei Gruppen, die kein vollstaendiger DN sind; Bestaetigung fuer ein
  LDAP-ID-Attribut, das nicht als unveraenderlich bekannt ist; Warnung bei langer Sitzungsdauer.
- **Bestaetigung vor sensiblen Aenderungen:** Wer Anbieter, Gruppen, Rollen-Zuordnung oder die lokale Anmeldung
  aendert oder ein Konto in ein lokales umwandelt, bestaetigt das mit dem eigenen Passwort (und 2FA-Code).
  Admins mit SSO-Konto melden sich dafuer kurz neu an und landen danach wieder auf derselben Seite.
- **Konto verknuepfen** (Einstellungen → API & Sicherheit): Bestehende lokale Konten lassen sich selbst mit OIDC oder
  LDAP verknuepfen (Passwort, ggf. 2FA-Code und Bestaetigung noetig). Zonenrechte, Einstellungen, 2FA und API-Tokens
  bleiben erhalten.
- **Benutzerverwaltung:** Badge „SSO“ bzw. „LDAP“ mit dem Anmeldedienst im Tooltip; im Dialog „Passwort &
  Sicherheit“ der neue Abschnitt „Externe Anmeldung“ (Anmeldedienst, Aussteller, externe ID zum Kopieren) mit
  „In lokales Konto umwandeln“ – das Zufallspasswort erscheint einmalig.
- Alle Texte in Deutsch, Englisch, Bosnisch, Kroatisch, Ungarisch und Serbisch.

**Geaendert**
- Konten mit SSO-/LDAP-Anmeldung: Benutzername und E-Mail im Profil nur lesend, statt der Passwortkarte ein Hinweis,
  2FA abschalten nur mit dem Code, keine Passkeys.
- 2FA-Karte: zeigt an, wenn das 2FA-Geheimnis nicht entschluesselt werden kann (Abschalten dann nur ueber einen Admin);
  das Code-Feld ist jetzt in allen Sprachen beschriftet. Passkey-Datum im Format der gewaehlten Sprache.

**API**
- Keine neuen Endpunkte in diesem Teil (Endpunkte: WS-F10-APP-BE). Das Frontend nutzt `GET /auth/sso/providers`,
  `GET|PUT /settings/sso`, `POST /settings/sso/test`, `POST /auth/me/sso/oidc/link`, `POST /auth/me/sso/ldap/link`,
  `POST /auth/users/{id}/convert-to-local`; Step-up-Felder als `step_up: {current_password, totp_code}`.

**Nach dem Update pruefen**
- Ohne SSO-Konfiguration sieht die Login-Seite aus wie bisher.
- Fuer OIDC zuerst die oeffentliche Basis-URL setzen (Einstellungen → Profil) und die angezeigte Redirect-URI beim
  Anbieter eintragen.
- Bei SSO die Sitzungsdauer (`AUTH_COOKIE_MAX_AGE`) auf 8–24 Stunden begrenzen.

**Website-Seiten (DE/EN)**
- `features/sso` (neu): Screenshots der Login-Seite (SSO-Button, LDAP-Hinweis, Notfall-Link), des Tabs „Anmeldung /
  SSO“ (Vorlagen, Test, Bestaetigungen JIT/ID-Attribut, Step-up-Dialog) und der Karte „Anmeldung ueber Firmenkonto“.
- `features/benutzer-rollen`: Badge „SSO/LDAP“, Abschnitt „Externe Anmeldung“, Umwandeln in lokales Konto.
- `troubleshooting`: Tabelle der Login-Fehlermeldungen (`sso_error`) inkl. „2FA-Geheimnis nicht lesbar“;
  „Erneute Anmeldung erforderlich“ beim Speichern der SSO-Einstellungen mit SSO-Konto.
- `sicherheit`: Bestaetigung per Passwort bzw. frischer Anmeldung vor sensiblen SSO-Aenderungen.
