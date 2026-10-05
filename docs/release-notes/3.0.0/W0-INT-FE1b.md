### Einstellungsseite neu gegliedert (W0-INT-FE1b, Grundlage)

- Die Einstellungen sind intern in einzelne Reiter und Karten aufgeteilt; Inhalt und Bedienung der Reiter
  bleiben wie in 2.4.1. Neue Funktionen (z. B. DNS-Optionen, Monitoring, Single Sign-on) docken als eigene
  Reiter bzw. Karten an.
- Reiter lassen sich direkt verlinken: `/settings?tab=<reiter>` (z. B. `?tab=templates`, `?tab=integrations`).
  Nicht freigegebene oder unbekannte Reiter oeffnen das Profil.
- Ein Wechsel zwischen den Reitern verwirft keine ungespeicherten Eingaben.
- **Behoben:** Ein neu erzeugter Panel-API-Token verschwand beim Klick auf "Kopieren" sofort, auch wenn das
  Kopieren fehlschlug. Token und Webhook-Secret erscheinen jetzt in einem Dialog, der bis zur Bestaetigung
  offen bleibt und anzeigt, ob das Kopieren geklappt hat. Das Webhook-Secret steht dort ohne den Vorsatz
  "Secret:".
