import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import LuaSyntaxHelp from '../../components/lua/LuaSyntaxHelp'
import LuaTemplatePicker from '../../components/lua/LuaTemplatePicker'
import LuaWarningBox from '../../components/lua/LuaWarningBox'
import { LUA_MAX_CONTENT_LENGTH, buildLuaContent, usesGeoFunctions } from '../../lib/luaRecord.js'
import { useZoneDetail } from '../zoneDetailContext'

// Formular-Erweiterung "LUA" im Record-Dialog (F15 2.2/6.6, Vertrag B.14 / form-extensions/README.md).
// Die Felder Ziel-Typ und Lua-Code kommen aus RECORD_TYPES.LUA (zoneDetailModel.js), die Live-Pruefung aus
// FIELD_VALIDATORS.LUA. Diese Erweiterung ergaenzt je Wert-Set "Vorlage einfuegen" und den Zeichenzaehler, darunter
// die Syntax-Hilfe und die Warnbox mit dem LUA-Status der Server (ctx.ensureLuaStatus laedt ihn einmal je Seite).
// Sie sendet nichts zusaetzlich (collect -> null); ob der Nutzer LUA schreiben darf, entscheidet das Backend.

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const ext = {
    id: 'lua',
    when: (type) => type === 'LUA',
    position: 'afterValues',
    initialState: () => null,
    collect: () => null,
}

export default function LuaExtension({ form, setForm, canEdit }) {
    const { t } = useTranslation()
    const { luaStatus, luaStatusLoading, luaStatusError, luaRelevantServers, ensureLuaStatus } = useZoneDetail()
    const sets = form?.fieldsList || []

    // Status erst laden, wenn der Dialog LUA zeigt (ensureLuaStatus setzt seinen Zustand asynchron)
    useEffect(() => {
        if (typeof ensureLuaStatus === 'function') ensureLuaStatus()
    }, [ensureLuaStatus])

    function applyTemplate(idx, tpl) {
        const current = String(sets[idx]?.code || '')
        if (current.trim() && !window.confirm(t('lua.templateReplaceConfirm'))) return
        setForm((f) => ({
            fieldsList: f.fieldsList.map((s, i) => (i === idx ? { ...s, rtype: tpl.rtype, code: tpl.code } : s)),
        }))
    }

    const geo = sets.some((s) => usesGeoFunctions(s?.code || ''))

    return (
        <div className="space-y-3">
            <div className="space-y-2">
                {sets.map((set, idx) => {
                    const length = buildLuaContent(set?.rtype || 'A', set?.code || '').length
                    const over = length > LUA_MAX_CONTENT_LENGTH
                    return (
                        <div key={idx} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            {sets.length > 1 && (
                                <span className="text-xs font-medium text-text-muted">{t('zoneDetail.valueIndex', { n: idx + 1 })}</span>
                            )}
                            <LuaTemplatePicker disabled={!canEdit} onApply={(tpl) => applyTemplate(idx, tpl)} />
                            <span className={`text-xs ${over ? 'text-danger font-medium' : 'text-text-muted'}`} aria-live="polite">
                                {t('lua.charCount', { count: length, max: LUA_MAX_CONTENT_LENGTH })}
                            </span>
                        </div>
                    )
                })}
            </div>
            <LuaSyntaxHelp />
            <LuaWarningBox
                status={luaStatus}
                loading={!!luaStatusLoading}
                error={luaStatusError || ''}
                relevantServers={luaRelevantServers || []}
                geo={geo}
            />
        </div>
    )
}
