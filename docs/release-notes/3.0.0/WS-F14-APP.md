### API-Tokens verwalten: Zonen, Leserecht, Ablauf, Pausieren und Admin-Uebersicht (WS-F14-APP)

Ergaenzt die Rechtepruefung fuer API-Tokens (W0-INT-BE2a) um die Verwaltung in Oberflaeche und API.

**Neu** (Einstellungen → API & Sicherheit → „API-Token (Panel)“, fuer jeden angemeldeten Benutzer)
- **Token erstellen im Dialog:** Bezeichnung, „Gueltig fuer“ (alle meine Zonen bzw. bei Administratoren alle Zonen,
  oder nur ausgewaehlte Zonen per Suchliste; Administratoren koennen zusaetzlich Zonen von Hand eintragen, z. B. von
  gerade nicht erreichbaren Servern), Berechtigung „Lesen & Schreiben“ oder „Nur lesen“, Ablauf nach 7/30/90/180/365
  Tagen oder „Kein Ablauf“ (mit Warnung). Vorbelegt sind bewusst sparsame Rechte: ausgewaehlte Zonen, 90 Tage.
- **Admin-Funktionen erlauben** (nur Administratoren, nur fuer Tokens ohne Zonen-Beschraenkung): Zonen anlegen,
  importieren und loeschen, Vorlagen verwalten, Benutzerliste und Audit-Log lesen. Einstellungen, Benutzerverwaltung
  und Zugangsdaten gehen weiterhin nie per Token.
- **Der neue Token erscheint genau einmal** im Geheimnis-Dialog (Kopieren mit Rueckmeldung, Schliessen nur per
  Bestaetigung) samt curl-Beispiel.
- **Liste** mit Berechtigung, Zonen (aufklappbar; Warnung bei Zonen, auf die das Konto keinen Zugriff mehr hat),
  Ablaufdatum („laeuft in n Tagen ab“ ab 14 Tagen Rest), „zuletzt benutzt“ mit Datum und IP, Status (aktiv, pausiert,
  abgelaufen) sowie den Hinweisen „Admin“, „Admin (ohne Wirkung)“ und **„Weitreichend“** (alle Zonen und kein
  Ablauf – betrifft nach dem Update alle bisherigen Tokens). Auf schmalen Bildschirmen als Kartenliste.
- **Bearbeiten** (Bezeichnung, Zonen, Berechtigung, Ablauf, Admin-Freigabe), **Pausieren/Aktivieren** und
  **Widerrufen** je Token. Beim Bearbeiten bleibt der Token-Wert gleich, Skripte muessen nichts austauschen; eine neue
  Laufzeit zaehlt ab heute. Ein abgelaufener Token wird ueber „Bearbeiten“ mit neuer Laufzeit wieder gueltig.
- **Benutzerverwaltung (Administratoren):** Badge mit Schluessel und Anzahl der aktiven Tokens je Benutzer; im Dialog
  „Passwort & Sicherheit“ der neue Abschnitt „API-Tokens dieses Benutzers“ (ansehen, einzeln oder alle widerrufen –
  nicht aendern).
- Audit-Log: neue Eintraege „API-Token geaendert“ und „API-Token widerrufen (Admin)“; „API-Token geloescht“ heisst jetzt
  „API-Token widerrufen“.
- Alle Texte in Deutsch, Englisch, Bosnisch, Kroatisch, Ungarisch und Serbisch; Datumsangaben im Format der gewaehlten
  Sprache.

**Geaendert**
- Die bisherige Eingabezeile „Bezeichnung fuer diesen Token“ mit „Token erstellen“ und die einfache Liste entfallen.
- „Widerrufen“ ist endgueltig; „Pausieren“ ist der umkehrbare Weg.

**API** (alle Pfade unter `/api/v1`, alle nur mit Browser-Anmeldung – per API-Token antworten sie mit 403)
- `GET /auth/me/panel-tokens` → `{tokens, max_tokens: 50, max_expiry_days: 3650}`; je Token (`PanelTokenOut`): `id`,
  `name`, `token_prefix`, `created_at`, `last_used_at`, `last_used_ip`, `is_active`, `status`
  (`active`|`paused`|`expired`), `expires_at`, `scope_zones` (Liste oder `null` = alle Zonen des Besitzers),
  `permission` (`manage`|`read`), `allow_admin`, `admin_effective`, `inaccessible_zones`. Widerrufene Tokens erscheinen
  nie; Hash und Klartext nie.
- `POST /auth/me/panel-tokens` (201): `{name, scope_zones?, permission?, expires_in_days?, allow_admin?}`. Ohne die
  neuen Felder entsteht ein Token wie bisher (alle Zonen, Lesen & Schreiben, kein Ablauf). Antwort
  `{token, plaintext_token, warning}`. Fehler: 400 (leere Bezeichnung, ungueltiger Zonenname, leere Zonenliste,
  Admin-Freigabe mit Zonen, mehr als 50 offene Tokens), 403 (Admin-Freigabe durch Nicht-Admin, Zone ohne eigenes
  Zonenrecht), 422 (Format; `expires_in_days` 1–3650, hoechstens 500 Zonen).
- **Neu** `PUT /auth/me/panel-tokens/{id}`: nur gesendete Felder aendern sich (`name`, `is_active`, `scope_zones`,
  `permission`, `expires_in_days`, `allow_admin`); `scope_zones: null` = alle Zonen, `expires_in_days: null` = kein
  Ablauf. Antwort `{message, token}`, ohne Aenderung `"Keine Änderungen"`. Fehler wie beim Anlegen, 404 bei fremdem,
  unbekanntem oder widerrufenem Token (widerrufene Tokens sind nicht reaktivierbar).
- `DELETE /auth/me/panel-tokens/{id}`: widerruft endgueltig (404 wenn schon widerrufen).
- **Neu (Administratoren):** `GET /auth/users/{id}/panel-tokens` → `{user_id, username, tokens}`,
  `DELETE /auth/users/{id}/panel-tokens/{token_id}` und `DELETE /auth/users/{id}/panel-tokens` (alle; Antwort
  `{message, revoked}`). 404 bei unbekanntem Benutzer bzw. Token.
- `GET /auth/users` liefert je Benutzer `panel_token_count` (aktive, nicht abgelaufene Tokens).
- Audit: `PANEL_TOKEN_CREATE` (mit Scope, Berechtigung, Ablauf, Admin-Freigabe), `PANEL_TOKEN_UPDATE` (Details
  `changed: {feld: {from, to}}`), `PANEL_TOKEN_DELETE`, `PANEL_TOKEN_ADMIN_REVOKE` (Admin als Ausloeser, Zielbenutzer
  als Ressource, `count`, `token_ids`, `names`, `prefixes`). Webhooks loest die Token-Verwaltung nicht aus.

**Nach dem Update pruefen**
- Unter „API-Token (Panel)“ alle Tokens mit „Weitreichend“ auf die noetigen Zonen beschraenken, Leserecht setzen, wo
  Schreiben nicht noetig ist, und eine Laufzeit vergeben. Admin-Tokens nur behalten, wo Skripte wirklich
  Admin-Funktionen brauchen.
- Administratoren: in der Benutzerliste pruefen, welche Konten aktive Tokens haben, und Unbenutztes widerrufen.

**Website-Seiten (DE/EN)**
- `docs/features/panel-api`: Rechte-Matrix (Zonen-Scope ∩ Benutzerrechte, Lesen/Schreiben, Admin-Freigabe nur ohne
  Zonen, Session-Pflicht-Endpunkte), Token-Dialog und Liste beschreiben, `PUT`/Admin-Endpunkte und Fehlertexte aus
  dem Abschnitt API; veraltet: „Token = voller Zugriff wie die Browser-Sitzung“.
- `docs/features/benutzer-rollen`: Token-Badge und Abschnitt „API-Tokens dieses Benutzers“; Administratoren koennen
  fremde Tokens ansehen und widerrufen, nicht aendern.
- `docs/features/audit-log`: neue Aktionen `PANEL_TOKEN_UPDATE`, `PANEL_TOKEN_ADMIN_REVOKE`, Anzeige „ueber API-Token“.
- `sicherheit`, `faq`, `update`: Empfehlung Ablauf + Zonen-Beschraenkung, Hinweis „Weitreichend“ nach dem Update.
- `branding`, `multi-server`: Beispiele mit Token gegen `/settings/*` entfernen (Session-Pflicht, siehe W0-INT-BE2a).
