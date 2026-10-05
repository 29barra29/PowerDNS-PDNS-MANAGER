// Datums-/Zeitformatierung (Plan B.14 [F14]) - ohne React/i18n-Import, per node --test ladbar.
// `lang` ist Pflicht (z. B. i18n.language); den Komfort-Hook liefert lib/useDateFormat.js.
//
// Eingaben: Date, Zahl (ms seit Epoch) oder ISO-String. Das Backend speichert naive UTC und gibt
// ueber iso_utc() '+00:00' aus; ISO-Strings OHNE Zeitzonenangabe werden trotzdem als UTC gelesen,
// damit Altwerte nicht als Lokalzeit fehlinterpretiert werden.

export const EMPTY_VALUE = '–'
export const DEFAULT_DATETIME_OPTS = Object.freeze({ dateStyle: 'short', timeStyle: 'medium' })
export const DEFAULT_DATE_OPTS = Object.freeze({ dateStyle: 'medium' })

const ISO_NO_TZ_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/

// Die App-Sprache "sr" ist Serbisch in lateinischer Schrift; Intl wuerde fuer "sr" kyrillisch formatieren.
const INTL_LOCALES = { sr: 'sr-Latn' }

function requireLang(lang) {
    if (typeof lang !== 'string' || !lang.trim()) {
        throw new TypeError('datetime: Parameter "lang" ist Pflicht (z. B. i18n.language)')
    }
    return INTL_LOCALES[lang.trim()] || lang.trim()
}

function isEmpty(value) {
    return value === null || value === undefined || value === ''
}

// Liefert ein gueltiges Date oder null.
export function parseDateValue(value) {
    if (isEmpty(value)) return null
    let d
    if (value instanceof Date) d = new Date(value.getTime())
    else if (typeof value === 'number') d = new Date(value)
    else if (typeof value === 'string') {
        const s = value.trim()
        d = new Date(ISO_NO_TZ_RE.test(s) ? `${s.replace(' ', 'T')}Z` : s)
    } else return null
    return Number.isNaN(d.getTime()) ? null : d
}

// Intl-Formatter; unbekannte/ungueltige Sprach-Tags fallen auf 'en' zurueck statt zu werfen.
function dateTimeFormat(lang, opts) {
    try {
        return new Intl.DateTimeFormat(lang, opts)
    } catch (err) {
        if (err instanceof RangeError) return new Intl.DateTimeFormat('en', opts)
        throw err
    }
}

// null/undefined/'' -> '–'; ungueltig -> Originalwert als String; sonst lokalisiert.
export function formatDateTime(value, lang, opts = DEFAULT_DATETIME_OPTS) {
    const locale = requireLang(lang)
    if (isEmpty(value)) return EMPTY_VALUE
    const d = parseDateValue(value)
    if (!d) return String(value)
    return dateTimeFormat(locale, opts || DEFAULT_DATETIME_OPTS).format(d)
}

export function formatDate(value, lang) {
    return formatDateTime(value, lang, DEFAULT_DATE_OPTS)
}

const REL_UNITS = [
    ['year', 365 * 24 * 3600],
    ['month', 30 * 24 * 3600],
    ['week', 7 * 24 * 3600],
    ['day', 24 * 3600],
    ['hour', 3600],
    ['minute', 60],
    ['second', 1],
]

// Relative Angabe ("vor 5 Minuten", "in 2 Tagen"); `now` (Date|Zahl) fuer Tests.
export function formatRelative(value, lang, now = Date.now()) {
    const locale = requireLang(lang)
    if (isEmpty(value)) return EMPTY_VALUE
    const d = parseDateValue(value)
    if (!d) return String(value)
    const ref = now instanceof Date ? now.getTime() : Number(now)
    const diffSec = Math.round((d.getTime() - ref) / 1000)
    const abs = Math.abs(diffSec)
    let rtf
    try {
        rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
    } catch (err) {
        if (!(err instanceof RangeError)) throw err
        rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
    }
    for (const [unit, secs] of REL_UNITS) {
        if (abs >= secs || unit === 'second') return rtf.format(Math.round(diffSec / secs), unit)
    }
    return rtf.format(diffSec, 'second')
}

const pad = (n) => String(n).padStart(2, '0')

// <input type="datetime-local">-Wert (Lokalzeit) -> ISO-UTC-String; leer/ungueltig -> ''
export function toIsoFromLocalInput(v) {
    if (isEmpty(v)) return ''
    const d = new Date(String(v))
    return Number.isNaN(d.getTime()) ? '' : d.toISOString()
}

// ISO-Wert -> 'YYYY-MM-DDTHH:mm' in Lokalzeit fuer datetime-local; leer/ungueltig -> ''
export function toLocalInputFromIso(iso) {
    const d = parseDateValue(iso)
    if (!d) return ''
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
