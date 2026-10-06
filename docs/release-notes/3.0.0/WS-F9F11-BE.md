### DynDNS und automatische PTR-Pflege (WS-F9F11-BE, Backend zu F9/F11)

- **DynDNS-Endpunkt:** Router (z. B. FRITZ!Box, OPNsense, ddclient, inadyn) und Skripte aktualisieren freigegebene
  Hostnamen (A/AAAA) ueber `GET /nic/update` (dyndns2-kompatibel, Textantwort) oder `GET|POST /api/v1/dyndns/update`
  (JSON mit Details je Hostname). `GET /api/v1/dyndns/whoami` prueft einen Token, ohne etwas zu aendern.
- **Eigene DynDNS-Tokens** (`dnsmgr_ddns_…`) je Benutzer: nur fuer die gelisteten Hostnamen und Typen, mit TTL
  (60–86400 s, Standard 60) und optionaler PTR-Pflege. Der Token gilt mit den Rechten des Besitzers zum Zeitpunkt des
  Updates; entzogene Zonenrechte wirken sofort. Verwaltung nur mit Browser-Anmeldung (`/api/v1/dyndns/tokens`,
  Admins sehen unter `/api/v1/dyndns/admin/tokens` alle Tokens); gespeichert wird nur ein Hash, der Klartext erscheint
  genau einmal. Hoechstens 50 Tokens je Benutzer und 20 Hostnamen je Token.
- **Token nie in die URL:** Der Token wird nur als Basic-Auth-Passwort oder Bearer-Header akzeptiert. Steht er im
  Query-String, wird die Anfrage abgelehnt und der Token sofort gesperrt (Audit `DYNDNS_TOKEN_REVOKED`). Das alte
  Secret ist damit endgueltig ungueltig: Ein so gesperrter Token laesst sich nicht einfach wieder aktivieren, sondern
  braucht ein neues Secret ("Neues Secret erzeugen").
  Browser-Anfragen von fremden Seiten werden abgewiesen.
- **Antworten:** `good`/`nochg` mit den gesetzten IPs, `badauth`, `nohost`, `notfqdn`, `numhost`, `badip`, `dnserr`,
  `911`. Unveraenderte Werte erzeugen keinen Schreibzugriff, keine Serial-Erhoehung und kein Audit.
- **Missbrauchsschutz:** Mehr als 30 Anfragen in 5 Minuten je Token beantwortet der Server mit `911` und `Retry-After`
  (Router wiederholen spaeter), ab 300 Anfragen mit `abuse`. Fehlversuche ohne gueltigen Token sperren die
  absendende IP fuer 15 Minuten – ein gueltiger Token funktioniert von dieser IP trotzdem.
- **Mehrere PowerDNS-Server:** Der Ist-Stand wird auf jedem schreibbaren Server mit der Zone geprueft. Ein veralteter
  Server wird beim naechsten Ping nachgezogen (Antwort `nochg`, Audit `DYNDNS_UPDATE` mit Kennzeichen "Reparatur");
  der Zustand bleibt ueber Neustarts erhalten. Faellt ein Server aus, reicht ein erfolgreicher Server fuer `good`.
- **PTR automatisch:** Beim DynDNS-Update (Token-Option) wird der PTR der neuen IP in einer vom Panel verwalteten
  Reverse-Zone gesetzt und der alte entfernt – nur wenn er auf den Hostnamen zeigt und Schreibrecht auf die
  Reverse-Zone besteht. Fremde PTRs werden nie ueberschrieben, Classless-Delegationen (RFC 2317) werden erkannt und
  ausgelassen. Jede Aenderung erscheint als `PTR_SYNC` im Verlauf der Reverse-Zone und laesst sich zuruecksetzen.
  Die PTR-Pflege beim Anlegen, Aendern und Loeschen von A/AAAA-Records (`manage_ptr`) folgt mit dem Bulk-Editor.
- **Einstellungen (Admins, Browser-Anmeldung):** `GET/PUT /api/v1/settings/dyndns` (DynDNS global an/aus, private
  IP-Adressen erlauben) und `GET/PUT /api/v1/settings/ptr` (PTR-Pflege standardmaessig an). `GET /api/v1/ptr/config`
  und `GET /api/v1/ptr/lookup` zeigen vorab, was mit einem PTR passieren wuerde.
- **Webhooks:** neue Ereignisse `dyndns.updated` (nur bei echter Aenderung) und `record.ptr_synced`.
- **Nach dem Update pruefen:** Hinter einem Reverse-Proxy `TRUST_PROXY_HEADERS=true` setzen und `/nic/update` im
  Proxy durchreichen – sonst sieht die IP-Erkennung nur den Proxy (Router koennen die IP auch per `myip` senden).
  Proxy-Logs sollten den `Authorization`-Header nicht protokollieren.
