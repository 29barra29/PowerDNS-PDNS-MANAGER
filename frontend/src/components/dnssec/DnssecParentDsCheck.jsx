import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, CheckCircle, Loader2, Search } from 'lucide-react'
import api from '../../api'
import { parentDsKeySummary, parentDsRows } from '../../zoneDetail/dnssecModel.js'

// Pruefung der Elternzone (F4 §2.5 Schritt 2/4, §2.9 Schritt 2; Teil B, WS-F4-C): Knopf "Elternzone pruefen" ->
// DS bei den oeffentlichen Resolvern aus den Propagations-Einstellungen (GET …/parent-ds). Nur Information – die
// Pflicht-Checkboxen der Dialoge bleiben (Resolver cachen DS bis zur TTL); beim Deaktivieren sperrt das Backend
// zusaetzlich mit 409 parent_ds_present.
// Ist die Pruefung nicht freigegeben (status.capabilities.parent_ds_check), steht nur der manuelle Weg (dig) da.
// Props: { server, zoneId, zone (Anzeige ohne Punkt), capability, mode: 'add'|'remove'|'disable', keyId, tag,
//          disabled }
export default function DnssecParentDsCheck({ server, zoneId, zone, capability, mode, keyId = null, tag = null, disabled = false }) {
    const { t } = useTranslation()
    const [busy, setBusy] = useState(false)
    const [result, setResult] = useState(null)
    const [error, setError] = useState('')

    if (!capability || result?.enabled === false) {
        return <p className="text-xs text-text-muted break-words">{t('dnssec.parentDsDisabled', { zone })}</p>
    }

    async function check() {
        if (busy) return
        setBusy(true)
        setError('')
        try {
            setResult(await api.getParentDs(server, zoneId))
        } catch (err) {
            setError(t('dnssec.checkRequestFailed', { error: err.message || String(err) }))
        } finally {
            setBusy(false)
        }
    }

    const rows = parentDsRows(result)
    const summary = parentDsKeySummary(result, keyId)
    const answered = rows.some((r) => r.kind !== 'error')
    let verdict = null
    if (result?.enabled && !answered) {
        verdict = { tone: 'warning', text: t('dnssec.parentDsNoAnswer') }
    } else if (result?.enabled && mode === 'disable') {
        verdict = result.any_visible
            ? { tone: 'warning', text: t('dnssec.parentDsStillPublished') }
            : { tone: 'success', text: t('dnssec.parentDsNoneVisible') }
    } else if (summary && mode === 'add') {
        verdict = summary.visibleAll
            ? { tone: 'success', text: t('dnssec.parentDsKeyVisible', { tag: tag ?? summary.tag ?? '—' }) }
            : { tone: 'warning', text: t('dnssec.parentDsKeyMissing', { tag: tag ?? summary.tag ?? '—', resolvers: summary.missingOn.join(', ') || '—' }) }
    } else if (summary && mode === 'remove') {
        verdict = summary.visibleOn.length
            ? { tone: 'warning', text: t('dnssec.parentDsKeyStillVisible', { tag: tag ?? summary.tag ?? '—', resolvers: summary.visibleOn.join(', ') }) }
            : { tone: 'success', text: t('dnssec.parentDsKeyGone', { tag: tag ?? summary.tag ?? '—' }) }
    }

    return (
        <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    onClick={check}
                    disabled={busy || disabled}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs font-medium text-text-primary hover:bg-bg-hover disabled:opacity-50"
                >
                    {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Search className="w-3.5 h-3.5" aria-hidden="true" />}
                    {result ? t('dnssec.checkAgain') : t('dnssec.parentDsCheck')}
                </button>
                <span className="text-xs text-text-muted">{t('dnssec.parentDsCacheHint')}</span>
            </div>
            {error && (
                <p className="text-xs text-danger flex items-start gap-1.5" role="alert">
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                    {error}
                </p>
            )}
            {result?.enabled && (
                <div className="space-y-1" role="status">
                    {verdict && (
                        <p className={`text-xs flex items-start gap-1.5 ${verdict.tone === 'success' ? 'text-success' : 'text-warning'}`}>
                            {verdict.tone === 'success'
                                ? <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                                : <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />}
                            {verdict.text}
                        </p>
                    )}
                    <ul className="text-xs text-text-secondary font-mono space-y-0.5 break-words">
                        {rows.map((r) => (
                            <li key={r.resolver}>
                                {r.kind === 'ds' && t('dnssec.parentDsRow', { resolver: r.resolver, tags: r.tags })}
                                {r.kind === 'none' && t('dnssec.parentDsRowNone', { resolver: r.resolver })}
                                {r.kind === 'error' && t('dnssec.parentDsRowError', { resolver: r.resolver, error: r.error })}
                            </li>
                        ))}
                    </ul>
                    {result.unknown_tags?.length > 0 && (
                        <p className="text-xs text-text-muted">{t('dnssec.parentDsUnknownTags', { tags: result.unknown_tags.join(', ') })}</p>
                    )}
                </div>
            )}
        </div>
    )
}
