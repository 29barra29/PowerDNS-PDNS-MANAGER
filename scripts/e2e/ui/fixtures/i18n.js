// UI-Texte aus den Sprachdateien des Frontends (frontend/src/locales/<lang>.json), damit Selektoren nicht an
// woertlich kopierten Texten haengen. Im Container read-only unter /locales (compose.ui.yaml).
const fs = require('node:fs')
const path = require('node:path')

const LOCALES_DIR = process.env.E2E_UI_LOCALES || path.join(__dirname, '..', '..', '..', '..', 'frontend', 'src', 'locales')
const cache = new Map()

function load(lang) {
  if (!cache.has(lang)) {
    cache.set(lang, JSON.parse(fs.readFileSync(path.join(LOCALES_DIR, `${lang}.json`), 'utf8')))
  }
  return cache.get(lang)
}

function lookup(lang, key) {
  let node = load(lang)
  for (const part of key.split('.')) {
    if (node == null || typeof node !== 'object' || !(part in node)) return undefined
    node = node[part]
  }
  return typeof node === 'string' ? node : undefined
}

function pluralSuffix(lang, count) {
  try {
    return new Intl.PluralRules(lang).select(count)
  } catch {
    return count === 1 ? 'one' : 'other'
  }
}

/**
 * Text zu einem i18n-Key (i18next-Format: {{var}}, Plural _one/_few/_other ueber vars.count).
 * Fehlt der Key, wird geworfen – ein Test soll nicht still mit einem Roh-Key suchen.
 */
function tl(lang, key, vars = {}) {
  let text
  if (vars.count !== undefined) {
    text = lookup(lang, `${key}_${pluralSuffix(lang, Number(vars.count))}`) ?? lookup(lang, `${key}_other`)
  }
  text = text ?? lookup(lang, key)
  if (text === undefined) throw new Error(`i18n-Key fehlt (${lang}): ${key}`)
  return text.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_, name) => (vars[name] !== undefined ? String(vars[name]) : `{{${name}}}`))
}

const t = (key, vars) => tl('de', key, vars)
const has = (key, lang = 'de') => lookup(lang, key) !== undefined

/** Regex, die den Text exakt (ohne Rand-Leerzeichen) trifft. */
function exact(text) {
  return new RegExp(`^\\s*${escapeRe(text)}\\s*$`)
}

/** Regex fuer einen Text mit Platzhaltern: {{x}} wird zu .*? */
function pattern(key, lang = 'de') {
  const raw = lookup(lang, key)
  if (raw === undefined) throw new Error(`i18n-Key fehlt (${lang}): ${key}`)
  return new RegExp(raw.split(/\{\{\s*[\w.]+\s*\}\}/).map(escapeRe).join('.*?'))
}

function escapeRe(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

module.exports = { t, tl, has, exact, pattern, escapeRe, load }
