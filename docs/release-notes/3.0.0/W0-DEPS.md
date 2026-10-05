### Abhaengigkeiten (W0-DEPS)

- Neue Python-Pakete als Grundlage fuer Single Sign-on und Monitoring: **Authlib 1.8.0** und **joserfc 1.7.5**
  (OIDC, BSD-3-Clause), **ldap3 2.9.1** (LDAP, LGPL-3.0, unveraendert als Paket genutzt) und
  **prometheus_client 0.26.0** (Prometheus-Metriken, Apache-2.0). `cryptography` ist jetzt direkt gepinnt
  (unveraendert 50.0.1).
- Alle uebrigen Paketversionen bleiben gegenueber 2.4.1 unveraendert (Lockfile nur um die neuen Pakete erweitert).
- Alle Pakete liegen als fertige Wheels fuer amd64 und arm64 vor; fuer Betreiber ist nichts zu tun – das Update
  baut das Image wie gewohnt neu.
