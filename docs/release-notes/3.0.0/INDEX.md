# Release-Notes 3.0.0 – Übersicht

Die Dateien in diesem Ordner sind die **Quelle** der Release-Notes von PDNS Manager 3.0.0. Jedes Fragment beschreibt
ein Arbeitspaket mit den Abschnitten *Neu*, *Geändert / Breaking*, *API*, *Nach dem Update prüfen* und
*Website-Seiten (DE/EN)* (bei den Grundlagen-Fragmenten `W0-*` als Fließtext). Die Fragmente bleiben unverändert; die
konsolidierte, für Betreiber geschriebene Fassung steht hier:

- **Changelog:** [README → v3.0.0](../../../README.md#v300--major) (Breaking, Nach dem Update prüfen, Feature-Blöcke,
  Behoben, API, Betrieb, Abhängigkeiten)
- **Upgrade-Schritte:** [INSTALL.md → Upgrade von 2.x auf 3.0](../../../INSTALL.md#upgrade-von-2x-auf-30)
- **API-Referenz:** [docs/PANEL-API.md](../../PANEL-API.md)

Wo ein Fragment vom ausgelieferten Stand abweicht, gilt der Code; die bekannten Abweichungen stehen unten unter
[Hinweise zur Konsolidierung](#hinweise-zur-konsolidierung).

## Reihenfolge

Die Feature-Blöcke im Changelog folgen dieser Reihenfolge; je Block die Fragmente, aus denen er entsteht.

| # | Block im Changelog | Fragmente |
|---|---|---|
| – | Vor dem Update lesen / Nach dem Update prüfen | alle Abschnitte *Geändert / Breaking* und *Nach dem Update prüfen*, dazu [W0-INT-BE1](W0-INT-BE1.md), [W0-INT-BE2a](W0-INT-BE2a.md), [W0-INT-BE2b](W0-INT-BE2b.md) |
| 1 | Geheimnisse verschlüsselt | [W0-SECRETS](W0-SECRETS.md), [WS-F5-BE](WS-F5-BE.md), [WS-F5-FE](WS-F5-FE.md) |
| 2 | Scoped API-Tokens | [W0-INT-BE2a](W0-INT-BE2a.md), [WS-F14-APP](WS-F14-APP.md) |
| 3 | Single Sign-On (OIDC, LDAP/Active Directory) | [WS-F10-SVC](WS-F10-SVC.md), [WS-F10-APP-BE](WS-F10-APP-BE.md), [WS-F10-APP-FE](WS-F10-APP-FE.md) |
| 4 | Benutzerverwaltung, Passwort-Reset & Zugangs-Widerruf | [WS-F2F3](WS-F2F3.md) (Benutzerteil), [WS-W2-NACHARBEIT](WS-W2-NACHARBEIT.md) (Sitzungsende beim Widerruf) |
| 5 | Record-Historie & Audit-Log | [WS-F7-BE](WS-F7-BE.md), [WS-F7-FE](WS-F7-FE.md), [WS-W1-NACHARBEIT](WS-W1-NACHARBEIT.md) |
| 6 | Webhooks mit Warteschlange | [W0-INT-BE2b](W0-INT-BE2b.md) (Warteschlange, Payload v2), [WS-F6-BE](WS-F6-BE.md), [WS-F6-FE](WS-F6-FE.md) |
| 7 | Bulk-Editor | [WS-F1](WS-F1.md) |
| 8 | DNSSEC | [WS-F4-A](WS-F4-A.md), [WS-F4-B](WS-F4-B.md), [WS-F4-C](WS-F4-C.md) |
| 9 | DynDNS | [WS-F9F11-BE](WS-F9F11-BE.md), [WS-F9F11-FE](WS-F9F11-FE.md) |
| 10 | Reverse-DNS (PTR) automatisch | [WS-F9F11-BE](WS-F9F11-BE.md), [WS-F9F11-FE](WS-F9F11-FE.md), [WS-F1](WS-F1.md) (PTR im Bulk) |
| 11 | Propagations-Check | [WS-F12F13-BE](WS-F12F13-BE.md), [WS-F12F13-FE](WS-F12F13-FE.md) |
| 12 | Prometheus-Metriken | [W0-INT-BE2b](W0-INT-BE2b.md), [WS-F12F13-BE](WS-F12F13-BE.md), [WS-F12F13-FE](WS-F12F13-FE.md) |
| 13 | LUA- & Geo-Records | [WS-F15](WS-F15.md) |
| 14 | Zonenansicht: Export und NOTIFY | [WS-F2F3](WS-F2F3.md) (Zonenteil) |
| 15 | Oberfläche & Übersetzungen | [W0-INT-FE1a](W0-INT-FE1a.md), [W0-INT-FE1b](W0-INT-FE1b.md), [W0-INT-FE2](W0-INT-FE2.md), [W0-SHARED-FE](W0-SHARED-FE.md), [W0-I18N](W0-I18N.md), [WS-F8b](WS-F8b.md), [WS-W2-NACHARBEIT](WS-W2-NACHARBEIT.md) (Tastaturbedienung) |
| – | Behoben | [W0-SHARED-BE](W0-SHARED-BE.md), [W0-INT-BE2a](W0-INT-BE2a.md), [W0-INT-BE2b](W0-INT-BE2b.md), [WS-F2F3](WS-F2F3.md), [WS-F4-A](WS-F4-A.md), [WS-F8b](WS-F8b.md), [WS-F10-APP-BE](WS-F10-APP-BE.md), [W0-INT-FE1b](W0-INT-FE1b.md), [W0-INT-FE2](W0-INT-FE2.md) |
| – | Betrieb, Skripte & Tests | [W0-INT-BE1](W0-INT-BE1.md), [W0-SHARED-BE](W0-SHARED-BE.md), [WS-F5-BE](WS-F5-BE.md), [W0-TESTINFRA](W0-TESTINFRA.md), [WS-UI-SMOKE](WS-UI-SMOKE.md) |
| – | Abhängigkeiten & Lizenzen | [W0-DEPS](W0-DEPS.md), [WS-F8b](WS-F8b.md) (Schrift lokal) |

## Alle Fragmente

| Fragment | Inhalt |
|---|---|
| [W0-DEPS](W0-DEPS.md) | Abhängigkeiten (Authlib, joserfc, ldap3, prometheus_client, cryptography) |
| [W0-I18N](W0-I18N.md) | Übersetzungen hu/sr/bs/hr vervollständigt, Pluralformen |
| [W0-INT-BE1](W0-INT-BE1.md) | Datenbank-Migration und Konfiguration für 3.0 |
| [W0-INT-BE2a](W0-INT-BE2a.md) | Rechteprüfung für API-Tokens und Sitzungen |
| [W0-INT-BE2b](W0-INT-BE2b.md) | Start, Webhook-Warteschlange, Health und Metriken |
| [W0-INT-FE1a](W0-INT-FE1a.md) | Schnellerer Start und verlässliche Sprachwahl |
| [W0-INT-FE1b](W0-INT-FE1b.md) | Einstellungsseite neu gegliedert |
| [W0-INT-FE2](W0-INT-FE2.md) | Zonenansicht, Benutzerliste und Protokoll-Aktionen umgebaut |
| [W0-SECRETS](W0-SECRETS.md) | Gespeicherte Geheimnisse verschlüsselt |
| [W0-SHARED-BE](W0-SHARED-BE.md) | Mehrere PowerDNS-Server, Login-Sperren, Access-Log ohne Geheimnisse |
| [W0-SHARED-FE](W0-SHARED-FE.md) | Gemeinsame Bausteine der Oberfläche und Übersetzungsprüfung |
| [W0-TESTINFRA](W0-TESTINFRA.md) | End-to-End-Tests und CI |
| [WS-F1](WS-F1.md) | Bulk-Editor in der Zonenansicht |
| [WS-F2F3](WS-F2F3.md) | Zonen-Export und NOTIFY, Benutzerverwaltung für Teams |
| [WS-F4-A](WS-F4-A.md) | DNSSEC: Optionen, Schlüsselverwaltung, Schutzregeln (Backend) |
| [WS-F4-B](WS-F4-B.md) | DNSSEC in der Oberfläche, Rollover-Assistent, Zonenanlage mit DNSSEC |
| [WS-F4-C](WS-F4-C.md) | DNSSEC: Prüfung von Elternzone und Nameservern |
| [WS-F5-BE](WS-F5-BE.md) | Geheimnisse: Status, Wiederherstellung, Downgrade, Update-Skript |
| [WS-F5-FE](WS-F5-FE.md) | Verschlüsselung gespeicherter Geheimnisse – Anzeige im Panel |
| [WS-F6-BE](WS-F6-BE.md) | Webhooks: Zustellung mit Wiederholungen, Protokoll und Test |
| [WS-F6-FE](WS-F6-FE.md) | Webhook-Oberfläche |
| [WS-F7-BE](WS-F7-BE.md) | Record-Historie, Zurücksetzen und Audit-Log (Backend) |
| [WS-F7-FE](WS-F7-FE.md) | Verlauf und Zurücksetzen in der Zonenansicht, neues Protokoll |
| [WS-F8b](WS-F8b.md) | Oberfläche: Fehlerbehebungen und Feinschliff |
| [WS-F9F11-BE](WS-F9F11-BE.md) | DynDNS und automatische PTR-Pflege (Backend) |
| [WS-F9F11-FE](WS-F9F11-FE.md) | DynDNS und automatische PTR-Pflege im Panel |
| [WS-F10-SVC](WS-F10-SVC.md) | Single Sign-On: Grundlagen für OIDC und LDAP |
| [WS-F10-APP-BE](WS-F10-APP-BE.md) | Single Sign-On: Anmeldung über OIDC und LDAP (Backend) |
| [WS-F10-APP-FE](WS-F10-APP-FE.md) | Single Sign-On im Panel |
| [WS-F12F13-BE](WS-F12F13-BE.md) | Propagations-Check und Prometheus-Metriken (Backend) |
| [WS-F12F13-FE](WS-F12F13-FE.md) | Propagation und Monitoring in der Oberfläche |
| [WS-F14-APP](WS-F14-APP.md) | API-Tokens verwalten: Zonen, Leserecht, Ablauf, Pausieren, Admin-Übersicht |
| [WS-F15](WS-F15.md) | LUA- & Geo-Records |
| [WS-UI-SMOKE](WS-UI-SMOKE.md) | Automatische Browser-Tests der Oberfläche |
| [WS-W1-NACHARBEIT](WS-W1-NACHARBEIT.md) | Feinschliff nach der ersten Testrunde |
| [WS-W2-NACHARBEIT](WS-W2-NACHARBEIT.md) | Feinschliff nach der zweiten Testrunde |

36 Fragmente; diese Übersicht ist keines davon.

## Hinweise zur Konsolidierung

Die Fragmente sind während der Entwicklung entstanden. Einige Aussagen sind durch spätere Arbeitspakete überholt oder
waren ungenau; im Changelog, in INSTALL.md und in PANEL-API.md steht der ausgelieferte Stand:

- **Vorgriffe auf spätere Pakete sind erledigt:** Die Hinweise „Oberfläche folgt mit F4-B“ ([WS-F4-A](WS-F4-A.md)),
  „Karte folgt mit F5-FE“ ([WS-F5-BE](WS-F5-BE.md)), „Einstellungsseite, Login-Button und Verknüpfung folgen mit
  F10-APP“ ([WS-F10-SVC](WS-F10-SVC.md)), „Zustellung liefert F6“, „LUA-Einstellung folgt mit F15“ und „`manage_ptr`
  wirkt mit F11“ ([W0-INT-BE2b](W0-INT-BE2b.md)) beschreiben Zwischenstände – alles ist in 3.0.0 enthalten.
- **[WS-F4-B](WS-F4-B.md):** Der Schritt „DNSKEY auf allen NS prüfen“ ist nicht mehr nur eine manuelle Prüfung; seit
  [WS-F4-C](WS-F4-C.md) fragt das Panel die Nameserver ab, sofern die externen DNS-Abfragen unter Einstellungen →
  Monitoring freigeschaltet sind (sonst wie beschrieben: „nicht geprüft“ mit Pflicht-Haken).
- **[WS-F9F11-BE](WS-F9F11-BE.md):** Der Satz, die PTR-Pflege beim Anlegen, Ändern und Löschen von A/AAAA-Records
  (`manage_ptr`) folge mit dem Bulk-Editor, ist überholt – `manage_ptr` wirkt an allen Record-Endpunkten und in
  `/bulk`. „`/settings/ptr` (PTR-Pflege standardmäßig an)“ ist missverständlich: Der Admin-Standard `auto_default` ist
  ab Werk **aus**; `/settings/ptr` stellt ihn ein. Das Fragment hat keine Gliederung in Neu/Breaking/API.
- **[WS-W2-NACHARBEIT](WS-W2-NACHARBEIT.md):** „Bedienung per Tastatur in allen Dialogen“ gilt für alle neuen und
  überarbeiteten Dialoge; einige ältere Dialoge haben noch keine vollständige Fokusführung. Zusätzlich zum Punkt
  unter „Geändert / Breaking“ sind auch das Sitzungsende nach „Alle Zugänge widerrufen“ bzw. Admin-Passwort-Reset
  (401) und die Antwort 503 bei `GET /settings/servers/{id}/api-key` (Audit-Eintrag nicht schreibbar) Breaking
  Changes; der Changelog führt sie dort.
- **[WS-F15](WS-F15.md):** Nach der letzten Korrektur prüft die Import-Sperre bei Policy `disabled` die Datei so, wie
  PowerDNS sie liest (auch `IN IN LUA`, `TYPE065402`, `$GENERATE` mit `$` im TTL-, Klassen- oder Typfeld,
  `$INCLUDE`), mit bewussten Fehlalarmen (z. B. das ungequotete Wort `LUA` im Inhalt). Der 403-Text endet mit
  „Betroffene Zeilen: N, M.“ (höchstens 10, dann „…“). Im Fehler-Audit `IMPORT` zählt `details.lua_count` jetzt die
  betroffenen **Zeilen** (vorher Records), neu ist `details.lines`; die Vorschau liefert `lua_blocked_lines`
  (höchstens 50).
- **[WS-F4-C](WS-F4-C.md):** Der Abschnitt „Website-Seiten (DE/EN)“ fehlt. Betroffen sind `features/dnssec`
  (automatische DNSKEY-Prüfung im Rollover-Assistenten, Knopf „Elternzone prüfen“, 409 `parent_ds_present` mit
  `force`, Audit `DNSSEC_DISABLE.parent_ds`, Voraussetzung Freischaltung unter Monitoring, ausgehend UDP/TCP 53,
  gemeinsames Limit 10 Prüfungen pro Minute mit dem Propagations-Check), `features/panel-api` (Endpunkte
  `parent-ds`, `dnskey-check`, `capabilities.dnskey_check`) und `troubleshooting` (PowerDNS-Paketcache, Standard
  20 Sekunden, kann frische Änderungen kurz verdecken).
- **Formatierung:** [WS-F5-FE](WS-F5-FE.md), [WS-F9F11-FE](WS-F9F11-FE.md), [WS-F10-APP-FE](WS-F10-APP-FE.md),
  [WS-F14-APP](WS-F14-APP.md) und [WS-F15](WS-F15.md) nutzen fett gesetzte Zwischentitel statt `####`-Überschriften;
  die `W0-*`-Fragmente sind Fließtext ohne Gliederung.
- **Lizenz von prometheus_client:** [W0-DEPS](W0-DEPS.md) nennt Apache-2.0; laut Paketangabe ist es
  „Apache-2.0 AND BSD-2-Clause“.

## Für künftige Releases

Neue Fragmente kommen nach `docs/release-notes/<version>/<name>.md` mit den fünf Abschnitten oben (siehe
[CONTRIBUTING.md](../../../CONTRIBUTING.md#dokumentation)). Vor dem Release entstehen daraus Changelog, Upgrade-Hinweise
und API-Doku; dieses INDEX hält Reihenfolge und Abweichungen fest.
