### Single Sign-On: Grundlagen fuer OIDC und LDAP/Active Directory (WS-F10-SVC)

Die Anmeldedienste fuer SSO sind fertig; Einstellungsseite, Login-Button und Konto-Verknuepfung folgen mit F10-APP.

- **OIDC** (Keycloak, Authentik, Microsoft Entra ID u. a.) per Authorization Code Flow mit PKCE. Das Panel prueft
  jedes ID-Token streng: nur asymmetrische Signaturen, Issuer exakt aus der Discovery, Audience, Nonce und Zeitstempel.
  Schluesselwechsel beim Anbieter werden automatisch nachgeladen.
- **LDAP/Active Directory**: Suche per Dienstkonto, Anmeldung per Bind als Benutzer, nur ueber LDAPS oder StartTLS
  mit Zertifikatspruefung (eigene CA moeglich), mehrere Server als Failover. Leere Passwoerter werden nie an das
  Verzeichnis geschickt. Hoechstens vier Verzeichnisabfragen laufen gleichzeitig; bei Ueberlast meldet das Panel sofort
  "nicht erreichbar", statt haengen zu bleiben.
- **Konten werden nur ueber eine stabile Kennung zugeordnet** (OIDC: Issuer + Subject, LDAP: objectGUID, entryUUID,
  nsUniqueId oder ipaUniqueID) – nie ueber Benutzername oder E-Mail-Adresse.
- **Sichere Voreinstellungen**: Die automatische Kontoanlage bei der ersten Anmeldung ist aus. Sie laesst sich nur
  mit erlaubten Gruppen, erlaubten E-Mail-Domains (OIDC, nur bei bestaetigter Adresse) oder einer ausdruecklichen
  Bestaetigung "jedes Konto darf sich anmelden" einschalten. Ein aenderbares ID-Attribut (z. B. sAMAccountName) muss
  ausdruecklich bestaetigt werden. LDAP-Gruppen werden nur als vollstaendiger DN verglichen.
- **Rollen aus Gruppen** (hochstufen oder abgleichen) stufen nie den letzten aktiven Admin herab; jede Aenderung steht
  im Audit-Log ("Rolle aus Gruppen uebernommen").
- Wer LDAP-Server, Bind-Benutzer, Issuer oder Client-ID aendert, muss das gespeicherte Passwort bzw. Client-Secret neu
  eingeben – es wird nie an ein geaendertes Ziel geschickt. Beide Geheimnisse liegen verschluesselt in der Datenbank.
