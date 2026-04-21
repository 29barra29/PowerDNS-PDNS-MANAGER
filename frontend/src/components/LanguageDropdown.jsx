import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Globe, Check, ChevronDown } from 'lucide-react'
import { LANGUAGES } from '../i18n'

/**
 * Sprachauswahl als Dropdown mit Flaggen.
 *
 * - `compact` (default): nur Flagge + ChevronDown (gut fuer Mobile-Header / Auth-Seiten)
 * - `compact={false}`: Flagge + Sprachname + Chevron (Settings, Sidebar)
 * - `align`: 'left' | 'right' – auf welcher Seite das Panel ausklappt (Default 'right')
 * - `onChange`: optionaler Callback nach Sprachwechsel (z.B. Settings: API persistieren)
 */
export default function LanguageDropdown({ compact = true, align = 'right', onChange }) {
    const { i18n, t } = useTranslation()
    const [open, setOpen] = useState(false)
    const ref = useRef(null)

    const current = LANGUAGES.find((l) => l.code === i18n.language)
        || LANGUAGES.find((l) => l.code === i18n.language?.split('-')[0])
        || LANGUAGES.find((l) => l.code === 'en')
        || LANGUAGES[0]

    useEffect(() => {
        if (!open) return
        const onClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
        const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
        document.addEventListener('mousedown', onClick)
        document.addEventListener('keydown', onKey)
        return () => {
            document.removeEventListener('mousedown', onClick)
            document.removeEventListener('keydown', onKey)
        }
    }, [open])

    const choose = (code) => {
        if (code !== i18n.language) i18n.changeLanguage(code)
        setOpen(false)
        if (typeof onChange === 'function') onChange(code)
    }

    return (
        <div className="relative" ref={ref}>
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-haspopup="listbox"
                aria-expanded={open}
                aria-label={t('settings.language')}
                title={current.label}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-sm bg-bg-tertiary hover:bg-bg-hover border border-border text-text-primary transition-colors"
            >
                <Globe className="w-3.5 h-3.5 text-text-muted shrink-0" aria-hidden="true" />
                <span className="text-base leading-none" aria-hidden="true">{current.flag}</span>
                {!compact && <span className="font-medium">{current.label}</span>}
                <ChevronDown className={`w-3.5 h-3.5 text-text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
            </button>

            {open && (
                <ul
                    role="listbox"
                    className={`absolute z-50 mt-2 min-w-[12rem] rounded-xl border border-border bg-bg-secondary shadow-2xl shadow-black/30 overflow-hidden
                        ${align === 'left' ? 'left-0' : 'right-0'}`}
                >
                    {LANGUAGES.map((lng) => {
                        const active = lng.code === i18n.language
                        return (
                            <li key={lng.code}>
                                <button
                                    type="button"
                                    role="option"
                                    aria-selected={active}
                                    onClick={() => choose(lng.code)}
                                    className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm text-left transition-colors
                                        ${active ? 'bg-accent/15 text-accent-light' : 'text-text-secondary hover:bg-bg-hover hover:text-text-primary'}`}
                                >
                                    <span className="text-lg leading-none" aria-hidden="true">{lng.flag}</span>
                                    <span className="flex-1 min-w-0 truncate">{lng.label}</span>
                                    {lng.wip && (
                                        <span
                                            className="text-[10px] uppercase font-semibold tracking-wide px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30"
                                            title={t('settings.languageWip')}
                                        >
                                            WIP
                                        </span>
                                    )}
                                    {active && <Check className="w-3.5 h-3.5 text-accent-light shrink-0" aria-hidden="true" />}
                                </button>
                            </li>
                        )
                    })}
                </ul>
            )}
        </div>
    )
}
