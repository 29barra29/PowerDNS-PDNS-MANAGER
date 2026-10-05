### Rechtepruefung fuer API-Tokens und Sitzungen (W0-INT-BE2a, Grundlage fuer F14)

- **Eingeschraenkte API-Tokens:** Panel-API-Tokens (`dnsmgr_usr_…`) koennen beim Anlegen auf einzelne Zonen,
  auf reines Lesen und auf eine Laufzeit beschraenkt werden (`scope_zones`, `permission: "read"`,
  `expires_in_days`). Die Rechte eines Tokens sind immer die Schnittmenge aus den Rechten des Benutzers und den
  Einschraenkungen des Tokens. Ein Lese-Token bekommt bei jeder schreibenden Anfrage 403, eine Zone ausserhalb des
  Scopes ebenfalls. Zonenlisten der Suche und die Zonenzahl der Server zeigen nur noch sichtbare Zonen.
  `GET /api/v1/auth/me` zeigt Skripten im neuen Block `auth`, mit welchem Token und Scope sie arbeiten.
- **Admin-Funktionen per Token nur mit Freigabe:** Admin-Endpunkte (Benutzerliste, Audit-Log, Vorlagen aendern,
  Zonen anlegen/loeschen/importieren) akzeptieren ein Token nur noch, wenn es mit „Admin-Funktionen erlauben“
  (`allow_admin`) angelegt wurde. Bestehende Tokens von Administratoren bekommen diese Freigabe beim Update
  automatisch, sie funktionieren also unveraendert.
- **Nach dem Update pruefen (Breaking Change):** Einstellungen (`/api/v1/settings/*`, inkl. Server, SMTP, Captcha,
  Branding, ACME-Tokens und API-Key-Anzeige), die Token-Verwaltung, die Webhook-Verwaltung (anlegen, aendern,
  loeschen) sowie die Anzeige von 2FA-Status und Passkeys sind nur noch mit einer Browser-Anmeldung moeglich.
  Skripte, die diese Endpunkte mit einem API-Token aufrufen, bekommen 403.
- **Weitere Haertung:** Panel-Tokens werden nur noch aus dem `Authorization`-Header akzeptiert (nicht aus dem
  Cookie). Pausierte, abgelaufene und widerrufene Tokens erhalten 401 mit eindeutiger Meldung; ein widerrufener
  Token bleibt endgueltig ungueltig. „Zuletzt benutzt“ wird hoechstens einmal pro Minute (oder bei IP-Wechsel)
  geschrieben. Die interne PowerDNS-URL und die Server-Statistiken sehen nur noch Administratoren.
- **Suche:** Die Zonenrechte werden vor der Begrenzung der Trefferzahl angewendet – eigene Treffer werden nicht mehr
  von fremden verdraengt. Die Antwort enthaelt `truncated`, wenn es mehr Treffer geben kann; die Suche ueber alle
  Server laeuft parallel und gibt keine internen Fehlertexte mehr aus.
- **Erzwungener Passwortwechsel:** Ist fuer ein Konto ein neues Passwort verlangt, laesst die Browser-Sitzung bis zum
  Wechsel nur noch das eigene Profil, den Passwortwechsel und das Abmelden zu.
- **Anmeldung:** Fehlversuche bei Passwort, 2FA-Code und Passkey zaehlen jetzt auch pro Benutzername (siehe
  W0-SHARED-BE); ein erfolgreicher Login setzt nur den eigenen Zaehler zurueck.
- **Stabilitaet:** Aenderungen an Einstellungen, Benutzern, Tokens und Vorlagen werden vor dem Senden der Antwort
  gespeichert – eine Erfolgsmeldung bedeutet damit immer auch gespeichert. Das Loeschen eines Benutzers entfernt
  auch die Webhook-Zustellungen. Die API-Pfade der Token- und Webhook-Verwaltung bleiben unveraendert.
