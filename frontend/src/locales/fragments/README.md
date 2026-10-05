# Locale-Fragmente (`src/locales/fragments/`)

Ab 3.0 schreibt **kein Workstream** direkt in `src/locales/<lang>.json` (Plan Regel 4, B.15). Jeder Workstream
liefert seine Texte als Fragment-Dateien; der Integrator mergt sie am Wellenende und **löscht** sie danach.

## Format

Je Workstream und Sprache eine Datei `<ws>.<lang>.json` (Workstream-ID klein, z. B. `w0-shared-fe.de.json`),
für **alle** Sprachen, die es in `src/locales/` gibt (heute `de`, `en`, `bs`, `hr`, `hu`, `sr`):

```json
{
  "set": {
    "common.prev": "Zurück",
    "zones.zonesCount_one": "{{count}} Zone",
    "zones.zonesCount_other": "{{count}} Zonen"
  },
  "remove": ["settings.updatesPrivateRepo"]
}
```

- Flache Punkt-Keys; Pluralformen explizit mit Suffix. Benötigte Kategorien je Sprache laut
  `Intl.PluralRules`: de/en/hu `_one`, `_other`; sr/bs/hr `_one`, `_few`, `_other`.
- `set` legt Keys an oder ändert bestehende Werte, `remove` entfernt Keys (auch aus früheren Wellen).
  Leere Elternobjekte verschwinden mit.
- DE/EN von Hand, bs/hr/hu/sr übersetzt (nicht aus en kopieren – W1). Interpolationen `{{var}}` wie in en (E6).
- Nur Keys mit dem eigenen Präfix aus Plan Anhang A2 setzen.

## Ablauf

1. Workstream legt die sechs Dateien an und prüft lokal (im Ordner `frontend`):
   ```bash
   node scripts/merge-locale-fragments.mjs --check     # Konflikte/Struktur, schreibt nichts
   node scripts/check-locales.mjs --with-fragments     # Locale-Regeln auf dem virtuell gemergten Stand
   ```
2. Integrator am Wellenende (F.2):
   ```bash
   npm run merge:locales                 # = node scripts/merge-locale-fragments.mjs (merge + Fragmente löschen)
   npm run check:locales -- --strict     # Strict-Gate ab Wellenende 0b
   ```
   danach Commit „locales: Welle n“. Für einen lokalen Probelauf ohne Löschen: `--keep`.

## Konfliktregeln (gelten nur innerhalb einer Welle, da Fragmente verbraucht werden)

| Fall | Ergebnis |
|---|---|
| gleicher Key, gleicher Wert in zwei Fragmenten | ok |
| gleicher Key, **abweichender** Wert in zwei Fragmenten | Fehler |
| `set` auf einen Key, den ein Fragment `remove`t | Fehler |
| Key wäre zugleich Text und Objekt (`a.b` und `a.b.c`) | Fehler |
| Workstream liefert nicht alle Sprachen / Sprache ohne Locale-Datei | Fehler |
| `remove` auf nicht vorhandenen Key | Warnung |
| Key-Satz eines Workstreams unterscheidet sich zwischen Sprachen | Warnung |

Bei einem Fehler schreibt das Skript nichts und löscht keine Fragmente. Allowlist-Ergänzungen für identische
Werte oder dynamische Key-Präfixe gehören nach `scripts/locale-allowlist.d/<ws>.json`.
