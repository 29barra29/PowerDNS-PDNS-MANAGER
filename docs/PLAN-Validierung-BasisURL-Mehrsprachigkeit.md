# Plan: Validierung, Basis-URL & Mehrsprachigkeit

## 1. Validierung der Profilfelder

### Ausgangslage
- **PLZ:** Es wurden Buchstaben + Zahlen eingetragen (z. B. „dasd555“).
- **Ort:** Zahlen drin (z. B. „61adas5a651“).
- **Land:** Gemischt (z. B. „asdasdas21d56a“).
- **Telefon:** Nur Buchstaben – sollte Nummer/Format sein.

### Internationaler Ansatz (nicht zu weit aus dem Fenster)
- **Keine harten Länder-Regeln** wie „nur 5 Ziffern in DE“. Sonst wird es schnell unüberschaubar (UK, USA, NL, etc.).
- **Pragmatisch:**
  - **Telefon:** Erlauben: Ziffern, Leerzeichen, `+`, `-`, `()`, ggf. `/` (Durchwahl). Keine reinen Buchstaben – mindestens eine Ziffer erforderlich. Max. Länge z. B. 25 Zeichen.
  - **PLZ:** Freitext, aber **max. Länge** (z. B. 20 Zeichen). Optional: „nur Ziffern und Buchstaben“ (keine Sonderzeichen), damit internationale Formate (UK: „SW1A 1AA“, DE: „12345“) funktionieren.
  - **Ort:** Freitext, max. Länge (z. B. 100). Keine Validierung auf „nur Buchstaben“ – viele Städte haben Zahlen/Bindestriche.
  - **Land:** Freitext, max. Länge (z. B. 100). Oder später: Dropdown mit ISO-Ländern (einmalige Liste), dann ist „Land“ einheitlich und übersetzbar.
- **Frontend:** Live-Hinweise (z. B. „Mindestens eine Ziffer“ bei Telefon, „Max. 20 Zeichen“ bei PLZ), **Backend** prüft dasselbe, damit die API nicht mit Unsinn befüllt wird.

### Technik
- **Backend:** Pydantic-Validatoren für die neuen Profilfelder (z. B. `Field(..., max_length=20, pattern=r'...')` für PLZ/Telefon).
- **Frontend:** `pattern`, `maxLength`, `minLength` wo sinnvoll; optional Fehlermeldung unter dem Feld bei Ungültigkeit.

---

## 2. Basis-URL für E-Mails (Passwort zurücksetzen)

### Problem
- Der Link in der E-Mail „Passwort zurücksetzen“ wird aus **request.base_url** gebaut.
- Das ist oft: `http://10.1.0.27:5380` (IP, HTTP, evtl. interner Port).
- Nutzer bekommen dann einen unbrauchbaren oder unsicheren Link (HTTP, interne IP).

### Lösung: Konfigurierbare „Öffentliche App-URL“
- **Eine Einstellung** (nur Admin), z. B. in **Einstellungen → System / Allgemein** oder bei „App-Info“:
  - **„Öffentliche Basis-URL“** (z. B. `https://dns.meinefirma.de`).
- **Speicherort:** In der Datenbank (z. B. `SystemSetting`: Key `app_base_url`), wie schon `app_name`, `registration_enabled`, etc.
- **Verwendung:**
  - Beim **Passwort-zurücksetzen-E-Mail** den Link so bauen:  
    `{app_base_url}/reset-password?token=...`  
    Wenn `app_base_url` leer ist: Fallback auf `request.base_url` (wie bisher).
- **Hinweis in der UI:** „Wird z. B. für Links in E-Mails (Passwort zurücksetzen) genutzt. Sollte die öffentlich erreichbare URL sein (z. B. https://dns.example.com).“

### Wo eintragen?
- Gleicher Bereich wie **System-Titel** und **Login & Registrierung** (Einstellungen → Profil für Admins), oder eigener Block „Allgemein“:
  - System-Titel  
  - **Öffentliche Basis-URL** (neu)  
  - Registrierung erlauben  
  - Passwort vergessen erlauben  

---

## 3. Mehrsprachigkeit (Multi-Language / i18n)

### Ziel
- Nutzer (nicht nur Admins) können die Oberfläche in **Englisch** (und später weitere Sprachen) anzeigen.
- **Vorbereitung:** Infrastruktur so bauen, dass man „nur noch die Übersetzungsdatei einfügen“ muss – ohne jedes Mal am Code zu drehen.

### Wie macht man das technisch?
- **Nicht** „ein eigenes JS pro Sprache“. Üblich ist:
  - **Eine Übersetzungsdatei pro Sprache** (JSON oder JS-Objekt), z. B.:
    - `locales/de.json` (Deutsch)
    - `locales/en.json` (Englisch)
  - **Keys** sind immer gleich, z. B. `profile.title`, `profile.save`, `zones.myZones`.
  - Im Code steht nur der Key: `t('profile.title')`. Die Bibliothek liefert je nach gewählter Sprache den Text aus der passenden Datei.
- **Bibliothek:** z. B. **react-i18next** (mit **i18next**). Standard in React, gut wartbar, Lazy-Loading pro Sprache möglich.

### Ablauf im Frontend
1. Beim App-Start: aktuelle Sprache laden (z. B. aus User-Einstellung oder Cookie/Browser).
2. Alle sichtbaren Texte kommen aus `t('key')` (oder aus einer Komponente, die das nutzt).
3. Beim Sprachwechsel: Sprache speichern (siehe unten), neu laden/umswitchen – keine neue „JS-Datei pro Sprache“ nötig; nur die JSON-Dateien werden getauscht.

### Wo Sprache speichern?
- **Pro Benutzer (empfohlen):** In der Datenbank beim User (z. B. `preferred_language`). Nach Login lädt das Frontend diese Sprache. Dann hat jeder User „Englisch“ oder „Deutsch“ unabhängig vom Gerät.
- **Alternativ/Fallback:** Nur im Browser (localStorage/Cookie), wenn du keine DB-Spalte willst. Dann ist die Wahl geräteabhängig.
- **Einstellungen-UI:** In **Einstellungen → Profil** (für alle User): Dropdown „Sprache / Language“ (Deutsch, English, …). Beim Speichern: Backend speichert `preferred_language`, Frontend wechselt sofort die Sprache.

### Einzelne Übersetzung bearbeitbar
- Die **einzige** Stelle, die du bei neuen Texten oder Änderungen anpassen musst, sind die **Übersetzungsdateien** (z. B. `de.json`, `en.json`).
- Kein separates „JS pro Sprache“ – genau **eine** Datei pro Sprache. Keys bleiben gleich, nur die Werte ändern sich pro Sprache.
- Bei jeder **Aktion/Änderung** in der App: Wenn neuer Text in der UI kommt, einen neuen Key anlegen (z. B. `newFeature.button`) und in **allen** Sprachdateien den Wert eintragen (DE + EN + später weitere). So bleibt alles zentral und bearbeitbar.

### Konkret: Vorbereitung „multi“
1. **i18n einbauen** (react-i18next + i18next), z. B. nur **Deutsch** zuerst (alle Texte in `de.json` auslagern).
2. **Sprachumschalter** in den Einstellungen (Profil) – sichtbar für alle User.
3. **Backend:** Optional User-Feld `preferred_language` (z. B. `de`, `en`); GET/PUT Profil erweitern; beim Login/GetMe mitgeben.
4. **Weitere Sprachen:** Einfach neue Datei `en.json` (und evtl. `fr.json` etc.) anlegen und in der Konfiguration registrieren – **kein** neues JS, nur neue Übersetzungsdatei.

### Nachteil (von dir angesprochen)
- Bei **jeder** Änderung/neuen Aktion in der UI: In **allen** Sprachdateien den passenden Key ergänzen/anpassen. Das ist der normale Aufwand bei i18n; mit einem Plan (z. B. „immer Key = Bereich.aktion“, z. B. `zones.create`) und kurzen Keys bleibt es überschaubar.

---

## Reihenfolge (Vorschlag)

| Schritt | Inhalt |
|--------|--------|
| 1 | **Basis-URL** in Einstellungen (Admin) speichern und beim Passwort-zurücksetzen-Link verwenden. |
| 2 | **Validierung** Profilfelder (Backend + Frontend): Telefon, PLZ, Ort, Land mit sinnvollen Regeln (international tauglich). |
| 3 | **i18n vorbereiten:** react-i18next einrichten, alle aktuellen Texte in `de.json` auslagern, `t('...')` im Frontend nutzen. |
| 4 | **Sprachwahl** in Einstellungen (für alle User), Backend `preferred_language` (optional). |
| 5 | **Englisch:** `en.json` anlegen und Sprache „English“ anbieten. |

Wenn du magst, können wir als Nächstes mit **Basis-URL** oder **Validierung** starten und das konkret im Code umsetzen; danach i18n Schritt für Schritt.
