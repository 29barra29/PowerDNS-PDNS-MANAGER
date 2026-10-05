import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Shield, Loader2, Copy } from 'lucide-react'
import QRCode from 'qrcode'
import api from '../../../api'
import InfoHint from '../../InfoHint'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const card = { id: 'totp', order: 10 }

// Karte "Zwei-Faktor (TOTP)" – mechanisch aus SettingsIntegrationsPanel.jsx (2.4.1) übernommen.
export default function TotpCard() {
    const { t } = useTranslation()
    const [loadErr, setLoadErr] = useState('')
    const [busy, setBusy] = useState(false)
    const [totp, setTotp] = useState({ totp_enabled: false, totp_pending: false })
    const [totpCode, setTotpCode] = useState('')
    const [totpUri, setTotpUri] = useState('')
    const [totpSecret, setTotpSecret] = useState('')
    const [disPw, setDisPw] = useState('')
    const [disCode, setDisCode] = useState('')
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
            setTotp(await api.getTotpStatus())
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

    return (
        <div className="glass-card p-6 space-y-4">
            {loadErr && (
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{loadErr}</div>
            )}
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
    )
}
