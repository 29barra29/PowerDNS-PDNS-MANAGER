import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from './locales/en.json'

// Zentrale Sprachliste – einmal hier pflegen, in UI ueberall importieren.
// flag = Unicode-Flag-Emoji (kein externes Asset noetig).
// Neue Sprache = JSON-Datei in ./locales/ + Eintrag hier (kein resources-Block mehr, F8 §6.3.1).
export const LANGUAGES = [
  { code: 'de', label: 'Deutsch', flag: '🇩🇪' },
  { code: 'en', label: 'English', flag: '🇬🇧' },
  { code: 'sr', label: 'Srpski', flag: '🇷🇸' },
  { code: 'hr', label: 'Hrvatski', flag: '🇭🇷' },
  { code: 'bs', label: 'Bosanski', flag: '🇧🇦' },
  { code: 'hu', label: 'Magyar', flag: '🇭🇺' },
]

export const SUPPORTED = LANGUAGES.map((l) => l.code)

const STORAGE_KEY = 'lang'

// en ist statisch im Entry-Chunk (Fallback), alle anderen Sprachen sind eigene Chunks und werden
// erst bei Bedarf geladen (F8-A03, B02).
const loaders = import.meta.glob(['./locales/*.json', '!./locales/en.json'])

// Ein laufender/fertiger Ladevorgang je Sprache; bei Fehlern wieder entfernt, damit ein spaeterer
// Versuch (z. B. nach Netzwerkausfall) erneut laedt.
const pending = new Map()

function loadBundle(lng) {
  if (lng === 'en') return Promise.resolve(en)
  const load = loaders[`./locales/${lng}.json`]
  if (!load) return Promise.reject(new Error(`Unbekannte Sprache: ${lng}`))
  if (!pending.has(lng)) {
    const p = load()
      .then((m) => m.default || m)
      .catch((err) => {
        pending.delete(lng)
        throw err
      })
    pending.set(lng, p)
  }
  return pending.get(lng)
}

// i18next-Backend: greift, wenn jemand direkt i18n.changeLanguage(code) aufruft (Altcode);
// applyLanguage laedt vorher selbst und bekommt so den Ladefehler mit.
const lazyBackend = {
  type: 'backend',
  init() {},
  read(lng, _ns, cb) {
    if (!SUPPORTED.includes(lng)) return cb(null, {})
    loadBundle(lng).then((data) => cb(null, data)).catch((err) => cb(err, null))
  },
}

// Gespeicherte, explizite Sprachwahl des Browsers (oder null).
export function getStoredLanguage() {
  try {
    const s = typeof window !== 'undefined' ? window.localStorage.getItem(STORAGE_KEY) : null
    return SUPPORTED.includes(s) ? s : null
  } catch {
    return null // localStorage blockiert (Inkognito/Cookie-Wall)
  }
}

function rememberLanguage(code) {
  try {
    if (typeof window !== 'undefined') window.localStorage.setItem(STORAGE_KEY, code)
  } catch { /* localStorage blockiert */ }
}

// Aktive Sprache (Basis-Code, z. B. "de").
export function currentLanguage() {
  return i18n.resolvedLanguage || (i18n.language || 'en').split('-')[0]
}

/**
 * Sprache wechseln (einziger Weg fuer die UI, F8 §2.1).
 * - laedt die Sprachdatei vorher; schlaegt das fehl, bleibt die alte Sprache aktiv und der Fehler wird geworfen
 * - remember=true schreibt die Wahl nach localStorage (explizite Wahl bzw. Nutzerprofil),
 *   remember=false nicht (Server-Default)
 * Rueckgabe: der aktive Sprach-Code.
 */
export async function applyLanguage(code, { remember = true } = {}) {
  if (!SUPPORTED.includes(code)) return currentLanguage()
  const data = await loadBundle(code)
  const added = !i18n.hasResourceBundle(code, 'translation')
  if (added) i18n.addResourceBundle(code, 'translation', data, true, true)
  // Auch bei gleicher Sprache neu setzen, wenn das Bundle gerade erst kam (Initial-Ladefehler) -> Re-Render
  if (added || currentLanguage() !== code) await i18n.changeLanguage(code)
  if (remember) rememberLanguage(code)
  return code
}

// Initialsprache: 1) gespeicherte Wahl 2) Browser-Sprache 3) en.
// Der Server-Default (App.jsx) und die Profilsprache (Layout.jsx) kommen spaeter ueber applyLanguage.
function detectInitialLang() {
  const stored = getStoredLanguage()
  if (stored) return stored
  if (typeof navigator !== 'undefined') {
    const nav = (navigator.language || navigator.userLanguage || '').toLowerCase().split('-')[0]
    if (SUPPORTED.includes(nav)) return nav
  }
  return 'en'
}

// Browser <html lang="..."> live mitziehen (Screenreader/SEO). Kein Speichern mehr bei jedem Wechsel.
function syncHtmlLang(lng) {
  if (typeof document !== 'undefined' && lng) document.documentElement.lang = lng
}

// Promise: Initialsprache geladen (oder Ladefehler -> en). main.jsx rendert erst danach.
export const i18nReady = i18n
  .use(lazyBackend)
  .use(initReactI18next)
  .init({
    resources: { en: { translation: en } },
    partialBundledLanguages: true,
    lng: detectInitialLang(),
    fallbackLng: 'en',
    supportedLngs: SUPPORTED,
    nonExplicitSupportedLngs: false,
    load: 'languageOnly',
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  })
  .catch((err) => {
    console.warn('i18n: Initialsprache konnte nicht geladen werden', err)
  })
  .then(() => {
    syncHtmlLang(currentLanguage())
  })

i18n.on('languageChanged', syncHtmlLang)

export default i18n
