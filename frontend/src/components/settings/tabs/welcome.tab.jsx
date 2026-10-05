import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, UserPlus, Check, Send } from 'lucide-react'
import api from '../../../api'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'welcome', order: 60, labelKey: 'settings.welcomeMail.tab', icon: UserPlus, adminOnly: true }

// Tab "Willkommens-Mail" – mechanisch aus SettingsPage.jsx 2.4.1 übernommen.
export default function WelcomeTab({ active }) {
    const { t } = useTranslation()
    const { profile, adminInfo, notify } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    // App-Name für die Vorschau (2.4.1: aus dem Profil-Formular, das ihn aus /app-info lädt)
    const [appName, setAppName] = useState('PDNS Manager')
    const [welcomeForm, setWelcomeForm] = useState({
        enabled: false,
        subject: '',
        body: '',
        default_subject: '',
        default_body: '',
        placeholders: ['username', 'display_name', 'email', 'app_name', 'login_url'],
    })
    const [savingWelcome, setSavingWelcome] = useState(false)
    const [welcomeTestEmail, setWelcomeTestEmail] = useState('')
    const [sendingWelcomeTest, setSendingWelcomeTest] = useState(false)


    async function loadWelcome() {
        try {
            const data = await api.getWelcomeEmailSettings()
            setWelcomeForm({
                enabled: !!data.enabled,
                subject: data.subject || '',
                body: data.body || '',
                default_subject: data.default_subject || '',
                default_body: data.default_body || '',
                placeholders: data.placeholders || ['username', 'display_name', 'email', 'app_name', 'login_url'],
            })
        } catch { /* settings not yet present is OK */ }
    }

    async function handleSaveWelcome(e) {
        e.preventDefault()
        setSavingWelcome(true)
        setError('')
        try {
            await api.updateWelcomeEmailSettings({
                enabled: welcomeForm.enabled,
                subject: welcomeForm.subject,
                body: welcomeForm.body,
            })
            setSuccess(t('settings.welcomeMail.saveSuccess'))
            await loadWelcome()
        } catch (err) { setError(err.message) }
        finally { setSavingWelcome(false) }
    }

    async function handleSendWelcomeTest() {
        if (!welcomeTestEmail.trim()) return
        setSendingWelcomeTest(true)
        try {
            const r = await api.sendWelcomeTestEmail({ to_email: welcomeTestEmail })
            if (r.success) setSuccess(r.message)
            else setError(r.error || t('settings.welcomeMail.testFailed'))
        } catch (err) { setError(err.message) }
        finally { setSendingWelcomeTest(false) }
    }

    function welcomePreview() {
        const subject = (welcomeForm.subject || welcomeForm.default_subject || '').trim()
        const body = (welcomeForm.body || welcomeForm.default_body || '').trim()
        const sample = {
            username: profile?.username || 'maxmustermann',
            display_name: profile?.display_name || profile?.username || 'Max Mustermann',
            email: profile?.email || 'max@example.com',
            app_name: appName || 'PDNS Manager',
            login_url: (adminInfo?.app_base_url || 'http://localhost:5380').replace(/\/$/, '') + '/login',
        }
        const replace = (s) => s.replace(/\{(\w+)\}/g, (_m, k) => (k in sample ? sample[k] : `{${k}}`))
        return { subject: replace(subject), body: replace(body) }
    }

    // Wie 2.4.1: bei jedem Öffnen des Tabs neu laden
    useEffect(() => {
        if (!active) return
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren wie 2.4.1
        loadWelcome()
        api.getAppInfo().then((app) => setAppName(app?.app_name || 'PDNS Manager')).catch(() => {})
    }, [active])

    return (
        <div className="space-y-6">
            <div className="glass-card p-6">
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
                        <UserPlus className="w-5 h-5 text-accent-light" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.welcomeMail.title')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.welcomeMail.subtitle')}</p>
                    </div>
                </div>

                <form onSubmit={handleSaveWelcome} className="space-y-5">
                    <label className="flex items-center gap-3 select-none">
                        <input type="checkbox" checked={welcomeForm.enabled}
                            onChange={(e) => setWelcomeForm(f => ({ ...f, enabled: e.target.checked }))}
                            className="w-4 h-4 accent-accent" />
                        <span className="text-sm text-text-primary font-medium">{t('settings.welcomeMail.enabled')}</span>
                    </label>

                    <div className="grid md:grid-cols-2 gap-5">
                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">
                                    {t('settings.welcomeMail.subject')}
                                </label>
                                <input type="text" value={welcomeForm.subject}
                                    onChange={(e) => setWelcomeForm(f => ({ ...f, subject: e.target.value }))}
                                    placeholder={welcomeForm.default_subject}
                                    className="w-full px-3 py-2 text-sm" maxLength={200} />
                                <p className="text-xs text-text-muted mt-1">{t('settings.welcomeMail.subjectHint')}</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">
                                    {t('settings.welcomeMail.body')}
                                </label>
                                <textarea value={welcomeForm.body}
                                    onChange={(e) => setWelcomeForm(f => ({ ...f, body: e.target.value }))}
                                    placeholder={welcomeForm.default_body}
                                    rows={12}
                                    className="w-full px-3 py-2 text-sm font-mono leading-relaxed" maxLength={20000} />
                                <p className="text-xs text-text-muted mt-1">{t('settings.welcomeMail.bodyHint')}</p>
                            </div>

                            <div className="rounded-lg border border-border bg-bg-tertiary p-3">
                                <p className="text-xs font-semibold text-text-secondary mb-2">{t('settings.welcomeMail.placeholdersTitle')}</p>
                                <div className="flex flex-wrap gap-1.5">
                                    {welcomeForm.placeholders.map((p) => (
                                        <code key={p} className="text-[11px] px-2 py-1 rounded bg-bg-hover text-accent-light border border-border">
                                            {`{${p}}`}
                                        </code>
                                    ))}
                                </div>
                            </div>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('settings.welcomeMail.preview')}</label>
                            <div className="rounded-lg border border-border bg-bg-tertiary p-4 h-full min-h-[24rem] overflow-auto">
                                <p className="text-xs uppercase tracking-wide text-text-muted">{t('settings.welcomeMail.previewSubject')}</p>
                                <p className="text-sm font-semibold text-text-primary mb-3">{welcomePreview().subject || <span className="text-text-muted italic">{t('settings.welcomeMail.previewEmpty')}</span>}</p>
                                <p className="text-xs uppercase tracking-wide text-text-muted">{t('settings.welcomeMail.previewBody')}</p>
                                <pre className="text-sm text-text-primary whitespace-pre-wrap font-sans">{welcomePreview().body || <span className="text-text-muted italic">{t('settings.welcomeMail.previewEmpty')}</span>}</pre>
                            </div>
                        </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-3 pt-2">
                        <button type="submit" disabled={savingWelcome}
                            className="px-5 py-2 bg-accent hover:bg-accent-hover text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                            {savingWelcome ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                            {t('common.save')}
                        </button>
                    </div>
                </form>
            </div>

            <div className="glass-card p-6">
                <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                        <Send className="w-5 h-5 text-success" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.welcomeMail.testTitle')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.welcomeMail.testSubtitle')}</p>
                    </div>
                </div>
                <div className="flex items-center gap-3">
                    <input type="email" value={welcomeTestEmail}
                        onChange={e => setWelcomeTestEmail(e.target.value)}
                        placeholder="test@meinedomain.de" className="flex-1 px-3 py-2 text-sm" />
                    <button onClick={handleSendWelcomeTest}
                        disabled={sendingWelcomeTest || !welcomeTestEmail.trim()}
                        className="px-5 py-2 bg-gradient-to-r from-success/80 to-emerald-600 hover:from-success hover:to-emerald-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 shrink-0">
                        {sendingWelcomeTest ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                        {t('common.send')}
                    </button>
                </div>
                <p className="text-xs text-text-muted mt-2">{t('settings.welcomeMail.testHint')}</p>
            </div>
        </div>
    )
}
