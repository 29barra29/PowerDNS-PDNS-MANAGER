import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Wifi, Eye, EyeOff, Mail, Send } from 'lucide-react'
import api from '../../../api'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'smtp', order: 50, labelKey: 'settings.smtp', icon: Mail, adminOnly: true }

// Tab "E-Mail (SMTP)" – mechanisch aus SettingsPage.jsx 2.4.1 übernommen.
export default function SmtpTab({ active }) {
    const { t } = useTranslation()
    const { notify } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    const [smtpForm, setSmtpForm] = useState({
        host: '', port: 587, username: '', password: '', from_email: '', from_name: 'PDNS Manager', encryption: 'starttls', enabled: false
    })
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
                password: data.password || '', from_email: data.from_email || '',
                from_name: data.from_name || 'PDNS Manager', encryption: data.encryption || 'starttls',
                enabled: data.enabled || false,
            })
        } catch { /* ignore */ }
        finally { setLoadingSmtp(false) }
    }

    async function handleSaveSmtp(e) {
        e.preventDefault()
        setSavingSmtp(true)
        setError('')
        try {
            await api.updateSmtpSettings(smtpForm)
            setSuccess(t('settings.smtpSaveSuccess'))
        } catch (err) { setError(err.message) }
        finally { setSavingSmtp(false) }
    }

    async function handleTestSmtp() {
        setTestingSmtp(true)
        setSmtpTestResult(null)
        try {
            const result = await api.testSmtpConnection()
            setSmtpTestResult(result)
        } catch (err) { setSmtpTestResult({ success: false, error: err.message }) }
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
        if (active) loadSmtp()
    }, [active])

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
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('login.password')}</label>
                                <div className="relative">
                                    <input type={showSmtpPassword ? 'text' : 'password'} value={smtpForm.password}
                                        onChange={e => setSmtpForm({ ...smtpForm, password: e.target.value })}
                                        placeholder="••••••••" className="w-full px-3 py-2 pr-10 text-sm" />
                                    <button type="button" onClick={() => setShowSmtpPassword(!showSmtpPassword)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                        {showSmtpPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
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
                        placeholder="test@meinedomain.de" className="flex-1 px-3 py-2 text-sm" />
                    <button onClick={handleSendTestEmail} disabled={sendingTest || !testEmailAddr.trim()}
                        className="px-5 py-2 bg-gradient-to-r from-success/80 to-emerald-600 hover:from-success hover:to-emerald-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 shrink-0">
                        {sendingTest ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                        Senden
                    </button>
                </div>
            </div>
        </div>
    )
}
