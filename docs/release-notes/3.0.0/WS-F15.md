### LUA- & Geo-Records (WS-F15)

**Neu**
- **LUA-Records** (PowerDNS-Typ 65402) anlegen, bearbeiten, klonen und löschen: Im Record-Dialog den Typ
  „LUA – dynamischer Record (Skript)“ wählen, dann **Ziel-Typ** (A, AAAA, CNAME, TXT, MX, SRV, PTR, CAA, NAPTR, LOC,
  SPF, HTTPS, SVCB, SSHFP, TLSA) und **Lua-Code** eingeben. Mehrere Werte im selben LUA-RRset sind möglich (z. B. A
  und AAAA).
- **Vorlagen** zum Einfügen: Failover per Port-Check (IPv4/IPv6), Failover per HTTP-Check, gewichtete Verteilung,
  Antwort nach Netz des Clients sowie die Geo-Vorlagen „Nächstgelegener Server“ (`pickclosest`), „Antwort nach Land“
  (`country`), „Antwort nach Kontinent“ (`continent`) und „Standort des Clients als TXT“ (`latlon`).
- **Live-Prüfung** im Editor (Anführungszeichen, Klammern, offene Zeichenketten/Kommentare, Länge bis 4000 Zeichen,
  erlaubter Ziel-Typ), Zeichenzähler, aufklappbare **Syntax-Hilfe** und eine **Warnbox** mit dem LUA-Status jedes
  beteiligten PowerDNS-Servers (`enable-lua-records` an/aus/unbekannt, fehlendes geoip-Backend bei Geo-Funktionen).
- **Geo-Records** gibt es ausschließlich über LUA-Funktionen (`pickclosest`, `country`, `continent`, `latlon` …). Das
  PowerDNS-geoip-Backend mit YAML-Zonen ist nicht über die HTTP-API pflegbar und deshalb nicht im Panel verwaltbar.
- **Admin-Schalter** unter Einstellungen → DNS-Optionen → **LUA-Records**: „Nur Administratoren“ (Standard), „Alle mit
  Schreibrecht auf die Zone“ oder „Deaktiviert“. Die Karte zeigt je PowerDNS-Server `enable-lua-records`,
  geoip-Backend, `edns-subnet-processing` und das Exec-Limit („Erneut prüfen“).
- In der Record-Tabelle: LUA-Werte mit Ziel-Typ-Badge und Code; Hinweis-Chip, wenn LUA auf einem beteiligten Server
  ausgeschaltet ist; ohne LUA-Recht sind Bearbeiten/Klonen bei LUA-Zeilen gesperrt (Löschen bleibt möglich).
- **Filter in der Zonenansicht** (für alle Typen): Name, Typ oder Wert durchsuchen, Typ auswählen, „x von y Einträgen“;
  ESC leert das Feld. Ausgewählte, aber ausgeblendete Einträge (Bulk-Editor) werden gemeldet.
- **Zonen-Import**: Die Vorschau versteht LUA- und ALIAS-Zeilen (vorher Parse-Fehler), zeigt die Anzahl der
  LUA-Records und Auffälligkeiten je Zeile. Ist LUA deaktiviert, sperrt das Panel den Import solcher Dateien.
- Zonen-Vorlagen: LUA-Zeilen werden beim Speichern geprüft; die Policy gilt beim Anwenden der Vorlage.
- Audit-Log: neue Aktion „LUA-Einstellungen geändert“ (`LUA_SETTINGS_UPDATE`, alter und neuer Wert).
- Alle Texte in Deutsch, Englisch, Bosnisch, Kroatisch, Ungarisch und Serbisch.

**Geändert / Breaking**
- Generische Typangaben wie `TYPE65402` werden in der Record-API mit 422 abgelehnt (kein Umweg an der LUA-Policy
  vorbei).
- Die Import-Vorschau vergleicht Namen im Record-Inhalt jetzt absolut: NS-, SOA-, MX- oder CNAME-Ziele erscheinen nicht
  mehr fälschlich als „würde hinzufügen/entfernen“ (Export → Import ohne Schein-Unterschiede).
- LUA-Records schreiben dürfen nach dem Update **nur Administratoren** (Standard-Policy `admin`); API-Tokens brauchen
  dafür die Admin-Freigabe (`allow_admin`). Bestehende LUA-Records in PowerDNS bleiben unverändert.

**API**
- `GET /api/v1/lua/policy` (alle angemeldeten Benutzer, auch Tokens): `policy`, `can_write`, `reason`,
  `target_types`, `max_content_length`.
- `GET /api/v1/lua/server-status[?refresh=true]`: nur für Admins oder Benutzer mit mindestens einem Zonenrecht; nur die
  LUA-relevanten Werte aus `GET /config`, 60 s Cache, `refresh` wirkt nur für Admins.
- `GET|PUT /api/v1/settings/lua` (nur Browser-Sitzung eines Admins): `{"policy": "admin"|"manage"|"disabled"}`.
- Record-Endpunkte: Typ `LUA`, Inhalt `<Ziel-Typ> "<Lua-Code>"` (höchstens 4000 Zeichen, keine doppelten
  Anführungszeichen im Code); 403 mit Policy-Text („LUA-Records dürfen nur Administratoren anlegen oder ändern.“ bzw.
  „… sind in diesem Panel deaktiviert …“); Löschen ist immer erlaubt.
- `POST /api/v1/zones/import/preview`: zusätzlich `lua_count`, `lua_issues`, `lua_policy`, `lua_blocked`;
  `POST /api/v1/zones/import`: 403, wenn LUA deaktiviert ist und die Datei LUA-Records enthält.

**Nach dem Update prüfen**
- Wer LUA-Records nutzen will: `enable-lua-records=yes` (oder `shared`) in die `pdns.conf` **jedes** Servers, der die
  Zone ausliefert, und PowerDNS neu starten; für Geo-Funktionen zusätzlich das geoip-Backend mit GeoIP-Datenbank und
  `edns-subnet-processing=yes`. Die Karte „LUA-Records“ zeigt, was die Server melden.
- Sollen Zonen-Verwalter LUA pflegen dürfen, die Policy bewusst auf „Alle mit Schreibrecht“ stellen (Health-Checks
  bauen Verbindungen aus dem DNS-Netz zu beliebigen Zielen auf).

**Website-Seiten (DE/EN)**
- `docs/features/lua-geo` (neu): Was LUA-Records sind, Voraussetzungen in der `pdns.conf`, Anlegen im Panel (Ziel-Typ,
  Code, Vorlagen, Live-Prüfung, Warnbox, Server-Status), Vorlagen-Katalog mit Code, Abgrenzung Geo (nur LUA-Funktionen,
  geoip-Backend nicht pflegbar), Berechtigungen (Policy-Tabelle, Löschen frei, Tokens, Import-Sperre, Bulk/Rollback),
  Sicherheit (Code läuft auf dem DNS-Server, Health-Checks = ausgehende Verbindungen), Import/Export, Einschränkungen
  (keine `"` im Code, einzeilig, 4000 Zeichen, Ziel-Typen), Fehlersuche (`dig`, SERVFAIL, geoip/ECS).
- `docs/features/zonen-records`: LUA in die Typenliste (mit Link), Satz „Das ist die vollständige Liste …“ ersetzen
  (andere Typen und `TYPE65402` werden abgelehnt), Abschnitt „Filter“.
- `docs/installation` und `components/Quickstart`: optionale Kommentarzeile `# enable-lua-records=yes`.
- `docs/sicherheit`: Abschnitt „LUA-Records“; `docs/faq`: Typenliste um LUA, Frage „Kann das Panel Geo-DNS?“;
  `docs/features/templates`: LUA in Vorlagen; `benutzer-rollen`, `multi-server`, `audit-log`, `panel-api`: Policy,
  „LUA auf allen Servern aktiv“, `LUA_SETTINGS_UPDATE`, neue Endpunkte.
- Veraltete Aussagen: Typenlisten ohne LUA (`zonen-records` DE Z. 72 / EN Z. 65, README Z. 32, `Features.astro`, `faq`).

**Repo-Doku (für WS-DOCS-REPO)**
- `README.md`: Typenliste (Z. 32) um „LUA-Records (dynamisch, inkl. Geo-Funktionen) mit Vorlagen“; im
  `pdns.conf`-Beispiel Kommentarzeile `# enable-lua-records=yes   # nur wenn LUA-/Geo-Records genutzt werden`.
- `INSTALL.md`: dieselbe Kommentarzeile im `pdns.conf`-Abschnitt plus Verweis auf die Website-Seite `lua-geo`.
- `docs/PANEL-API.md`: LUA-Format, Längenlimit, Policy-403-Texte, `TYPEnnn`-Sperre, die vier neuen Endpunkte und ein
  curl-Beispiel (Heredoc für das Quoting).
- `frontend/README.md`: `src/lib/luaRecord.js` (reine LUA-Logik, Sync-Test mit dem Backend), `src/components/lua/`,
  Slots `form-extensions/lua.ext.jsx`, `value-renderers/lua.renderer.jsx`, `settings/dns-cards/20-lua.card.jsx`.
