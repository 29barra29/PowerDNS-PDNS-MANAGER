import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Key, Trash2, Plus } from 'lucide-react'
import api from '../../../api'
import InfoHint from '../../InfoHint'
import OneTimeSecretModal from '../../OneTimeSecretModal'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'panel-tokens', order: 30 }

// Karte "API-Token (Panel)" – aus SettingsIntegrationsPanel.jsx (2.4.1) übernommen.
// f76: der neue Token erscheint im OneTimeSecretModal (Kopieren wartet auf die Zwischenablage,
// der Klartext bleibt sichtbar, bis der Benutzer bestätigt).
export default function PanelTokensCard() {
    const { t } = useTranslation()
    const [loadErr, setLoadErr] = useState('')
    const [busy, setBusy] = useState(false)
    const [ptName, setPtName] = useState('CLI')
    const [tokens, setTokens] = useState([])
    const [plainTok, setPlainTok] = useState('')

    async function refresh() {
        setLoadErr('')
        try {
            setTokens(await api.getPanelTokens().then((d) => d.tokens || []))
        } catch (e) {
            setLoadErr(e.message)
        }
    }

    useEffect(() => {
        queueMicrotask(() => refresh())
    }, [])

    async function createPT() {
        setBusy(true)
        setLoadErr('')
        try {
            const d = await api.createPanelToken({ name: ptName || 'Token' })
            setPlainTok(d.plaintext_token)
            setPtName('CLI')
            await refresh()
        } catch (e) { setLoadErr(e.message) }
        finally { setBusy(false) }
    }

    async function delPT(id) {
        if (!window.confirm(t('settings.integrations.deleteTokenQ'))) return
        try {
            await api.deletePanelToken(id)
            await refresh()
        } catch (e) { setLoadErr(e.message) }
    }

    const apiPrefix = '/api/v1'
    const originExample = typeof window !== 'undefined' ? `${window.location.origin}` : 'https://dein-server'

    return (
        <div className="glass-card p-6 space-y-4">
            {loadErr && (
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{loadErr}</div>
            )}
            {plainTok && (
                <OneTimeSecretModal
                    title={t('settings.integrations.panelTokens')}
                    secret={plainTok}
                    onDone={() => setPlainTok('')}
                />
            )}
            <h2 className="text-lg font-bold flex items-center gap-2"><Key className="w-5 h-5" />{t('settings.integrations.panelTokens')}</h2>
            <p className="text-sm text-text-muted">{t('settings.integrations.panelTokensHelp')}</p>
            <InfoHint title={t('settings.integrations.apiInfoTitle')}>
                <p>{t('settings.integrations.apiInfoP1', { prefix: apiPrefix })}</p>
                <p>{t('settings.integrations.apiInfoP2')}</p>
                <p>{t('settings.integrations.apiInfoP3')}</p>
                <p className="pt-1 font-mono text-[11px] text-text-primary/90">{t('settings.integrations.apiInfoCodeLabel')}</p>
                <pre className="mt-1 p-2 rounded-lg bg-bg-primary border border-border text-[11px] overflow-x-auto whitespace-pre-wrap break-all">
                    {t('settings.integrations.curlExample', { origin: originExample })}
                </pre>
            </InfoHint>
            <p className="text-xs text-text-muted">{t('settings.integrations.apiDocHint')}</p>
            <div className="flex flex-wrap gap-2 items-end max-w-lg">
                <div className="flex-1 min-w-[8rem]">
                    <label className="block text-xs text-text-muted mb-0.5">{t('settings.integrations.tokenName')}</label>
                    <input value={ptName} onChange={(e) => setPtName(e.target.value)} className="w-full px-3 py-2 text-sm" />
                </div>
                <button type="button" disabled={busy} onClick={createPT} className="px-3 py-2 rounded-lg bg-accent/20 text-sm flex items-center gap-1">
                    <Plus className="w-4 h-4" /> {t('settings.integrations.createToken')}
                </button>
            </div>
            <ul className="text-sm space-y-2">
                {tokens.map((x) => (
                    <li key={x.id} className="flex items-center justify-between gap-2 border border-border/50 rounded-lg px-3 py-2">
                        <span className="font-mono text-xs">{x.name} <span className="text-text-muted">({x.token_prefix})</span></span>
                        <button type="button" onClick={() => delPT(x.id)} className="p-1 text-danger hover:bg-danger/10 rounded"><Trash2 className="w-4 h-4" /></button>
                    </li>
                ))}
            </ul>
        </div>
    )
}
