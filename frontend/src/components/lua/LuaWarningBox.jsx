import { useTranslation } from 'react-i18next'
import { AlertTriangle, Loader2 } from 'lucide-react'
import { luaServersWithoutGeoip, luaStatusLine } from '../../lib/luaRecord.js'

// Warnbox unter dem LUA-Editor (F15 6.5-3, immer offen): vier Hinweise und der LUA-Status je relevantem Server
// (aktueller Server + schreibbare, erreichbare Peers). Nutzt der Code Geo-Funktionen, ist der geoip-Hinweis fett und
// Server ohne geoip-Backend bekommen eine eigene Zeile.
const KIND_CLASS = {
    enabled: 'text-success',
    disabled: 'text-danger',
    unknown: 'text-text-muted',
}

export default function LuaWarningBox({ status, loading = false, error = '', relevantServers = [], geo = false }) {
    const { t } = useTranslation()
    const noGeoip = geo ? luaServersWithoutGeoip(status, relevantServers) : []
    return (
        <div role="note" className="rounded-lg border border-amber-500/40 bg-amber-500/10 text-amber-200 p-3 text-xs space-y-2">
            <p className="font-medium flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                {t('lua.warnTitle')}
            </p>
            <ul className="list-disc pl-5 space-y-1 leading-relaxed">
                <li>{t('lua.warnEnable')}</li>
                <li>{t('lua.warnExec')}</li>
                <li className={geo ? 'font-semibold' : ''}>{t('lua.warnGeo')}</li>
                <li>{t('lua.warnSecondary')}</li>
            </ul>
            <div className="border-t border-amber-500/30 pt-2">
                <p className="font-medium mb-1">{t('lua.statusTitle')}</p>
                {loading ? (
                    <p className="flex items-center gap-2 text-text-muted">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> {t('lua.statusLoading')}
                    </p>
                ) : error ? (
                    <p className="text-text-muted">{t('lua.statusError', { error })}</p>
                ) : (
                    <ul className="space-y-0.5" aria-live="polite">
                        {relevantServers.map((name) => {
                            const line = luaStatusLine(status, name)
                            let text
                            if (line.kind === 'enabled') text = t('lua.statusEnabled', { server: name, mode: line.mode })
                            else if (line.kind === 'disabled') text = t('lua.statusDisabled', { server: name })
                            else text = t('lua.statusUnknown', { server: name, error: line.error || '—' })
                            return <li key={name} className={KIND_CLASS[line.kind]}>{text}</li>
                        })}
                        {noGeoip.map((name) => (
                            <li key={`geo-${name}`} className="text-amber-200">{t('lua.statusNoGeoip', { server: name })}</li>
                        ))}
                    </ul>
                )}
            </div>
        </div>
    )
}
