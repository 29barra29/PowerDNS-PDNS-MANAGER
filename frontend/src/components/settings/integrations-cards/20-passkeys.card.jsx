import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Trash2, Plus, Fingerprint } from 'lucide-react'
import { startRegistration, browserSupportsWebAuthn } from '@simplewebauthn/browser'
import api from '../../../api'
import InfoHint from '../../InfoHint'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'passkeys', order: 20 }

// Karte "Passkeys" – mechanisch aus SettingsIntegrationsPanel.jsx (2.4.1) übernommen.
export default function PasskeysCard() {
    const { t } = useTranslation()
    const [loadErr, setLoadErr] = useState('')
    const [busy, setBusy] = useState(false)
    const [passkeys, setPasskeys] = useState([])
    const [pkName, setPkName] = useState('')
    const [pkSupported, setPkSupported] = useState(false)

    async function refresh() {
        setLoadErr('')
        // Wie 2.4.1: Fehler beim Laden der Passkeys werden still als leere Liste behandelt
        const list = await api.getPasskeys().then((d) => d.credentials || []).catch(() => [])
        setPasskeys(list)
    }

    useEffect(() => {
        queueMicrotask(() => refresh())
    }, [])

    useEffect(() => {
        queueMicrotask(() => {
            try { setPkSupported(browserSupportsWebAuthn()) } catch { setPkSupported(false) }
        })
    }, [])

    async function addPasskey() {
        setBusy(true)
        setLoadErr('')
        try {
            const { options, challenge_token } = await api.passkeyRegisterBegin()
            const credential = await startRegistration({ optionsJSON: options })
            await api.passkeyRegisterComplete({
                name: (pkName || 'Passkey').trim().slice(0, 100),
                challenge_token,
                credential,
            })
            setPkName('')
            await refresh()
        } catch (e) {
            // Abbruch durch den Nutzer ist kein echter Fehler.
            if (e?.name === 'NotAllowedError' || e?.name === 'AbortError') {
                setLoadErr('')
            } else {
                setLoadErr(e?.message || t('settings.integrations.passkeyFailed'))
            }
        } finally {
            setBusy(false)
        }
    }

    async function delPasskey(id) {
        if (!window.confirm(t('settings.integrations.deletePasskeyQ'))) return
        try {
            await api.deletePasskey(id)
            await refresh()
        } catch (e) { setLoadErr(e.message) }
    }

    return (
        <div className="glass-card p-6 space-y-4">
            {loadErr && (
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{loadErr}</div>
            )}
            <h2 className="text-lg font-bold flex items-center gap-2"><Fingerprint className="w-5 h-5" />{t('settings.integrations.passkeys')}</h2>
            <p className="text-sm text-text-muted">{t('settings.integrations.passkeysHelp')}</p>
            <InfoHint title={t('settings.integrations.passkeyInfoTitle')}>
                <p>{t('settings.integrations.passkeyInfoBody')}</p>
            </InfoHint>

            {!pkSupported ? (
                <p className="text-sm text-amber-300">{t('settings.integrations.passkeyUnsupported')}</p>
            ) : (
                <div className="flex flex-wrap gap-2 items-end max-w-lg">
                    <div className="flex-1 min-w-[10rem]">
                        <label className="block text-xs text-text-muted mb-0.5">{t('settings.integrations.passkeyNameLabel')}</label>
                        <input
                            value={pkName}
                            onChange={(e) => setPkName(e.target.value)}
                            className="w-full px-3 py-2 text-sm"
                            placeholder={t('settings.integrations.passkeyNamePlaceholder')}
                        />
                    </div>
                    <button type="button" disabled={busy} onClick={addPasskey} className="px-3 py-2 rounded-lg bg-accent/20 text-sm flex items-center gap-1">
                        {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} {t('settings.integrations.addPasskey')}
                    </button>
                </div>
            )}

            {passkeys.length === 0 ? (
                <p className="text-sm text-text-muted">{t('settings.integrations.passkeyNone')}</p>
            ) : (
                <ul className="text-sm space-y-2">
                    {passkeys.map((p) => (
                        <li key={p.id} className="flex items-center justify-between gap-2 border border-border/50 rounded-lg px-3 py-2">
                            <span className="min-w-0">
                                <span className="font-medium">{p.name}</span>
                                <span className="block text-xs text-text-muted">
                                    {t('settings.integrations.passkeyCreatedLabel')}: {p.created_at ? new Date(p.created_at).toLocaleDateString() : '—'}
                                    {p.last_used_at ? ` · ${t('settings.integrations.passkeyLastUsedLabel')}: ${new Date(p.last_used_at).toLocaleDateString()}` : ''}
                                </span>
                            </span>
                            <button type="button" onClick={() => delPasskey(p.id)} className="p-1 text-danger hover:bg-danger/10 rounded shrink-0"><Trash2 className="w-4 h-4" /></button>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    )
}
