import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Key, Webhook, Shield, Loader2, Trash2, Plus, Copy } from 'lucide-react'
import QRCode from 'qrcode'
import api from '../api'
import InfoHint from './InfoHint'

export default function SettingsIntegrationsPanel() {
    const { t } = useTranslation()
    const [loadErr, setLoadErr] = useState('')
    const [busy, setBusy] = useState(false)
    const [totp, setTotp] = useState({ totp_enabled: false, totp_pending: false })
    const [totpCode, setTotpCode] = useState('')
    const [totpUri, setTotpUri] = useState('')
    const [totpSecret, setTotpSecret] = useState('')
    const [disPw, setDisPw] = useState('')
    const [disCode, setDisCode] = useState('')
    const [ptName, setPtName] = useState('CLI')
    const [tokens, setTokens] = useState([])
    const [hooks, setHooks] = useState([])
    const [wh, setWh] = useState({ name: '', url: '', events: '*' })
    const [plainTok, setPlainTok] = useState('')
    const [plainHookSecret, setPlainHookSecret] = useState('')
    /** QR als PNG-Data-URL (qrcode-Paket – kein react-qr-code/SVG, zuverlässiger im Build) */
    const [totpQrDataUrl, setTotpQrDataUrl] = useState('')

    useEffect(() => {
        if (!totpUri || totpUri.length < 8) {
            queueMicrotask(() => setTotpQrDataUrl(''))
            return
        }
        let cancelled = false
        QRCode.toDataURL(totpUri, {
            width: 256,
            margin: 2,
            errorCorrectionLevel: 'H',
            color: { dark: '#000000', light: '#ffffff' },
        })
            .then((url) => {
                if (!cancelled) setTotpQrDataUrl(url)
            })
            .catch(() => {
                if (!cancelled) setTotpQrDataUrl('')
            })
        return () => { cancelled = true }
    }, [totpUri])

    async function refresh() {
        setLoadErr('')
        try {
            const [s, toks, w] = await Promise.all([
                api.getTotpStatus(),
                api.getPanelTokens().then((d) => d.tokens || []),
                api.getWebhooks().then((d) => d.webhooks || []),
            ])
            setTotp(s)
            setTokens(toks)
            setHooks(w)
        } catch (e) {
            setLoadErr(e.message)
        }
    }

    useEffect(() => {
        queueMicrotask(() => refresh())
    }, [])

    async function beginTotp() {
        setBusy(true)
        setLoadErr('')
        try {
            const d = await api.totpBegin()
            setTotpUri(d.provisioning_uri || '')
            setTotpSecret(d.secret || '')
        } catch (e) { setLoadErr(e.message) }
        finally { setBusy(false) }
    }

    function copySecret() {
        if (totpSecret) navigator.clipboard.writeText(totpSecret).catch(() => {})
    }

    async function enableTotp() {
        setBusy(true)
        setLoadErr('')
        try {
            await api.totpEnable(totpCode)
            setTotpCode('')
            setTotpUri('')
            setTotpSecret('')
            setTotpQrDataUrl('')
            await refresh()
        } catch (e) { setLoadErr(e.message) }
        finally { setBusy(false) }
    }

    async function disableTotp() {
        setBusy(true)
        setLoadErr('')
        try {
            await api.totpDisable(disPw, disCode)
            setDisPw('')
            setDisCode('')
            await refresh()
        } catch (e) { setLoadErr(e.message) }
        finally { setBusy(false) }
    }

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
            setPlainHookSecret((d.webhook && d.secret) ? `Secret: ${d.secret}\n` : '')
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

    const apiPrefix = '/api/v1'
    const originExample = typeof window !== 'undefined' ? `${window.location.origin}` : 'https://dein-server'

    return (
        <div className="space-y-6">
            {loadErr && (
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{loadErr}</div>
            )}
            {plainTok && (
                <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-sm space-y-2">
                    <p className="font-medium text-amber-200">{t('settings.integrations.newPanelToken')}</p>
                    <code className="block break-all text-xs bg-bg-primary/80 p-2 rounded">{plainTok}</code>
                    <button type="button" onClick={() => { navigator.clipboard.writeText(plainTok); setPlainTok('') }} className="text-xs text-accent flex items-center gap-1">
                        <Copy className="w-3.5 h-3.5" /> {t('common.copy')}
                    </button>
                </div>
            )}
            {plainHookSecret && (
                <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-sm whitespace-pre-wrap text-xs">
                    {plainHookSecret}
                    <button type="button" className="block mt-2 text-accent text-xs" onClick={() => setPlainHookSecret('')}>OK</button>
                </div>
            )}

            <div className="glass-card p-6 space-y-4">
                <h2 className="text-lg font-bold flex items-center gap-2"><Shield className="w-5 h-5" />{t('settings.integrations.totp')}</h2>
                <p className="text-sm text-text-muted">{t('settings.integrations.totpHelp')}</p>
                <p className="text-sm">{t('settings.integrations.status')}: {totp.totp_enabled ? t('settings.integrations.on') : t('settings.integrations.off')}</p>
                {!totp.totp_enabled ? (
                    <div className="space-y-4 max-w-lg">
                        {!totpUri ? (
                            <button type="button" disabled={busy} onClick={beginTotp} className="px-3 py-2 rounded-lg bg-accent/20 text-sm">
                                {busy ? <Loader2 className="w-4 h-4 inline animate-spin" /> : null} {t('settings.integrations.totpBegin')}
                            </button>
                        ) : (
                            <div className="space-y-4">
                                <InfoHint title={t('settings.integrations.totpQrTitle')}>
                                    <p>{t('settings.integrations.totpQrHelp')}</p>
                                </InfoHint>
                                {totpUri && totpUri.length > 0 && (
                                    <div className="flex flex-col items-center sm:items-start gap-2">
                                        <div className="rounded-2xl bg-white p-3 shadow-lg ring-1 ring-border/20 inline-block">
                                            {totpQrDataUrl ? (
                                                <img
                                                    src={totpQrDataUrl}
                                                    width={256}
                                                    height={256}
                                                    className="block h-64 w-64 max-w-full"
                                                    alt="TOTP QR"
                                                />
                                            ) : (
                                                <div className="h-64 w-64 flex items-center justify-center text-xs text-text-muted">
                                                    <Loader2 className="w-8 h-8 animate-spin text-accent" />
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                )}
                                {totpSecret && (
                                    <div className="space-y-1">
                                        <p className="text-xs font-medium text-text-secondary">{t('settings.integrations.totpSecretLabel')}</p>
                                        <div className="flex flex-wrap items-center gap-2">
                                            <code className="text-xs font-mono break-all bg-bg-primary/80 px-2 py-1.5 rounded border border-border flex-1 min-w-0">
                                                {totpSecret}
                                            </code>
                                            <button
                                                type="button"
                                                onClick={copySecret}
                                                className="shrink-0 p-2 rounded-lg border border-border hover:bg-bg-hover text-text-secondary"
                                                title={t('common.copy')}
                                            >
                                                <Copy className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>
                                )}
                                <div>
                                    <label className="block text-xs text-text-muted mb-1">TOTP-Code (6+ Ziffern)</label>
                                    <input
                                        value={totpCode}
                                        onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                                        className="w-full max-w-xs px-3 py-2 text-sm"
                                        placeholder="123456"
                                    />
                                </div>
                                <button type="button" disabled={busy || totpCode.length < 6} onClick={enableTotp} className="px-3 py-2 rounded-lg bg-gradient-to-r from-accent to-purple-600 text-white text-sm">
                                    {t('settings.integrations.totpEnable')}
                                </button>
                            </div>
                        )}
                    </div>
                ) : (
                    <div className="space-y-2 max-w-md">
                        <input type="password" value={disPw} onChange={(e) => setDisPw(e.target.value)} className="w-full px-3 py-2 text-sm" placeholder={t('login.password')} />
                        <input value={disCode} onChange={(e) => setDisCode(e.target.value.replace(/\D/g, '').slice(0, 8))} className="w-full px-3 py-2 text-sm" placeholder="TOTP" />
                        <button type="button" disabled={busy} onClick={disableTotp} className="px-3 py-2 rounded-lg border border-danger/40 text-danger text-sm">
                            {t('settings.integrations.totpDisable')}
                        </button>
                    </div>
                )}
            </div>

            <div className="glass-card p-6 space-y-4">
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

            <div className="glass-card p-6 space-y-4">
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
        </div>
    )
}
