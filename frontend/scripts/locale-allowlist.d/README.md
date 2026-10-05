# `locale-allowlist.d/` – Allowlist-Ergänzungen je Workstream

`scripts/check-locales.mjs` liest `scripts/locale-allowlist.json` **und** alle `*.json` in diesem Ordner
(alphabetisch) und vereinigt die Listen. Die Basisdatei wird nach Welle 0 nicht mehr editiert; jeder
Workstream legt bei Bedarf **eine** eigene Datei `<ws>.json` an (klein geschrieben, z. B. `f15.json`).

```json
{
  "reason": "Warum diese Werte absichtlich identisch mit en sind bzw. warum Keys dynamisch gebildet werden",
  "global": ["lua.fn.ifportup", "lua.code*"],
  "perLanguage": { "de": ["lua.settings.title"] },
  "dynamic": ["lua.err.*"]
}
```

| Feld | Wirkung | Regel |
|---|---|---|
| `global` | Wert darf in allen Sprachen identisch mit en sein | W1 |
| `perLanguage.<lang>` | Wert darf in dieser Sprache identisch mit en sein | W1 |
| `dynamic` | Key-Präfix wird im Code per Verkettung gebildet (`t('lua.err.' + code)`); statische Referenzen auf den Präfix gelten als bekannt | E7 |
| `reason` | Pflicht (fehlt sie: Warnung W3, mit `--strict` Fehler) | W3 |

- Einträge: exakter Key oder Präfix mit `*` am Ende. Plural-Keys treffen auch über ihren Basis-Key
  (`zones.zonesCount` deckt `zones.zonesCount_one|_few|_other` ab).
- In `global`/`perLanguage` nur Fachbegriffe, Marken und Platzhalter (F8 §6.6) – die Begründung gehört in `reason`
  und in die PR-/Integrationsnotiz.
- Einträge, die nicht (mehr) zutreffen (Key fehlt in en oder Wert nicht identisch), meldet der Checker als W2;
  mit `--strict` (CI ab Wellenende 0b) ist das ein Fehler. `dynamic`-Einträge werden von W2 nicht geprüft.
- Unbekannte Felder oder falsche Typen brechen den Check ab.

Prüfen vor dem Wellenende (Fragmente virtuell gemergt, echte Dateien bleiben unverändert):

```bash
cd frontend
node scripts/check-locales.mjs --with-fragments            # Befunde des eigenen Fragments ansehen
node scripts/check-locales.mjs --with-fragments --print-identical
```
