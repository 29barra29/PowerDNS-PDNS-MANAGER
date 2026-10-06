### DNSSEC in der Oberfläche: Optionen, Schlüsselverwaltung, Rollover-Assistent (WS-F4-B, Frontend und Zonenanlage zu F4)

#### Neu
- **DNSSEC-Karte in der Zonenansicht:** zeigt immer den tatsächlichen Zustand aus PowerDNS (aktiv, nicht aktiv,
  Schlüssel vorhanden aber keiner aktiv, vorsigniert) – auch für Zonen mit nur inaktiven oder vorab
  veröffentlichten Schlüsseln. Zusammenfassung mit Algorithmus, Schlüsselmodell (CSK oder KSK + ZSK), NSEC/NSEC3
  (inkl. Opt-Out/Narrow), Server und PowerDNS-Version; Hinweise (z. B. veraltete Algorithmen, zu viele
  NSEC3-Iterationen, Server mit abweichenden oder fehlenden Schlüsseln) und die Liste anderer Server mit dieser Zone.
- **DNSSEC aktivieren mit Optionen:** Schlüsselmodell (CSK empfohlen oder KSK + ZSK), Algorithmus (ECDSA P-256 als
  Standard, P-384, Ed25519, Ed448, RSA-SHA256/512 mit 2048/3072/4096 Bit), NSEC oder NSEC3 (Standard `1 0 0 -` nach
  RFC 9276, Iterationen, Salt, Opt-Out, Narrow). Liegt die Zone auf weiteren Servern, warnt der Dialog vorab. Nach dem
  Aktivieren öffnet sich direkt der DS-Assistent.
- **Schlüsseltabelle:** aktivieren/deaktivieren, veröffentlichen/zurückziehen (ab PowerDNS 4.3) und löschen, jeweils
  mit Rückfrage. Greift eine Schutzregel (z. B. letzter aktiver KSK/CSK), erklärt eine zweite Rückfrage das Risiko;
  nur nach Bestätigung wird die Aktion erzwungen.
- **Rollover-Assistent** für KSK/CSK (mit DS-Wechsel beim Registrar) und ZSK: setzt immer beim aktuellen Stand fort,
  zeigt Wartezeiten (DNSKEY-TTL, größte TTL der Zone, „seit …“), den DS des neuen Schlüssels zum Kopieren und verlangt
  vor jedem riskanten Schritt eine Bestätigung. Vor dem DS-Tausch und vor dem Löschen des alten Schlüssels steht der
  Schritt „DNSKEY auf allen NS prüfen“ – in dieser Ausbaustufe als manuelle Prüfung mit Pflicht-Haken (der
  automatische Propagations-Check folgt).
- **Serial und NOTIFY bei Master-Zonen:** In allen DNSSEC-Dialogen gibt es den Schalter „Serial erhöhen und NOTIFY
  senden“ (Standard an). Das Ergebnis (neuer Serial, NOTIFY gesendet) steht in der Meldung; scheitert NOTIFY, bleibt
  die Änderung gültig und ein gelber Hinweis nennt den Grund.
- **NSEC/NSEC3 nachträglich ändern** und **Schlüssel hinzufügen** (z. B. für einen Algorithmuswechsel, mit Warnung
  bei neuem Algorithmus) als eigene Dialoge.
- **DS-Assistent neu:** empfohlener DS (SHA-256) je aktuellem Schlüssel mit einzeln kopierbaren Feldern, alle
  Schlüssel mit Status („aktuell“, „neu – beim Registrar ergänzen“, „veraltet – entfernen“), weitere Digest-Varianten
  auf Wunsch, DNSKEY-Angaben für Registrare, die sie verlangen. Kopierfehler erscheinen im Dialog.
- **Zone anlegen mit DNSSEC-Optionen:** Beim Anlegen lassen sich dieselben Optionen wählen (eingeklappt = Standard).

#### Geändert / Breaking
- **DNSSEC deaktivieren** hat einen eigenen Dialog mit der richtigen Reihenfolge (erst DS beim Registrar entfernen,
  TTL abwarten) und Pflicht-Haken; das DS-Fenster enthält keinen Abschalt-Knopf mehr.
- **Zone anlegen auf mehreren Servern mit getrennten Datenbanken:** DNSSEC wird nur auf dem ersten Server
  eingerichtet, auf dem die Zone angelegt wurde. Weitere Server melden `created; dnssec-skipped`; scheitert DNSSEC,
  bleibt die Zone angelegt und das Ergebnis lautet `created; dnssec-error: <Grund>`. Der Anlege-Dialog bleibt dann
  offen und zeigt die Warnung. Skripte, die bei `POST /zones` nur `created` erwarten, müssen die neuen Werte kennen.
- Ungültige `dnssec_options` bei `POST /zones` ergeben 422 (vorher ignoriert).

#### API
- `POST /api/v1/zones`: `dnssec_options` (wie der Body von `POST …/dnssec/{server}/{zone}/enable`) wird jetzt
  ausgewertet; `details` je Server kann `created; dnssec-skipped` bzw. `created; dnssec-error: …` enthalten. Das
  Audit `CREATE` trägt `details.dnssec = {enabled, server, options}`, zusätzlich `DNSSEC_ENABLE` mit
  `source: "zone_create"` und das Ereignis `dnssec.enabled` (nach `zone.created`).
- Intern entfernt: die alten PowerDNS-Client-Methoden `enable_dnssec`/`disable_dnssec`/`activate_cryptokey`/
  `deactivate_cryptokey` (alle Wege laufen über die DNSSEC-Endpunkte mit Schutzregeln und Audit).

#### Nach dem Update prüfen
- Zonen mit DNSSEC öffnen: Die Karte zeigt Schlüssel und Hinweise. Bei mehreren Servern mit getrennten Datenbanken
  auf „andere Schlüssel“/„nicht signiert“ achten und die Schlüssel abgleichen (Doku Multi-Server).
- Skripte, die Zonen mit `enable_dnssec` anlegen, auf die neuen Ergebniswerte prüfen.

#### Website-Seiten (DE/EN)
- `docs/features/dnssec` (Neuschrieb): Karte, Aktivieren-Dialog mit Optionen und Algorithmus-Tabelle, Schlüsseltabelle
  mit Schutzregeln, Rollover-Assistent (KSK/CSK in Schritten inkl. DNSKEY-Prüfung, ZSK), NSEC/NSEC3 ändern,
  Deaktivieren-Dialog, DS-Assistent, Serial/NOTIFY bei Master-Zonen. Veraltet und zu streichen: „Optionen dazu gibt
  es in der UI nicht“, „Schlüssel-Rotation (nur per API)“, „eine Rotations-UI gibt es nicht“, „eine UI-Option gibt es
  nicht“ (NSEC), „NSEC3PARAM 1 0 1 ab“ als Standard, „Algorithmus 1 = SHA-1“ (richtig: Digest-Typ).
- `docs/erste-schritte`: „Aktivieren drücken“ → Dialog mit Standardwerten (CSK, ECDSA P-256, NSEC3 `1 0 0 -`), DS über
  „DS / Registrar-Assistent“ in der DNSSEC-Karte.
- `docs/features/zonen-records`: DNSSEC-Optionen beim Anlegen; bei getrennten Datenbanken nur auf dem ersten Server.
- `docs/features/multi-server`: DNSSEC nur auf dem URL-Server, Warnungen der Karte, manueller Schlüsselabgleich.
- `docs/faq`: Stichpunkt DNSSEC wie im Changelog.
