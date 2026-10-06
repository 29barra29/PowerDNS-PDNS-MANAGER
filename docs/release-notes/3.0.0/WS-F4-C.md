### DNSSEC: Prüfung von Elternzone und Nameservern (WS-F4-C, Teil B zu F4)

#### Neu
- **DNSKEY-Prüfung im Rollover-Assistenten:** Der Schritt „DNSKEY auf allen NS prüfen“ fragt jetzt jeden
  autoritativen Nameserver der Zone per DNS ab und zeigt je Nameserver, welche Schlüssel er ausliefert. „DS tauschen“
  bzw. „Umschalten“ und „alten Schlüssel löschen“ werden erst frei, wenn alle Nameserver den neuen Schlüssel liefern.
  Wer trotzdem weitermachen will, muss „Trotzdem fortfahren“ ankreuzen und eine Warnung bestätigen.
- **Elternzone prüfen:** In den DS-Schritten des Rollover-Assistenten und im Dialog „DNSSEC deaktivieren“ zeigt der
  Knopf „Elternzone prüfen“, welche DS-Records die öffentlichen Resolver für die Zone liefern und ob der neue bzw. alte
  Schlüssel dort schon (oder noch) sichtbar ist.
- **Schutz beim Deaktivieren:** Liefert ein Resolver noch einen DS der Zone, lehnt das Panel das Deaktivieren ab und
  erklärt, dass zuerst der DS beim Registrar entfernt und die TTL abgewartet werden muss. Nach einer Rückfrage lässt
  sich das Deaktivieren erzwingen; das Audit-Log vermerkt das.

#### Geändert
- Beide Prüfungen nutzen die Einstellungen unter **Einstellungen → Monitoring → Propagations-Check** (externe
  DNS-Abfragen, Resolver-Liste, autoritative Nameserver). Sind die externen Abfragen aus (Standard), fragt das Panel
  nichts ab: Der Schritt zeigt „nicht geprüft“, verlangt wie bisher die Bestätigung „Ich habe geprüft“ und nennt
  den passenden `dig`-Befehl.
- Die DNS-Prüfungen teilen sich das Limit von 10 Prüfungen pro Minute und Benutzer mit dem Propagations-Check.
- Der Propagations-Check hält während des Wartens auf einen freien Prüfplatz keine Datenbankverbindung mehr.

#### API
- Neu: `GET /api/v1/dnssec/{server}/{zone}/parent-ds` (DS bei den Resolvern, Abgleich je KSK/CSK, unbekannte Tags)
  und `GET /api/v1/dnssec/{server}/{zone}/dnskey-check?key_tag=…` (DNSKEY je Nameserver, `all_ok`). Leserecht auf
  die Zone genügt (auch Panel-Tokens mit Zonen-Scope); ohne Freigabe `enabled: false`; 429 bei zu vielen Prüfungen.
- `POST …/disable`: neuer Fehler 409 mit `code: "parent_ds_present"` und `force_possible: true`; `force: true`
  übersteuert. Audit `DNSSEC_DISABLE` mit `parent_ds`: `not_checked`, `none` oder `present_forced`.
- `GET …/status`: `capabilities.parent_ds_check` und neu `capabilities.dnskey_check` zeigen, ob die Prüfungen
  freigegeben sind.

#### Nach dem Update prüfen
- Wer die DNSKEY- und Elternzonen-Prüfung nutzen möchte, schaltet unter Monitoring die externen DNS-Abfragen ein.
  Die Firewall muss dann ausgehendes DNS (UDP/TCP 53) zu den Resolvern und den Nameservern der Zonen erlauben.
- Skripte, die DNSSEC per API deaktivieren, sollten 409 `parent_ds_present` behandeln, z. B. erst den DS beim
  Registrar entfernen oder bewusst `{"force": true}` senden.

#### Doku-Impact
- PANEL-API: zwei neue Routen, Fehlercode `parent_ds_present`, `capabilities.dnskey_check`.
- Website DNSSEC/Rollover: automatische DNSKEY-Prüfung, Knopf „Elternzone prüfen“, Voraussetzung Monitoring-Opt-in.
