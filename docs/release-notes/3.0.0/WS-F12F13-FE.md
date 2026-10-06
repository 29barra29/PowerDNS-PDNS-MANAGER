### Propagation und Monitoring in der Oberfläche (WS-F12F13-FE, Frontend zu F12/F13)

#### Neu
- **Neuer Tab „Propagation“ in der Zonenansicht** (für alle mit Lesezugriff, Deep-Link `?tab=propagation`): Beim
  Öffnen startet automatisch eine Prüfung (höchstens 8 Sekunden). Die Tabelle zeigt je Panel-Server, autoritativem
  Nameserver und öffentlichem Resolver die ausgelieferte Serial, den Status (aktuell, abweichend, Fehler,
  Zeitüberschreitung, übersprungen), die Antwortzeit und verständliche Hinweise, z. B. „NOTIFY ausstehend“ oder
  „Resolver-Cache: noch bis zu 1240 s“. Optional lassen sich ein einzelner Record (Name + Typ) und der komplette
  Zoneninhalt zwischen den Panel-Servern vergleichen.
- **Getrennte Datenbanken:** Liegt die Zone auf mehreren schreibbaren Servern mit eigener Datenbank, zeigt die Zeile
  des zweiten Servers bei gleichem Inhalt „Inhalt gleich, Serial abweichend (getrennte Datenbank)“ und gilt als
  aktuell. Weicht der Inhalt ab, nennt der Hinweis die Anzahl und Beispiele der abweichenden RRsets.
- Fehler der Prüfung (z. B. zu viele Prüfungen pro Minute) erscheinen im Tab selbst; das letzte Ergebnis bleibt
  ausgegraut sichtbar. Ein aufklappbarer Kasten erklärt, warum Werte abweichen können.
- **Neuer Einstellungs-Tab „Monitoring“** (nur Admins, `/settings?tab=monitoring`):
  - **Systemstatus:** laufende Hintergrund-Aufgaben, fehlgeschlagene Datenbank-Migrationen, nicht geladene
    PowerDNS-Server und der Zustand der Verschlüsselung auf einen Blick.
  - **Propagations-Check:** externe DNS-Abfragen freischalten, autoritative Nameserver und IPv6 ein-/ausschalten,
    Resolver-Liste pflegen (Standard 1.1.1.1, 8.8.8.8, 9.9.9.9 per Klick wiederherstellbar).
  - **Prometheus-Metriken:** Scrape-Token erzeugen, neu erzeugen oder löschen; der Token wird genau einmal in einem
    Dialog angezeigt, der sich nur per Bestätigung schließt (Kopieren lässt den Token sichtbar). Dazu fertige
    Beispiele für `prometheus.yml`, einen curl-Schnelltest und nützliche Grafana-Abfragen; ohne öffentliche
    Basis-URL steht dort ein Platzhalter-Host in der gewählten Sprache. Ist `METRICS_TOKEN` in der Umgebung gesetzt,
    sind die Bedienelemente gesperrt und ein Hinweis erklärt warum.

#### Geändert / Breaking
- Keine Änderungen an bestehenden Bedienwegen; der Tab „Monitoring“ ist nur mit Admin-Browser-Anmeldung sichtbar.

#### API
- Keine eigenen Endpunkte; die Oberfläche nutzt die Endpunkte aus WS-F12F13-BE.

#### Nach dem Update prüfen
- Einstellungen → Monitoring öffnen: Systemstatus ohne Migrationsfehler und ohne nicht geladene Server.
- Wer externe Abfragen nutzen will: dort freischalten und im Tab „Propagation“ einer Zone eine Prüfung starten.
- Wer Prometheus nutzen will: Token erzeugen, sicher ablegen (Datei mit Rechten 600) und das Beispiel für
  `prometheus.yml` übernehmen.

#### Website-Seiten (DE/EN)
- `features/propagation` (neu, siehe WS-F12F13-BE): Screenshots des Tabs „Propagation“ inkl. Hinweis „getrennte
  Datenbank“ und Fehleranzeige.
- `features/monitoring` (neu, siehe WS-F12F13-BE): Screenshots des Tabs „Monitoring“ (Systemstatus, Token-Dialog,
  Beispiele); Hinweis „nur für Admins mit Browser-Anmeldung“.
- Fachbegriffe in den Übersetzungen: „Nadzor“ (bs/hr/sr), „Monitorozás“ (hu) für den Tab.
