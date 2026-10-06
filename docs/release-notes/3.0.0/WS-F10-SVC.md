### Single Sign-On: Grundlagen fuer OIDC und LDAP/Active Directory (WS-F10-SVC)

Die Anmeldedienste fuer SSO sind fertig; Einstellungsseite, Login-Button und Konto-Verknuepfung folgen mit F10-APP.

#### Neu
- **OIDC** (Keycloak, Authentik, Microsoft Entra ID u. a.) per Authorization Code Flow mit PKCE. Das Panel prueft
  jedes ID-Token streng: nur asymmetrische Signaturen, Issuer exakt aus der Discovery, Audience, Nonce und Zeitstempel.
  Schluesselwechsel beim Anbieter werden automatisch nachgeladen.
- **LDAP/Active Directory**: Suche per Dienstkonto, Anmeldung per Bind als Benutzer, nur ueber LDAPS oder StartTLS
  mit Zertifikatspruefung (eigene CA moeglich), mehrere Server als Failover. Leere Passwoerter werden nie an das
  Verzeichnis geschickt. Hoechstens vier Verzeichnisabfragen laufen gleichzeitig; bei Ueberlast meldet das Panel sofort
  "nicht erreichbar", statt haengen zu bleiben.
- **Konten werden nur ueber eine stabile Kennung zugeordnet** (OIDC: Issuer + Subject, LDAP: objectGUID, entryUUID,
  nsUniqueId oder ipaUniqueID) – nie ueber Benutzername oder E-Mail-Adresse.
- **Rollen aus Gruppen** (hochstufen oder abgleichen) stufen nie den letzten aktiven Admin herab; jede Aenderung steht
  im Audit-Log ("Rolle aus Gruppen uebernommen", `USER_ROLE_SYNC`).

#### Geändert / Breaking
- **Sichere Voreinstellungen**: Die automatische Kontoanlage bei der ersten Anmeldung ist aus. Sie laesst sich nur
  mit erlaubten Gruppen, erlaubten E-Mail-Domains (OIDC, nur bei bestaetigter Adresse) oder einer ausdruecklichen
  Bestaetigung "jedes Konto darf sich anmelden" einschalten. Ein aenderbares ID-Attribut (z. B. sAMAccountName) muss
  ausdruecklich bestaetigt werden. LDAP-Gruppen werden nur als vollstaendiger DN verglichen (mindestens zwei
  Bestandteile, z. B. `cn=dns-admins,ou=groups,dc=example,dc=com`).
- Wer LDAP-Server, Bind-Benutzer, Issuer oder Client-ID aendert, muss das gespeicherte Passwort bzw. Client-Secret neu
  eingeben – es wird nie an ein geaendertes Ziel geschickt. Beide Geheimnisse liegen verschluesselt in der Datenbank.
- Neue Umgebungsvariable `SSO_ALLOW_INSECURE` (Standard `false`, nur fuer Testumgebungen ohne HTTPS/TLS).

#### API
- Keine eigenen Endpunkte; Einstellungen (`/api/v1/settings/sso`), Login-Wege und Verknuepfung kommen mit F10-APP.

#### Nach dem Update prüfen
- Nichts zu tun, solange SSO nicht eingerichtet wird. Wer SSO plant: oeffentliche Basis-URL setzen (Redirect-URI
  `https://<host>/api/v1/auth/oidc/callback`) und fuer LDAP die CA des Verzeichnisservers bereithalten.

#### Website-Seiten (DE/EN)
- **neu** `features/sso` („Single Sign-On (OIDC & LDAP)“ / „Single sign-on (OIDC & LDAP)“) – Grundlagen aus diesem
  Workstream: Zuordnung nur ueber die externe ID, JIT standardmaessig aus und nur mit Einschraenkung, LDAP-Gruppen als
  DN, nur LDAPS/StartTLS, Neueingabe von Secret/Passwort bei Zielaenderung, Rollen aus Gruppen mit Schutz des letzten
  Admins. Beispiele fuer Keycloak, Authentik und Active Directory sowie Notfallzugang ergaenzt F10-APP.
- `konfiguration`: Tabelle und Beispiel-`.env` um `SSO_ALLOW_INSECURE`.
- `sicherheit`: Absatz „SSO absichern“ (HTTPS, exakte Redirect-URI, LDAPS, Dienstkonto mit minimalen Rechten).
- `features/audit-log`: Aktion `USER_ROLE_SYNC`.
- `components/TechStack`: Auth-Zeile um „SSO: OIDC (Authlib/joserfc), LDAP (ldap3)“.
- Veraltet: `benutzer-rollen` „kennt bewusst nur zwei Rollen“ (präzisieren: Admin-Rolle optional aus Gruppen),
  `faq` „Das Backend selbst kontaktiert keine externen Dienste“ (mit SSO: der konfigurierte Anmeldedienst).
