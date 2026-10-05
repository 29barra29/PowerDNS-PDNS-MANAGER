// Komfort-Hook fuer lib/datetime.js: Sprache kommt aus i18next (Plan B.14 [F14]).
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { formatDate, formatDateTime, formatRelative } from './datetime.js'

export function useDateFormat() {
    const { i18n } = useTranslation()
    const lang = i18n.resolvedLanguage || i18n.language || 'en'
    return useMemo(() => ({
        lang,
        fmtDateTime: (value, opts) => formatDateTime(value, lang, opts),
        fmtDate: (value) => formatDate(value, lang),
        fmtRelative: (value, now) => formatRelative(value, lang, now),
    }), [lang])
}

export default useDateFormat
