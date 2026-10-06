import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Wifi, Eye, EyeOff, Mail, Send, AlertTriangle } from 'lucide-react'
import api from '../../../api'
import { smtpPasswordValue } from '../../../lib/settingsForms.js'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'smtp', order: 50, labelKey: 'settings.smtp', icon: Mail, adminOnly: true }

// Tab "E-Mail (SMTP)".
// F8-D05 (N20): das gespeicherte Passwort wird nie ins Formular geladen. Leeres Feld = behalten (password: null),
// neuer Wert = ersetzen, Haken "entfernen" = loeschen (password: ''). Texte uebersetzt (A11).
// F5 (WS-F5-FE): Warnung bei nicht entschluesselbarem Passwort (`password_unreadable`); Aendern von Server, Port,
// Benutzer oder Verschluesselung verlangt das Passwort neu (Backend 400 `secret_reentry_required` [S3]) – der
// Hinweis erscheint am Passwortfeld. Der Verbindungstest prueft die Werte im Formular (auch ungespeicherte).
const SMTP_TARGET_FIELDS = ['host', 'port', 'username', 'encryption']
export default function SmtpTab({ active }) {
    const { t } = useTranslation()
    const { notify, isAdmin } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    const [smtpForm, setSmtpForm] = useState({
        host: '', port: 587, username: '', password: '', from_email: '', from_name: 'PDNS Manager', encryption: 'starttls', enabled: false
    })
    // Ist serverseitig ein Passwort gespeichert? / Soll es beim Speichern entfernt werden?
    const [smtpPasswordSet, setSmtpPasswordSet] = useState(false)
    const [smtpClearPassword, setSmtpClearPassword] = useState(false)
    const [smtpPasswordUnreadable, setSmtpPasswordUnreadable] = useState(false)
    // Zuletzt geladene Zielfelder (fuer den Test: nur abweichende Werte mitschicken)
    const [smtpLoadedTargets, setSmtpLoadedTargets] = useState(null)
    // Backend verlangt das Passwort neu (Zielfeld geaendert)
    const [smtpRetarget, setSmtpRetarget] = useState(false)
    const [loadingSmtp, setLoadingSmtp] = useState(false)
    const [savingSmtp, setSavingSmtp] = useState(false)
    const [testingSmtp, setTestingSmtp] = useState(false)
    const [smtpTestResult, setSmtpTestResult] = useState(null)
    const [showSmtpPassword, setShowSmtpPassword] = useState(false)
    const [testEmailAddr, setTestEmailAddr] = useState('')
    const [sendingTest, setSendingTest] = useState(false)


    async function loadSmtp() {
        setLoadingSmtp(true)
        try {
            const data = await api.getSmtpSettings()
            setSmtpForm({
                host: data.host || '', port: data.port || 587, username: data.username || '',
                password: '', from_email: data.from_email || '',
                from_name: data.from_name || 'PDNS Manager', encryption: data.encryption || 'starttls',
                enabled: data.enabled === true || data.enabled === 'true',
            })
            setSmtpPasswordSet(!!data.password_set)
            setSmtpPasswordUnreadable(data.password_unreadable === true)
            setSmtpClearPassword(false)
            setSmtpRetarget(false)
            setSmtpLoadedTargets({
                host: data.host || '', port: data.port || 587, username: data.username || '',
                encryption: data.encryption || 'starttls',
            })
        } catch (err) {
            setError(err.message)
        } finally { setLoadingSmtp(false) }
    }

    async function handleSaveSmtp(e) {
        e.preventDefault()
        setSavingSmtp(true)
        setError('')
        try {
            await api.updateSmtpSettings({
                ...smtpForm,
                password: smtpPasswordValue(smtpForm.password, smtpClearPassword),
            })
            setSuccess(t('settings.smtpSaveSuccess'))
            // Neu laden: zeigt, ob jetzt ein Passwort gespeichert ist, und leert das Feld
            await loadSmtp()
        } catch (err) {
            if (err?.code === 'secret_reentry_required') setSmtpRetarget(true)
            setError(err.message)
        }
        finally { setSavingSmtp(false) }
    }

    // Body fuer den Verbindungstest: nur Zielfelder, die vom gespeicherten Stand abweichen, und ein neu
    // eingegebenes Passwort. null = ohne Body testen (gespeicherte Werte, Verhalten wie 2.4.1).
    function buildSmtpTestBody() {
        const body = {}
        for (const key of SMTP_TARGET_FIELDS) {
            if (!smtpLoadedTargets || smtpForm[key] !== smtpLoadedTargets[key]) body[key] = smtpForm[key]
        }
        if (smtpForm.password) body.password = smtpForm.password
        else if (smtpClearPassword) body.password = ''
        return Object.keys(body).length > 0 ? body : null
    }

    async function handleTestSmtp() {
        setTestingSmtp(true)
        setSmtpTestResult(null)
        try {
            const body = buildSmtpTestBody()
            const result = body ? await api.testSmtpSettings(body) : await api.testSmtpConnection()
            setSmtpTestResult(result)
        } catch (err) {
            if (err?.code === 'secret_reentry_required') setSmtpRetarget(true)
            setSmtpTestResult({ success: false, error: err.message })
        }
        finally { setTestingSmtp(false) }
    }

    async function handleSendTestEmail() {
        if (!testEmailAddr.trim()) return
        setSendingTest(true)
        try {
            const result = await api.sendTestEmail({ to_email: testEmailAddr })
            if (result.success) setSuccess(result.message)
            else setError(result.error)
        } catch (err) { setError(err.message) }
        finally { setSendingTest(false) }
    }

    // Wie 2.4.1: bei jedem Öffnen des Tabs neu laden
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren wie 2.4.1
        if (active && isAdmin) loadSmtp()
    }, [active]) // eslint-disable-line react-hooks/exhaustive-deps -- nur beim Aktivieren laden (wie 2.4.1)

    // Zusaetzliches Render-Gate (F8 6.7): der Tab ist adminOnly
    if (!isAdmin) return null

    return (
        <div className="space-y-6">
            <div className="glass-card p-6">
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
                        <Mail className="w-5 h-5 text-accent-light" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.smtpTitle')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.smtpSubtitle')}</p>
                    </div>
                </div>

                {loadingSmtp ? (
                    <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
                ) : (
                    <form onSubmit={handleSaveSmtp} className="space-y-4">
                        {/* Aktiviert */}
                        <label className="flex items-center gap-3 cursor-pointer p-3 rounded-lg border border-border hover:bg-bg-hover transition-colors">
                            <input type="checkbox" checked={smtpForm.enabled} onChange={e => setSmtpForm({ ...smtpForm, enabled: e.target.checked })} className="w-4 h-4 rounded" />
                            <div>
                                <span className="text-sm font-medium text-text-primary">{t('settings.smtpEnable')}</span>
                                <p className="text-xs text-text-muted">{t('settings.smtpOnlyWhenEnabled')}</p>
                            </div>
                        </label>

                        {/* Server & Port */}
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div className="md:col-span-2">
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.smtpServer')}</label>
                                <input type="text" value={smtpForm.host} onChange={e => setSmtpForm({ ...smtpForm, host: e.target.value })}
                                    placeholder="smtp.gmail.com" className="w-full px-3 py-2 text-sm" />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.port')}</label>
                                <input type="number" value={smtpForm.port} onChange={e => setSmtpForm({ ...smtpForm, port: parseInt(e.target.value) || 587 })}
                                    placeholder="587" className="w-full px-3 py-2 text-sm" />
                            </div>
                        </div>

                        {/* Verschlüsselung */}
                        <div>
                            <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.encryption')}</label>
                            <select value={smtpForm.encryption} onChange={e => setSmtpForm({ ...smtpForm, encryption: e.target.value })} className="w-full px-3 py-2 text-sm">
                                <option value="starttls">{t('settings.encryptionStarttls')}</option>
                                <option value="ssl">{t('settings.encryptionSsl')}</option>
                                <option value="none">{t('settings.encryptionNone')}</option>
                            </select>
                        </div>

                        {/* Zugangsdaten */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.username')}</label>
                                <input type="text" value={smtpForm.username} onChange={e => setSmtpForm({ ...smtpForm, username: e.target.value })}
                                    placeholder="user@example.com" className="w-full px-3 py-2 text-sm" />
                            </div>
                            <div>
                                <label htmlFor="smtp-password" className="block text-sm font-medium text-text-secondary mb-1">{t('login.password')}</label>
                                {smtpPasswordUnreadable && (
                                    <div role="alert" className="mb-2 p-2.5 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs flex items-start gap-2">
                                        <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                                        <span>{t('settings.smtpPasswordUnreadable')}</span>
                                    </div>
                                )}
                                <div className="relative">
                                    <input id="smtp-password" type={showSmtpPassword ? 'text' : 'password'} value={smtpForm.password}
                                        onChange={e => { setSmtpForm({ ...smtpForm, password: e.target.value }); if (e.target.value) setSmtpRetarget(false) }}
                                        aria-invalid={smtpRetarget || undefined}
                                        aria-describedby={smtpRetarget ? 'smtp-password-retarget' : undefined}
                                        placeholder={smtpPasswordSet ? t('settings.smtpPasswordKeepPlaceholder') : ''}
                                        disabled={smtpClearPassword}
                                        autoComplete="new-password"
                                        className="w-full px-3 py-2 pr-10 text-sm disabled:opacity-50" />
                                    <button type="button" onClick={() => setShowSmtpPassword(!showSmtpPassword)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary"
                                        aria-label={t('login.password')}>
                                        {showSmtpPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                {smtpPasswordSet && (
                                    <label className="mt-2 flex items-center gap-2 text-xs text-text-secondary cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={smtpClearPassword}
                                            onChange={e => {
                                                setSmtpClearPassword(e.target.checked)
                                                if (e.target.checked) setSmtpForm(f => ({ ...f, password: '' }))
                                            }}
                                            className="w-3.5 h-3.5 rounded"
                                        />
                                        {t('settings.smtpPasswordClear')}
                                    </label>
                                )}
                                {smtpClearPassword && (
                                    <p className="mt-1 text-xs text-warning">{t('settings.smtpPasswordWillBeCleared')}</p>
                                )}
                                {smtpRetarget && !smtpForm.password && (
                                    <p id="smtp-password-retarget" className="mt-1 text-xs text-warning">{t('settings.smtpRetargetHint')}</p>
                                )}
                            </div>
                        </div>

                        {/* Absender */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.senderEmail')}</label>
                                <input type="email" value={smtpForm.from_email} onChange={e => setSmtpForm({ ...smtpForm, from_email: e.target.value })}
                                    placeholder={t('settings.smtpFromEmailPlaceholder')} className="w-full px-3 py-2 text-sm" />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.senderName')}</label>
                                <input type="text" value={smtpForm.from_name} onChange={e => setSmtpForm({ ...smtpForm, from_name: e.target.value })}
                                    placeholder={t('settings.smtpFromNamePlaceholder')} className="w-full px-3 py-2 text-sm" />
                            </div>
                        </div>

                        {/* Buttons */}
                        <div className="flex items-center justify-between pt-2 border-t border-border">
                            <div className="flex items-center gap-2">
                                <button type="button" onClick={handleTestSmtp} disabled={testingSmtp || !smtpForm.host}
                                    className="px-4 py-2 text-sm font-medium border border-border rounded-lg text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-50 flex items-center gap-2">
                                    {testingSmtp ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wifi className="w-4 h-4" />}
                                    {t('settings.testConnection')}
                                </button>
                                {smtpTestResult && (
                                    <span className={`text-xs ${smtpTestResult.success ? 'text-success' : 'text-danger'}`}>
                                        {smtpTestResult.success ? `✅ ${smtpTestResult.message}` : `❌ ${smtpTestResult.error}`}
                                    </span>
                                )}
                            </div>
                            <button type="submit" disabled={savingSmtp}
                                className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 transition-all">
                                {savingSmtp && <Loader2 className="w-4 h-4 animate-spin" />}
                                {t('common.save')}
                            </button>
                        </div>
                    </form>
                )}
            </div>

            {/* Test-E-Mail senden */}
            <div className="glass-card p-6">
                <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                        <Send className="w-5 h-5 text-success" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.testEmailTitle')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.testEmailSubtitle')}</p>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <input type="email" value={testEmailAddr} onChange={e => setTestEmailAddr(e.target.value)}
                        placeholder="test@example.com" className="flex-1 px-3 py-2 text-sm" />
                    <button onClick={handleSendTestEmail} disabled={sendingTest || !testEmailAddr.trim()}
                        className="px-5 py-2 bg-gradient-to-r from-success/80 to-emerald-600 hover:from-success hover:to-emerald-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 shrink-0">
                        {sendingTest ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                        {t('common.send')}
                    </button>
                </div>
            </div>
        </div>
    )
}
