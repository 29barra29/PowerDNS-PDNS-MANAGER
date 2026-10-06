import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { LUA_TEMPLATES, LUA_TEMPLATE_GROUPS, luaTemplateById } from '../../lib/luaRecord.js'

// Auswahl "Vorlage einfuegen" (F15 6.5-1): Gruppen Verfuegbarkeit/Verteilung/Geo, Beschreibung als title.
// Nach der Auswahl springt das Feld auf den Platzhalter zurueck (kontrollierter Wert '').
export default function LuaTemplatePicker({ disabled = false, onApply, className = '' }) {
    const { t } = useTranslation()
    const id = useId()
    return (
        <span className={`inline-flex items-center gap-2 ${className}`}>
            <label htmlFor={id} className="sr-only">{t('lua.templatesChoose')}</label>
            <select
                id={id}
                value=""
                disabled={disabled}
                onChange={(e) => {
                    const tpl = luaTemplateById(e.target.value)
                    if (tpl && onApply) onApply(tpl)
                }}
                className="h-9 px-3 text-xs rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50"
            >
                <option value="">{t('lua.templatesChoose')}</option>
                {LUA_TEMPLATE_GROUPS.map((group) => (
                    <optgroup key={group} label={t(`lua.templateGroup.${group}`)}>
                        {LUA_TEMPLATES.filter((tpl) => tpl.group === group).map((tpl) => (
                            <option key={tpl.id} value={tpl.id} title={t(`lua.templates.${tpl.id}.desc`)}>
                                {t(`lua.templates.${tpl.id}.label`)}{tpl.geo ? ` · ${t('lua.templateGroup.geo')}` : ''}
                            </option>
                        ))}
                    </optgroup>
                ))}
            </select>
        </span>
    )
}
