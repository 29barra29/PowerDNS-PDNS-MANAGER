import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import de from './locales/de.json'
import en from './locales/en.json'
import sr from './locales/sr.json'
import hr from './locales/hr.json'
import bs from './locales/bs.json'
import hu from './locales/hu.json'

// Zentrale Sprachliste – einmal hier pflegen, in UI ueberall importieren.
// flag = Unicode-Flag-Emoji (kein externes Asset noetig).
// Das "wip"-Flag war fuer Skeleton-Sprachen gedacht (faellt per fallbackLng auf en);
// seit v2.3.5 sind alle Sprachen uebersetzt, deshalb nicht mehr noetig.
export const LANGUAGES = [
  { code: 'de', label: 'Deutsch', flag: '🇩🇪' },
  { code: 'en', label: 'English', flag: '🇬🇧' },
  { code: 'sr', label: 'Srpski', flag: '🇷🇸' },
  { code: 'hr', label: 'Hrvatski', flag: '🇭🇷' },
  { code: 'bs', label: 'Bosanski', flag: '🇧🇦' },
  { code: 'hu', label: 'Magyar', flag: '🇭🇺' },
]

const resources = {
  de: { translation: de },
  en: { translation: en },
  sr: { translation: sr },
  hr: { translation: hr },
  bs: { translation: bs },
  hu: { translation: hu },
}

// Initialsprache: 1) localStorage (User-Wahl auf Login/Forgot/etc.) 2) Browser-Sprache 3) en
const SUPPORTED = LANGUAGES.map((l) => l.code)
const detectInitialLang = () => {
  try {
    const stored = typeof window !== 'undefined' ? window.localStorage.getItem('lang') : null
    if (stored && SUPPORTED.includes(stored)) return stored
  } catch { /* localStorage evtl. blockiert (Inkognito/Cookie-Wall) */ }
  if (typeof navigator !== 'undefined') {
    const nav = (navigator.language || navigator.userLanguage || '').toLowerCase().split('-')[0]
    if (SUPPORTED.includes(nav)) return nav
  }
  return 'en'
}

i18n
  .use(initReactI18next)
  .init({
    resources,
    lng: detectInitialLang(),
    fallbackLng: 'en',
    interpolation: {
      escapeValue: false,
    },
  })

// Sprachwechsel persistieren, damit Login/Auth-Seiten die Wahl nicht verlieren.
const persistLang = (lng) => {
  try { if (typeof window !== 'undefined') window.localStorage.setItem('lang', lng) }
  catch { /* localStorage evtl. blockiert (Inkognito/Cookie-Wall) */ }
}

// Browser <html lang="..."> live mitziehen, damit Screenreader/SEO die Sprache erkennen.
const syncHtmlLang = (lng) => {
  if (typeof document !== 'undefined' && lng) {
    document.documentElement.lang = lng
  }
}
syncHtmlLang(i18n.language)
i18n.on('languageChanged', (lng) => {
  syncHtmlLang(lng)
  persistLang(lng)
})

export default i18n
