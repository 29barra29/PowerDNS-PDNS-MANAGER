### DynDNS und automatische PTR-Pflege im Panel (WS-F9F11-FE)

**Neu**
- **DynDNS-Karte** unter Einstellungen → API & Sicherheit (für alle angemeldeten Benutzer mit Schreibrecht auf mindestens
  eine Zone): eigene DynDNS-Tokens anlegen, bearbeiten, aktivieren/deaktivieren, mit „Neues Secret“ erneuern und löschen.
  Ein Token gilt nur für die ausgewählten Hostnamen (A und/oder AAAA, eigene TTL, optional PTR mitpflegen). Die Liste
  zeigt je Token die Hostnamen mit Status (Zone fehlt / kein Schreibrecht mehr), zuletzt benutzt (Zeit und IP), letztes
  Ergebnis und die zuletzt gesetzten IPs.
- **Einmal-Anzeige** des neuen Tokens mit eingesetzter **Einrichtungsanleitung**: FRITZ!Box (Update-URL
  `…/nic/update?hostname=<domain>&myip=<ipaddr>,<ip6addr>`, Benutzername beliebig, Kennwort = Token), andere Router
  (dyndns2), curl (Basic-Auth und JSON mit Bearer-Header) und ddclient – jeweils mit Kopier-Button. Die Anleitung weist
  ausdrücklich darauf hin, den Token **nie in die URL** zu schreiben (Tokens im Query-String werden abgelehnt und
  deaktiviert), und warnt bei einer `http://`-Basis-URL.
- **Admin-Bereich** in der DynDNS-Karte: Endpunkt ein-/ausschalten, private IP-Adressen erlauben, Liste „Alle
  DynDNS-Tokens“ mit Besitzer (fremde Tokens nur aktivieren/deaktivieren/löschen).
- **Admin-Hinweis über den Seiten des Panels** (für die Sitzung ausblendbar), wenn das Panel hinter einem Reverse-Proxy läuft, `TRUST_PROXY_HEADERS` aber aus
  ist (DynDNS würde sonst die Proxy-IP statt der Router-IP sehen).
- **„PTR in Reverse-Zone mitpflegen“** im Record-Dialog bei A/AAAA: Die Checkbox merkt sich die Auswahl je Zone im
  Browser; vor dem Speichern zeigt eine Live-Prüfung je IP, ob der PTR angelegt wird, schon stimmt, auf einen anderen
  Namen zeigt (wird nie überschrieben), ob keine Reverse-Zone im Panel liegt, ob eine Classless-Delegation vorliegt oder
  ob das Schreibrecht fehlt. Nach dem Speichern erscheinen gesetzte/entfernte PTRs in der Erfolgsmeldung, Probleme in
  einem gelben Hinweis, der stehen bleibt.
- Beim **Löschen** eines A/AAAA-Werts (Papierkorb) wird der passende PTR mit entfernt, sofern er auf den Namen zeigt und
  die PTR-Pflege aktiv ist; die Rückfrage nennt das.
- Neuer Admin-Tab **„DNS-Optionen“** mit der Karte „Reverse-DNS (PTR)“: PTR-Pflege standardmäßig an/aus (Voreinstellung
  der Checkbox und Standard für API-Aufrufe ohne `manage_ptr`).
- Audit-Log: Bezeichnungen für die neuen Aktionen (DynDNS-Token, DynDNS-Anmeldung, DynDNS-Einstellungen, PTR-Pflege,
  PTR-Einstellungen); PTR-Änderungen erscheinen im Verlauf der Reverse-Zone.
- Alle Texte in Deutsch, Englisch, Bosnisch, Kroatisch, Ungarisch und Serbisch.

**Geändert / Breaking**
- Keine Breaking Changes. Ohne gemerkte Auswahl gilt für die PTR-Checkbox der Admin-Default (ab Werk aus); das Panel
  sendet `manage_ptr` immer ausdrücklich.

**API** (vom Panel genutzt, Backend siehe WS-F9F11-BE)
- `GET /api/v1/dyndns/info|zones|tokens`, `POST|PUT|DELETE /api/v1/dyndns/tokens…`, `POST …/rotate`,
  `GET /api/v1/dyndns/admin/tokens`, `GET|PUT /api/v1/settings/dyndns`, `GET /api/v1/ptr/config`,
  `GET /api/v1/ptr/lookup`, `GET|PUT /api/v1/settings/ptr`; Record-Endpunkte mit `manage_ptr`, Antwort `details.ptr`.

**Nach dem Update prüfen**
- Läuft das Panel hinter einem Reverse-Proxy und erscheint der gelbe DynDNS-Hinweis: `TRUST_PROXY_HEADERS=true` (und
  ggf. `TRUSTED_PROXY_HOPS`) setzen.
- System-Setting „Öffentliche Basis-URL“ (`app_base_url`) prüfen – die DynDNS-Anleitung setzt sie in die Beispiele ein.

**Website-Seiten (DE/EN)**
- `docs/features/dyndns` (neu): Karte „DynDNS“, Token anlegen, Einmal-Anzeige, FRITZ!Box-Felder (Update-URL ohne
  `<pass>`/`<username>`), andere Router, curl, ddclient, Antwortcodes inkl. `911` („Server beschäftigt – Client
  wiederholt automatisch“) und `abuse`, Admin-Schalter, Proxy-Hinweis.
- `docs/features/reverse-dns` (neu): Checkbox im Record-Dialog mit Live-Prüfung, Merken je Zone, Löschen mit PTR,
  Admin-Tab „DNS-Optionen“.
- `docs/features/zonen-records`: PTR-Zeile → Verweis auf die automatische Pflege; `docs/features/templates`: veraltete
  Aussage „PTRs werden später per Hand gepflegt“ ersetzen.
- `docs/features/audit-log`: neue Aktionen; `troubleshooting`/`faq`: DynDNS mit FRITZ!Box, `badauth`/`badip` hinter Proxy.
