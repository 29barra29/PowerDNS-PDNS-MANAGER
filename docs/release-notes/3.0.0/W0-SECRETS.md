### Gespeicherte Geheimnisse verschluesselt (W0-SECRETS, Grundlage)

- Neuer Kern fuer die Verschluesselung aller umkehrbar gespeicherten Geheimnisse: PowerDNS-API-Keys,
  Webhook-Secrets **und Webhook-URLs** (bei Slack/Teams/Discord ist die URL selbst das Geheimnis),
  2FA-Geheimnisse sowie SMTP-Passwort, Captcha-Secret, OIDC-/LDAP-Secrets und Metrik-Token liegen ab 3.0
  nur noch verschluesselt (`enc:v1:`, Fernet) in der Datenbank. Ein DB-Dump ohne Schluessel verraet sie nicht mehr.
- Beim ersten 3.0-Start werden vorhandene Klartexte automatisch und wiederholbar verschluesselt. Ohne
  `SECRET_ENCRYPTION_KEY` in der `.env` erzeugt das Backend einmalig `/app/data/.secret_key` (0600) – diesen
  Schluessel **getrennt vom DB-Backup** sichern: ohne ihn sind die Geheimnisse aus einem Backup nicht lesbar.
- Schluesselverlust fuehrt nie still zu Datenverlust: Fehlt der Schluessel oder passt er nicht, startet das
  Backend nicht und nennt im Log die Ursache und beide Auswege. Ein einzelner defekter Wert legt nichts lahm,
  er wird als "nicht lesbar" gemeldet und kann neu eingetragen werden.
- Schluesselwechsel: neuen Wert als `SECRET_ENCRYPTION_KEY`, alten als `SECRET_ENCRYPTION_KEY_PREVIOUS`
  eintragen; der naechste Start schluesselt alles um.
- Mehr Schutz in den Einstellungen: Wer die Zieladresse (z. B. SMTP-Host, LDAP-Server, OIDC-Issuer) aendert,
  muss das gespeicherte Passwort/Secret neu eingeben – es wird nie an ein geaendertes Ziel weitergegeben.
