import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Webhook, Trash2, Plus } from 'lucide-react'
import api from '../../../api'
import InfoHint from '../../InfoHint'
import OneTimeSecretModal from '../../OneTimeSecretModal'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'webhooks', order: 40 }

// Karte "Webhooks" – aus SettingsIntegrationsPanel.jsx (2.4.1) übernommen.
// f76: das Secret eines neuen Webhooks erscheint im OneTimeSecretModal (ohne "Secret:"-Präfix).
export default function WebhooksCard() {
    const { t } = useTranslation()
    const [loadErr, setLoadErr] = useState('')
    const [busy, setBusy] = useState(false)
    const [hooks, setHooks] = useState([])
    const [wh, setWh] = useState({ name: '', url: '', events: '*' })
    const [plainHookSecret, setPlainHookSecret] = useState('')

    async function refresh() {
        setLoadErr('')
        try {
            setHooks(await api.getWebhooks().then((d) => d.webhooks || []))
        } catch (e) {
            setLoadErr(e.message)
        }
    }

    useEffect(() => {
        queueMicrotask(() => refresh())
    }, [])

    async function createHook() {
        setBusy(true)
        setLoadErr('')
        const ev = wh.events.split(',').map((s) => s.trim()).filter(Boolean)
        try {
            const d = await api.createWebhook({
                name: wh.name,
                url: wh.url,
                events: ev.length ? ev : ['*'],
            })
            setPlainHookSecret((d.webhook && d.secret) ? d.secret : '')
            setWh({ name: '', url: '', events: '*' })
            await refresh()
        } catch (e) { setLoadErr(e.message) }
        finally { setBusy(false) }
    }

    async function delHook(id) {
        if (!window.confirm(t('settings.integrations.deleteHookQ'))) return
        try {
            await api.deleteWebhook(id)
            await refresh()
        } catch (e) { setLoadErr(e.message) }
    }

    return (
        <div className="glass-card p-6 space-y-4">
            {loadErr && (
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{loadErr}</div>
            )}
            {plainHookSecret && (
                <OneTimeSecretModal
                    title={t('settings.integrations.webhookSecretTitle')}
                    secret={plainHookSecret}
                    onDone={() => setPlainHookSecret('')}
                />
            )}
            <h2 className="text-lg font-bold flex items-center gap-2"><Webhook className="w-5 h-5" />{t('settings.integrations.webhooks')}</h2>
            <p className="text-sm text-text-muted">{t('settings.integrations.webhooksHelp')}</p>
            <InfoHint title={t('settings.integrations.webhookInfoTitle')}>
                <p>{t('settings.integrations.webhookInfoBody')}</p>
            </InfoHint>
            <div className="grid gap-3 max-w-2xl">
                <div>
                    <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-xs font-medium text-text-secondary">{t('settings.integrations.webhookFieldName')}</span>
                    </div>
                    <p className="text-[11px] text-text-muted mb-1 flex items-start gap-1.5">
                        <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-accent/50 text-[9px] font-bold text-accent">i</span>
                        {t('settings.integrations.webhookNameHint')}
                    </p>
                    <input value={wh.name} onChange={(e) => setWh((w) => ({ ...w, name: e.target.value }))} className="w-full px-3 py-2 text-sm" />
                </div>
                <div>
                    <div className="text-xs font-medium text-text-secondary mb-0.5">{t('settings.integrations.webhookFieldUrl')}</div>
                    <p className="text-[11px] text-text-muted mb-1 flex items-start gap-1.5">
                        <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-accent/50 text-[9px] font-bold text-accent">i</span>
                        {t('settings.integrations.webhookUrlHint')}
                    </p>
                    <input value={wh.url} onChange={(e) => setWh((w) => ({ ...w, url: e.target.value }))} className="w-full px-3 py-2 text-sm" placeholder="https://…" />
                </div>
                <div>
                    <div className="text-xs font-medium text-text-secondary mb-0.5">{t('settings.integrations.webhookFieldEvents')}</div>
                    <p className="text-[11px] text-text-muted mb-1 flex items-start gap-1.5">
                        <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-accent/50 text-[9px] font-bold text-accent">i</span>
                        {t('settings.integrations.webhookEventsHint')}
                    </p>
                    <input value={wh.events} onChange={(e) => setWh((w) => ({ ...w, events: e.target.value }))} className="w-full px-3 py-2 text-sm" placeholder="*" />
                </div>
                <button type="button" disabled={busy} onClick={createHook} className="self-start px-3 py-2 rounded-lg bg-accent/20 text-sm flex items-center gap-1">
                    <Plus className="w-4 h-4" /> {t('settings.integrations.addWebhook')}
                </button>
            </div>
            <ul className="text-sm space-y-2">
                {hooks.map((h) => (
                    <li key={h.id} className="flex items-center justify-between gap-2 border border-border/50 rounded-lg px-3 py-2">
                        <span className="truncate">{h.name} — <span className="text-text-muted text-xs">{h.url}</span></span>
                        <button type="button" onClick={() => delHook(h.id)} className="p-1 text-danger"><Trash2 className="w-4 h-4" /></button>
                    </li>
                ))}
            </ul>
        </div>
    )
}
