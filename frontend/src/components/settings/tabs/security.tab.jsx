import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, CheckCircle2, Eye, EyeOff, Check, Shield } from 'lucide-react'
import CaptchaWidget from '../../CaptchaWidget'
import api from '../../../api'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'security', order: 70, labelKey: 'settings.captcha.tab', icon: Shield, adminOnly: true }

// Tab "Sicherheit (Captcha)" – mechanisch aus SettingsPage.jsx 2.4.1 übernommen.
export default function SecurityTab({ active }) {
    const { t } = useTranslation()
    const { notify } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    const [captchaForm, setCaptchaForm] = useState({
        provider: 'none',
        site_key: '',
        secret_key: '',
        secret_key_set: false,
    })
    const [savingCaptcha, setSavingCaptcha] = useState(false)
    const [showCaptchaSecret, setShowCaptchaSecret] = useState(false)
    const [captchaTestToken, setCaptchaTestToken] = useState('')
    const [captchaTestResult, setCaptchaTestResult] = useState(null)
    const captchaTestRef = useRef(null)
    // Damit das Test-Widget nach Provider-/Key-Wechsel neu rendert.
    const [captchaPreview, setCaptchaPreview] = useState({ provider: 'none', site_key: '' })


    async function loadCaptcha() {
        try {
            const data = await api.getCaptchaSettings()
            setCaptchaForm({
                provider: data.provider || 'none',
                site_key: data.site_key || '',
                secret_key: '',
                secret_key_set: !!data.secret_key_set,
            })
            setCaptchaPreview({ provider: data.provider || 'none', site_key: data.site_key || '' })
            setCaptchaTestResult(null)
            setCaptchaTestToken('')
        } catch { /* ignore - settings not yet present is OK */ }
    }

    async function handleSaveCaptcha(e) {
        e.preventDefault()
        setSavingCaptcha(true)
        setError('')
        try {
            const payload = {
                provider: captchaForm.provider,
                site_key: captchaForm.site_key.trim(),
                // Leeres Secret = "nicht aendern" (das maskierte Backend-Feld kommt nicht zurueck als
                // Plaintext, also tauschen wir hier auf "••••••••" um die Konvention beizubehalten).
                secret_key: captchaForm.secret_key.trim() || (captchaForm.secret_key_set ? '••••••••' : ''),
            }
            await api.updateCaptchaSettings(payload)
            setSuccess(t('settings.captcha.saveSuccess'))
            setCaptchaPreview({ provider: payload.provider, site_key: payload.site_key })
            await loadCaptcha()
        } catch (err) { setError(err.message) }
        finally { setSavingCaptcha(false) }
    }

    async function handleTestCaptcha() {
        setCaptchaTestResult(null)
        if (!captchaTestToken) {
            setCaptchaTestResult({ success: false, error: t('settings.captcha.testNoToken') })
            return
        }
        try {
            const r = await api.testCaptcha(captchaTestToken)
            setCaptchaTestResult(r)
        } catch (err) {
            setCaptchaTestResult({ success: false, error: err.message })
        } finally {
            // Token ist nach Verify verbraucht - Widget zuruecksetzen.
            setCaptchaTestToken('')
            captchaTestRef.current?.reset()
        }
    }

    // Wie 2.4.1: bei jedem Öffnen des Tabs neu laden
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren wie 2.4.1
        if (active) loadCaptcha()
    }, [active])

    return (
        <div className="space-y-6">
            <div className="glass-card p-6">
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
                        <Shield className="w-5 h-5 text-accent-light" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.captcha.title')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.captcha.subtitle')}</p>
                    </div>
                </div>

                <form onSubmit={handleSaveCaptcha} className="space-y-5">
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('settings.captcha.provider')}</label>
                        <select value={captchaForm.provider}
                            onChange={(e) => setCaptchaForm(f => ({ ...f, provider: e.target.value }))}
                            className="w-full md:w-1/2 px-3 py-2 text-sm">
                            <option value="none">{t('settings.captcha.providerNone')}</option>
                            <option value="turnstile">{t('settings.captcha.providerTurnstile')}</option>
                            <option value="hcaptcha">{t('settings.captcha.providerHCaptcha')}</option>
                            <option value="recaptcha">{t('settings.captcha.providerRecaptcha')}</option>
                        </select>
                    </div>

                    {captchaForm.provider !== 'none' && (
                        <>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('settings.captcha.siteKey')}</label>
                                <input type="text" value={captchaForm.site_key}
                                    onChange={(e) => setCaptchaForm(f => ({ ...f, site_key: e.target.value }))}
                                    className="w-full px-3 py-2 text-sm font-mono" maxLength={500} />
                                <p className="text-xs text-text-muted mt-1">{t('settings.captcha.siteKeyHint')}</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('settings.captcha.secretKey')}</label>
                                <div className="relative">
                                    <input
                                        type={showCaptchaSecret ? 'text' : 'password'}
                                        value={captchaForm.secret_key}
                                        onChange={(e) => setCaptchaForm(f => ({ ...f, secret_key: e.target.value }))}
                                        placeholder={captchaForm.secret_key_set ? '•••••••• (' + t('settings.captcha.secretKeep') + ')' : t('settings.captcha.secretEnter')}
                                        className="w-full px-3 py-2 pr-10 text-sm font-mono" maxLength={500}
                                        autoComplete="off" />
                                    <button type="button" onClick={() => setShowCaptchaSecret(s => !s)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                        {showCaptchaSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                <p className="text-xs text-text-muted mt-1">{t('settings.captcha.secretKeyHint')}</p>
                            </div>

                            <div className="rounded-lg border border-border bg-bg-tertiary p-3 text-xs text-text-secondary leading-relaxed">
                                {captchaForm.provider === 'turnstile' && (
                                    <>
                                        <p className="font-semibold text-text-primary mb-1">Cloudflare Turnstile</p>
                                        <p>{t('settings.captcha.docsTurnstile')}</p>
                                        <a href="https://dash.cloudflare.com/?to=/:account/turnstile" target="_blank" rel="noreferrer noopener" className="text-accent-light hover:underline">https://dash.cloudflare.com/?to=/:account/turnstile</a>
                                    </>
                                )}
                                {captchaForm.provider === 'hcaptcha' && (
                                    <>
                                        <p className="font-semibold text-text-primary mb-1">hCaptcha</p>
                                        <p>{t('settings.captcha.docsHCaptcha')}</p>
                                        <a href="https://dashboard.hcaptcha.com/sites" target="_blank" rel="noreferrer noopener" className="text-accent-light hover:underline">https://dashboard.hcaptcha.com/sites</a>
                                    </>
                                )}
                                {captchaForm.provider === 'recaptcha' && (
                                    <>
                                        <p className="font-semibold text-text-primary mb-1">Google reCAPTCHA v2</p>
                                        <p>{t('settings.captcha.docsRecaptcha')}</p>
                                        <a href="https://www.google.com/recaptcha/admin" target="_blank" rel="noreferrer noopener" className="text-accent-light hover:underline">https://www.google.com/recaptcha/admin</a>
                                    </>
                                )}
                            </div>
                        </>
                    )}

                    <div className="flex flex-wrap items-center gap-3 pt-2">
                        <button type="submit" disabled={savingCaptcha}
                            className="px-5 py-2 bg-accent hover:bg-accent-hover text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                            {savingCaptcha ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                            {t('common.save')}
                        </button>
                    </div>
                </form>
            </div>

            {captchaPreview.provider !== 'none' && captchaPreview.site_key && (
                <div className="glass-card p-6">
                    <div className="flex items-center gap-3 mb-4">
                        <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                            <CheckCircle2 className="w-5 h-5 text-success" />
                        </div>
                        <div>
                            <h2 className="text-lg font-semibold text-text-primary">{t('settings.captcha.testTitle')}</h2>
                            <p className="text-sm text-text-muted">{t('settings.captcha.testSubtitle')}</p>
                        </div>
                    </div>

                    <div className="space-y-4">
                        <CaptchaWidget
                            ref={captchaTestRef}
                            provider={captchaPreview.provider}
                            siteKey={captchaPreview.site_key}
                            onToken={setCaptchaTestToken}
                            onExpire={() => setCaptchaTestToken('')}
                            onError={() => setCaptchaTestToken('')}
                        />
                        <div className="flex items-center justify-center">
                            <button type="button" onClick={handleTestCaptcha} disabled={!captchaTestToken}
                                className="px-5 py-2 bg-gradient-to-r from-success/80 to-emerald-600 hover:from-success hover:to-emerald-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                <Shield className="w-4 h-4" />
                                {t('settings.captcha.testButton')}
                            </button>
                        </div>
                        {captchaTestResult && (
                            <div className={`p-3 rounded-lg text-sm ${captchaTestResult.success ? 'bg-success/10 border border-success/30 text-success' : 'bg-danger/10 border border-danger/30 text-danger'}`}>
                                {captchaTestResult.success ? captchaTestResult.message : captchaTestResult.error}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}
