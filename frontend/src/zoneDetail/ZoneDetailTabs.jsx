import { useRef } from 'react'
import { useTranslation } from 'react-i18next'

// Tab-Leiste der Zonenansicht (F12 §6.2, Plan B.14). tabs: [{ id, labelKey, icon }].
// `t` optional (sonst aus useTranslation). Pfeiltasten/Pos1/Ende wechseln den Tab (WAI-ARIA Tabs).
export default function ZoneDetailTabs({ tabs, active, onChange, t: tProp }) {
    const { t: tHook } = useTranslation()
    const t = tProp || tHook
    const refs = useRef({})
    const list = Array.isArray(tabs) ? tabs : []

    function focusTab(idx) {
        const tab = list[(idx + list.length) % list.length]
        if (!tab) return
        onChange(tab.id)
        refs.current[tab.id]?.focus()
    }

    function onKeyDown(e, idx) {
        if (e.key === 'ArrowRight') { e.preventDefault(); focusTab(idx + 1) }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); focusTab(idx - 1) }
        else if (e.key === 'Home') { e.preventDefault(); focusTab(0) }
        else if (e.key === 'End') { e.preventDefault(); focusTab(list.length - 1) }
    }

    return (
        <div role="tablist" className="flex gap-1 border-b border-border overflow-x-auto">
            {list.map((tab, idx) => {
                const Icon = tab.icon
                const selected = active === tab.id
                return (
                    <button
                        key={tab.id}
                        ref={(el) => { refs.current[tab.id] = el }}
                        type="button"
                        role="tab"
                        id={`zone-tab-${tab.id}`}
                        aria-selected={selected}
                        tabIndex={selected ? 0 : -1}
                        onClick={() => onChange(tab.id)}
                        onKeyDown={(e) => onKeyDown(e, idx)}
                        className={'flex items-center gap-2 px-4 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors '
                            + (selected ? 'border-accent text-text-primary' : 'border-transparent text-text-muted hover:text-text-primary')}
                    >
                        {Icon && <Icon className="w-4 h-4" aria-hidden="true" />}
                        {t(tab.labelKey)}
                    </button>
                )
            })}
        </div>
    )
}
