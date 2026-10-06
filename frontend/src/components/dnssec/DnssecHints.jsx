import { useTranslation } from 'react-i18next'
import { AlertCircle, AlertTriangle, Info } from 'lucide-react'
import { hintText, sortedHints } from '../../zoneDetail/dnssecModel.js'

// Hinweis-Kaesten aus status.hints (F4 §2.1 Nr. 5): danger rot, warning amber, info neutral; Reihenfolge
// danger -> warning -> info. `exclude` = Codes, die hier nicht erscheinen (Standard: sha1_ds, nur im DS-Dialog).
const STYLE = {
    danger: { box: 'bg-danger/10 border-danger/30 text-danger', Icon: AlertCircle },
    warning: { box: 'bg-warning/10 border-warning/30 text-warning', Icon: AlertTriangle },
    info: { box: 'bg-bg-secondary/60 border-border text-text-secondary', Icon: Info },
}

export default function DnssecHints({ hints, exclude, only }) {
    const { t } = useTranslation()
    let list = sortedHints(hints, exclude ? { exclude } : undefined)
    if (only) list = (hints || []).filter((h) => only.includes(h.code))
    if (!list.length) return null
    return (
        <ul className="space-y-2" aria-label={t('dnssec.hintsTitle')}>
            {list.map((hint, i) => {
                const style = STYLE[hint.level] || STYLE.info
                const { Icon } = style
                return (
                    <li key={`${hint.code}-${i}`} className={`p-3 rounded-lg border text-sm flex items-start gap-2 ${style.box}`}>
                        <Icon className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                        <span className="min-w-0 break-words">{hintText(hint, t)}</span>
                    </li>
                )
            })}
        </ul>
    )
}
