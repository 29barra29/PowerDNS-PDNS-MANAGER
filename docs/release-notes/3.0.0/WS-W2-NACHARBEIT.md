### Feinschliff nach der zweiten Testrunde (WS-W2-NACHARBEIT)

#### Neu / Behoben
- **„Alle Zugänge widerrufen“ meldet auch ab:** Bestehende Browser-Sitzungen des Benutzers enden sofort (nächster
  Klick → „Sitzung abgelaufen – bitte erneut anmelden“). Das Passwort bleibt unverändert; eine neue Anmeldung ist
  gleich danach möglich. Dasselbe gilt beim Zufallspasswort durch einen Admin.
- **DynDNS-Tokens beim Widerruf endgültig gesperrt:** „Alle Zugänge widerrufen“ entwertet das Secret aller
  DynDNS-Tokens des Benutzers (auch pausierter). Ein so gesperrter Token lässt sich nicht einfach wieder
  aktivieren – erst nach „Neues Secret“.
- **PowerDNS-Server: URL-Wechsel nur mit API-Key:** Wer die URL eines Servers ändert, muss den API-Key neu eingeben.
  So kann der gespeicherte Key nicht unbemerkt an ein anderes Ziel gehen.
- **API-Key anzeigen nur mit Protokolleintrag:** Kann der Eintrag im Audit-Log nicht geschrieben werden, wird der
  Key nicht angezeigt (Meldung statt Klartext).
- **DynDNS-Reparaturen im Zonenverlauf:** Einträge, mit denen ein veralteter Server angeglichen wurde, sind als
  solche erkennbar (auch für Benutzer ohne Admin-Rechte) und lassen sich nicht zurücksetzen.
- **Bedienung per Tastatur in allen Dialogen:** Auch Bestätigung mit Passwort, API-Token, DynDNS-Token,
  DNSSEC-Dialoge, Bulk-Editor, TTL für die Auswahl, Einmal-Anzeige von Geheimnissen, Benutzer-Sicherheit und die
  Detailansicht im Audit-Log setzen den Fokus in den Dialog, halten die Tab-Taste darin, schließen mit ESC (wo
  erlaubt) und geben den Fokus danach zurück. Bei übereinanderliegenden Dialogen reagiert nur der oberste.
- **Übersetzungen (Kroatisch):** zwei Protokoll-Bezeichnungen für DynDNS-Tokens in der kroatischen Standardform.
- **Monitoring:** Die Metrik `pdnsmgr_dyndns_updates_total` zählt `badip` und `numhost` jetzt einzeln statt unter
  `other`.

#### Geändert / Breaking
- `PUT /api/v1/settings/servers/{id}`: Eine geänderte `url` ohne neuen `api_key` (fehlend, leer oder Maske) ergibt
  `400` mit `code: "secret_reentry_required"` und `fields: ["url"]`. Ohne lesbaren gespeicherten Key gilt das nicht.
  Die Maske `••••••••` wird nie als Key gespeichert.

#### API
- Sitzungen (Cookie/JWT), die vor einem Widerruf ausgestellt wurden, bekommen `401` („Sitzung abgelaufen – bitte
  erneut anmelden“). Panel-Tokens sind davon unabhängig (sie werden beim Widerruf ohnehin widerrufen).
- `GET /api/v1/settings/servers/{id}/api-key`: `503`, wenn der Audit-Eintrag nicht geschrieben werden konnte.
- `POST /api/v1/auth/users/{id}/revoke-access` und `revoked.dyndns_tokens`: zählt jetzt neu gesperrte Tokens inkl.
  pausierter; `access-summary.dyndns_tokens` zählt alle noch nicht gesperrten Tokens (auch pausierte).
- Zonenverlauf/Rollback: neuer Sperrgrund `dyndns_repair` (`DYNDNS_UPDATE` mit `details.repair = true`);
  `details.repair` ist für Nicht-Admins sichtbar.

#### Nach dem Update prüfen
- Neue Spalte `users.sessions_revoked_at` (wird beim Start automatisch angelegt, für bestehende Konten leer).
  Ein Downgrade auf 2.4.x ignoriert sie.

#### Doku-Impact
- `benutzer-rollen`/`sicherheit`: „Alle Zugänge widerrufen“ beendet auch Browser-Sitzungen; DynDNS-Tokens werden
  endgültig gesperrt.
- `installation`/`konfiguration` (Server): URL-Wechsel verlangt den API-Key.
- `monitoring`: Werte `badip`/`numhost` der DynDNS-Metrik.
- `features/record-historie`: DynDNS-Reparaturen sind nicht rücksetzbar.
