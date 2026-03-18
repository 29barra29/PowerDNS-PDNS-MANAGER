import { useState, useEffect } from 'react'
import { Settings, Server, Database, Plus, Trash2, Pencil, Loader2, AlertCircle, CheckCircle2, RefreshCw, Wifi, WifiOff, Eye, EyeOff, X, Zap, UserCog, Lock, Mail, User, Download, Github, Code, Sliders, Copy, Star, Send } from 'lucide-react'
import api from '../api'

export default function SettingsPage() {
    const [activeTab, setActiveTab] = useState('profile')
    const [servers, setServers] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')

    // Profile
    const [profile, setProfile] = useState(null)
    const [profileForm, setProfileForm] = useState({ username: '', display_name: '', email: '' })
    const [savingProfile, setSavingProfile] = useState(false)

    // Password change
    const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '', confirm_password: '' })
    const [savingPassword, setSavingPassword] = useState(false)
    const [showCurrentPw, setShowCurrentPw] = useState(false)
    const [showNewPw, setShowNewPw] = useState(false)

    // Updates
    const [commits, setCommits] = useState([])
    const [loadingCommits, setLoadingCommits] = useState(false)
    const [commitError, setCommitError] = useState('')

    // Über-Tab: Version kommt aus API (eine zentrale Quelle: VERSION-Datei)
    const [appInfo, setAppInfo] = useState(null)

    // Add/Edit Server
    const [showForm, setShowForm] = useState(false)
    const [editId, setEditId] = useState(null)
    const [form, setForm] = useState({ name: '', display_name: '', url: '', api_key: '', description: '' })
    const [saving, setSaving] = useState(false)
    const [showApiKey, setShowApiKey] = useState(false)

    // Test connection
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState(null)

    // Templates
    const [templates, setTemplates] = useState([])
    const [loadingTemplates, setLoadingTemplates] = useState(false)
    const [showTemplateForm, setShowTemplateForm] = useState(false)
    const [editTemplateId, setEditTemplateId] = useState(null)
    const emptyTemplate = {
        name: '', description: '', nameservers: '',
        kind: 'Native', soa_edit_api: 'DEFAULT', default_ttl: 3600,
        records: [], is_default: false,
    }
    const [templateForm, setTemplateForm] = useState({ ...emptyTemplate })
    const [savingTemplate, setSavingTemplate] = useState(false)
    const emptyRecord = { name: '@', type: 'A', content: '', ttl: 3600, prio: null }
    const [newRecord, setNewRecord] = useState({ ...emptyRecord })

    useEffect(() => {
        loadProfile()
        loadServers()
        loadTemplates()
    }, [])

    useEffect(() => {
        if (activeTab === 'about') api.getAppInfo().then(setAppInfo).catch(() => setAppInfo(null))
    }, [activeTab])

    useEffect(() => {
        if (activeTab === 'updates' && commits.length === 0) {
            loadCommits()
        }
        if (activeTab === 'smtp') {
            loadSmtp()
        }
    }, [activeTab])

    async function loadCommits() {
        setLoadingCommits(true)
        setCommitError('')
        try {
            const res = await fetch('https://api.github.com/repos/29barra29/dns-manager/commits?per_page=5')
            if (!res.ok) throw new Error('Repository ist privat (Änderungen können nicht abgerufen werden)')
            const data = await res.json()
            setCommits(data)
        } catch (err) {
            setCommitError(err.message)
        } finally {
            setLoadingCommits(false)
        }
    }

    async function loadProfile() {
        try {
            const data = await api.getMe()
            const app = await api.getAppInfo().catch(() => ({ app_name: 'DNS Manager' }))
            setProfile(data)
            setProfileForm({
                username: data.username || '',
                display_name: data.display_name || '',
                email: data.email || '',
                app_name: app.app_name || 'DNS Manager'
            })
        } catch (err) {
            setError(err.message)
        }
    }

    async function loadServers() {
        setLoading(true)
        try {
            const data = await api.getServerConfigs()
            setServers(data.servers || [])
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    // ===== Profile functions =====
    async function handleSaveProfile(e) {
        e.preventDefault()
        setSavingProfile(true)
        setError('')
        setSuccess('')
        try {
            const result = await api.updateProfile({
                username: profileForm.username,
                display_name: profileForm.display_name,
                email: profileForm.email,
            })
            if (profile?.role === 'admin' && profileForm.app_name) {
                await api.updateAppInfo({ app_name: profileForm.app_name })
            }
            // Trigger a minor refresh on the layout without full reload, by delaying localstorage
            setSuccess('Profil und Einstellungen erfolgreich aktualisiert! (Lädt neu...)')
            
            if (result.user) {
                localStorage.setItem('user', JSON.stringify(result.user))
                setProfile(result.user)
            }
            setTimeout(() => window.location.reload(), 1500)
        } catch (err) {
            setError(err.message)
        } finally {
            setSavingProfile(false)
        }
    }

    async function handleChangePassword(e) {
        e.preventDefault()
        if (passwordForm.new_password !== passwordForm.confirm_password) {
            setError('Die neuen Passwörter stimmen nicht überein!')
            return
        }
        if (passwordForm.new_password.length < 4) {
            setError('Das neue Passwort muss mindestens 4 Zeichen lang sein!')
            return
        }
        setSavingPassword(true)
        setError('')
        setSuccess('')
        try {
            await api.changePassword({
                current_password: passwordForm.current_password,
                new_password: passwordForm.new_password,
            })
            setSuccess('Passwort erfolgreich geändert!')
            setPasswordForm({ current_password: '', new_password: '', confirm_password: '' })
            setShowCurrentPw(false)
            setShowNewPw(false)
        } catch (err) {
            setError(err.message)
        } finally {
            setSavingPassword(false)
        }
    }

    function handleSaveDefaults(e) {
        // kept for backwards compat, no-op now
    }

    async function loadTemplates() {
        setLoadingTemplates(true)
        try {
            const data = await api.getTemplates()
            setTemplates(data.templates || [])
        } catch (err) { /* ignore */ }
        finally { setLoadingTemplates(false) }
    }

    function openTemplateAdd() {
        setEditTemplateId(null)
        setTemplateForm({ ...emptyTemplate })
        setNewRecord({ ...emptyRecord })
        setShowTemplateForm(true)
    }

    function openTemplateEdit(t) {
        setEditTemplateId(t.id)
        setTemplateForm({
            name: t.name,
            description: t.description || '',
            nameservers: (t.nameservers || []).join(', '),
            kind: t.kind || 'Native',
            soa_edit_api: t.soa_edit_api || 'DEFAULT',
            default_ttl: t.default_ttl || 3600,
            records: t.records || [],
            is_default: t.is_default || false,
        })
        setNewRecord({ ...emptyRecord })
        setShowTemplateForm(true)
    }

    function addRecordToTemplate() {
        if (!newRecord.content.trim()) return
        const rec = { ...newRecord }
        if (rec.type !== 'MX' && rec.type !== 'SRV') rec.prio = null
        setTemplateForm({
            ...templateForm,
            records: [...templateForm.records, rec],
        })
        setNewRecord({ ...emptyRecord })
    }

    function removeRecordFromTemplate(idx) {
        setTemplateForm({
            ...templateForm,
            records: templateForm.records.filter((_, i) => i !== idx),
        })
    }

    async function handleSaveTemplate(e) {
        e.preventDefault()
        setSavingTemplate(true)
        setError('')
        try {
            const nsArray = templateForm.nameservers.split(/[\n,]+/).map(n => n.trim()).filter(Boolean)
            const payload = {
                name: templateForm.name,
                description: templateForm.description,
                nameservers: nsArray,
                kind: templateForm.kind,
                soa_edit_api: templateForm.soa_edit_api,
                default_ttl: parseInt(templateForm.default_ttl),
                records: templateForm.records,
                is_default: templateForm.is_default,
            }
            if (editTemplateId) {
                await api.updateTemplate(editTemplateId, payload)
                setSuccess('Vorlage aktualisiert!')
            } else {
                await api.createTemplate(payload)
                setSuccess('Vorlage erstellt!')
            }
            setShowTemplateForm(false)
            loadTemplates()
        } catch (err) {
            setError(err.message)
        } finally {
            setSavingTemplate(false)
        }
    }

    async function handleDeleteTemplate(id, name) {
        if (!confirm(`Vorlage "${name}" wirklich löschen?`)) return
        try {
            await api.deleteTemplate(id)
            setSuccess(`Vorlage "${name}" gelöscht`)
            loadTemplates()
        } catch (err) { setError(err.message) }
    }

    // ===== SMTP =====
    const [smtpForm, setSmtpForm] = useState({
        host: '', port: 587, username: '', password: '', from_email: '', from_name: 'DNS Manager', encryption: 'starttls', enabled: false
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
                from_name: data.from_name || 'DNS Manager', encryption: data.encryption || 'starttls',
                enabled: data.enabled || false,
            })
        } catch (err) { /* ignore */ }
        finally { setLoadingSmtp(false) }
    }

    async function handleSaveSmtp(e) {
        e.preventDefault()
        setSavingSmtp(true)
        setError('')
        try {
            await api.updateSmtpSettings(smtpForm)
            setSuccess('SMTP-Einstellungen gespeichert!')
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

    // ===== Server functions =====
    function openAdd() {
        setEditId(null)
        setForm({ name: '', display_name: '', url: '', api_key: '', description: '', allow_writes: true })
        setTestResult(null)
        setShowApiKey(false)
        setShowForm(true)
    }

    function openEdit(s) {
        setEditId(s.id)
        setForm({
            name: s.name,
            display_name: s.display_name || '',
            url: s.url,
            api_key: s.api_key_full || '',
            description: s.description || '',
            allow_writes: s.allow_writes !== false,
        })
        setTestResult(null)
        setShowApiKey(false)
        setShowForm(true)
    }

    async function handleTest() {
        if (!form.url || !form.api_key) {
            setTestResult({ success: false, error: 'URL und API-Key eingeben!' })
            return
        }
        setTesting(true)
        setTestResult(null)
        try {
            const result = await api.testConnection({ url: form.url, api_key: form.api_key })
            setTestResult(result)
        } catch (err) {
            setTestResult({ success: false, error: err.message })
        } finally {
            setTesting(false)
        }
    }

    async function handleSave(e) {
        e.preventDefault()
        setSaving(true)
        setError('')
        try {
            if (editId) {
                await api.updateServerConfig(editId, {
                    display_name: form.display_name,
                    url: form.url,
                    api_key: form.api_key,
                    description: form.description,
                    allow_writes: form.allow_writes,
                })
                setSuccess('Server aktualisiert!')
            } else {
                await api.addServerConfig(form)
                setSuccess('Server hinzugefügt!')
            }
            setShowForm(false)
            loadServers()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(id, name) {
        if (!confirm(`Server "${name}" wirklich löschen? Alle Verbindungsdaten gehen verloren!`)) return
        try {
            await api.deleteServerConfig(id)
            setSuccess(`Server "${name}" gelöscht`)
            loadServers()
        } catch (err) {
            setError(err.message)
        }
    }

    async function toggleActive(s) {
        try {
            await api.updateServerConfig(s.id, { is_active: !s.is_active })
            loadServers()
        } catch (err) {
            setError(err.message)
        }
    }

    async function toggleAllowWrites(s) {
        try {
            await api.updateServerConfig(s.id, { allow_writes: !(s.allow_writes !== false) })
            loadServers()
        } catch (err) {
            setError(err.message)
        }
    }

    const tabs = [
        { id: 'profile', label: 'Profil', icon: UserCog },
        { id: 'servers', label: 'DNS-Server', icon: Server },
        { id: 'templates', label: 'Vorlagen', icon: Copy },
        { id: 'smtp', label: 'E-Mail (SMTP)', icon: Mail },
        { id: 'updates', label: 'Updates', icon: Download },
        { id: 'about', label: 'Über', icon: Database },
    ]

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">Einstellungen</h1>
                <p className="text-text-muted text-sm mt-1">Profil, Systemkonfiguration und Server-Verwaltung</p>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{error}</p>
                    <button onClick={() => setError('')} className="ml-auto text-xs hover:underline">×</button>
                </div>
            )}
            {success && (
                <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success flex items-center gap-3">
                    <CheckCircle2 className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{success}</p>
                    <button onClick={() => setSuccess('')} className="ml-auto text-xs hover:underline">×</button>
                </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 p-1 bg-bg-secondary rounded-xl border border-border">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${activeTab === tab.id
                            ? 'bg-accent/20 text-accent-light'
                            : 'text-text-muted hover:text-text-primary hover:bg-bg-hover'
                            }`}
                    >
                        <tab.icon className="w-4 h-4" />
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* =================== PROFILE TAB =================== */}
            {activeTab === 'profile' && (
                <div className="space-y-6">
                    {/* Profile Info */}
                    <div className="glass-card p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
                                <User className="w-5 h-5 text-accent-light" />
                            </div>
                            <div>
                                <h2 className="text-lg font-semibold text-text-primary">Profil bearbeiten</h2>
                                <p className="text-sm text-text-muted">Ändere deinen Benutzernamen, Anzeigenamen und E-Mail</p>
                            </div>
                        </div>

                        <form onSubmit={handleSaveProfile} className="space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        Benutzername
                                    </label>
                                    <input
                                        type="text"
                                        value={profileForm.username}
                                        onChange={e => setProfileForm({ ...profileForm, username: e.target.value })}
                                        placeholder="admin"
                                        className="w-full px-3 py-2 text-sm"
                                        required
                                        minLength={3}
                                    />
                                    <p className="text-xs text-text-muted mt-1">Wird für den Login verwendet</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        Anzeigename
                                    </label>
                                    <input
                                        type="text"
                                        value={profileForm.display_name}
                                        onChange={e => setProfileForm({ ...profileForm, display_name: e.target.value })}
                                        placeholder="Administrator"
                                        className="w-full px-3 py-2 text-sm"
                                    />
                                    <p className="text-xs text-text-muted mt-1">Wird in der Oberfläche angezeigt</p>
                                </div>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">
                                    E-Mail
                                </label>
                                <div className="relative">
                                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                                    <input
                                        type="email"
                                        value={profileForm.email}
                                        onChange={e => setProfileForm({ ...profileForm, email: e.target.value })}
                                        placeholder="admin@example.com"
                                        className="w-full pl-10 pr-3 py-2 text-sm"
                                    />
                                </div>
                            </div>
                            
                            {profile?.role === 'admin' && (
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        System-Titel
                                    </label>
                                    <input
                                        type="text"
                                        value={profileForm.app_name}
                                        onChange={e => setProfileForm({ ...profileForm, app_name: e.target.value })}
                                        placeholder="DNS Manager"
                                        className="w-full px-3 py-2 text-sm"
                                        required
                                    />
                                    <p className="text-xs text-text-muted mt-1">Der Name der Anwendung (wird oben links im Menü angezeigt)</p>
                                </div>
                            )}

                            {profile && (
                                <div className="flex items-center gap-4 pt-2 text-xs text-text-muted">
                                    <span>Rolle: <span className="text-accent-light font-medium">{profile.role === 'admin' ? 'Administrator' : 'Benutzer'}</span></span>
                                    {profile.created_at && <span>Erstellt: {new Date(profile.created_at).toLocaleDateString('de-DE')}</span>}
                                    {profile.last_login && <span>Letzter Login: {new Date(profile.last_login).toLocaleString('de-DE')}</span>}
                                </div>
                            )}

                            <div className="flex justify-end pt-2 border-t border-border">
                                <button
                                    type="submit"
                                    disabled={savingProfile}
                                    className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 transition-all"
                                >
                                    {savingProfile && <Loader2 className="w-4 h-4 animate-spin" />}
                                    Profil speichern
                                </button>
                            </div>
                        </form>
                    </div>

                    {/* Password Change */}
                    <div className="glass-card p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-10 h-10 rounded-xl bg-warning/20 flex items-center justify-center">
                                <Lock className="w-5 h-5 text-warning" />
                            </div>
                            <div>
                                <h2 className="text-lg font-semibold text-text-primary">Passwort ändern</h2>
                                <p className="text-sm text-text-muted">Ändere dein Passwort für den Login</p>
                            </div>
                        </div>

                        <form onSubmit={handleChangePassword} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">
                                    Aktuelles Passwort
                                </label>
                                <div className="relative">
                                    <input
                                        type={showCurrentPw ? 'text' : 'password'}
                                        value={passwordForm.current_password}
                                        onChange={e => setPasswordForm({ ...passwordForm, current_password: e.target.value })}
                                        placeholder="••••••••"
                                        className="w-full px-3 py-2 pr-10 text-sm"
                                        required
                                    />
                                    <button type="button" onClick={() => setShowCurrentPw(!showCurrentPw)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                        {showCurrentPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        Neues Passwort
                                    </label>
                                    <div className="relative">
                                        <input
                                            type={showNewPw ? 'text' : 'password'}
                                            value={passwordForm.new_password}
                                            onChange={e => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                                            placeholder="••••••••"
                                            className="w-full px-3 py-2 pr-10 text-sm"
                                            required
                                            minLength={4}
                                        />
                                        <button type="button" onClick={() => setShowNewPw(!showNewPw)}
                                            className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                            {showNewPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                        </button>
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        Neues Passwort bestätigen
                                    </label>
                                    <input
                                        type={showNewPw ? 'text' : 'password'}
                                        value={passwordForm.confirm_password}
                                        onChange={e => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })}
                                        placeholder="••••••••"
                                        className="w-full px-3 py-2 text-sm"
                                        required
                                        minLength={4}
                                    />
                                </div>
                            </div>

                            {passwordForm.new_password && passwordForm.confirm_password && passwordForm.new_password !== passwordForm.confirm_password && (
                                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm flex items-center gap-2">
                                    <AlertCircle className="w-4 h-4 shrink-0" />
                                    Die Passwörter stimmen nicht überein
                                </div>
                            )}

                            <div className="flex justify-end pt-2 border-t border-border">
                                <button
                                    type="submit"
                                    disabled={savingPassword || (passwordForm.new_password !== passwordForm.confirm_password)}
                                    className="px-5 py-2 bg-gradient-to-r from-warning/80 to-orange-600 hover:from-warning hover:to-orange-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 transition-all"
                                >
                                    {savingPassword && <Loader2 className="w-4 h-4 animate-spin" />}
                                    Passwort ändern
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* =================== SMTP TAB =================== */}
            {activeTab === 'smtp' && (
                <div className="space-y-6">
                    <div className="glass-card p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
                                <Mail className="w-5 h-5 text-accent-light" />
                            </div>
                            <div>
                                <h2 className="text-lg font-semibold text-text-primary">E-Mail-Versand (SMTP)</h2>
                                <p className="text-sm text-text-muted">Konfiguriere den SMTP-Server für E-Mail-Versand (z.B. Benachrichtigungen, Passwort-Reset)</p>
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
                                        <span className="text-sm font-medium text-text-primary">SMTP aktivieren</span>
                                        <p className="text-xs text-text-muted">E-Mails können nur gesendet werden, wenn SMTP aktiviert ist</p>
                                    </div>
                                </label>

                                {/* Server & Port */}
                                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                    <div className="md:col-span-2">
                                        <label className="block text-sm font-medium text-text-secondary mb-1">SMTP-Server</label>
                                        <input type="text" value={smtpForm.host} onChange={e => setSmtpForm({ ...smtpForm, host: e.target.value })}
                                            placeholder="smtp.gmail.com" className="w-full px-3 py-2 text-sm" />
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-1">Port</label>
                                        <input type="number" value={smtpForm.port} onChange={e => setSmtpForm({ ...smtpForm, port: parseInt(e.target.value) || 587 })}
                                            placeholder="587" className="w-full px-3 py-2 text-sm" />
                                    </div>
                                </div>

                                {/* Verschlüsselung */}
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Verschlüsselung</label>
                                    <select value={smtpForm.encryption} onChange={e => setSmtpForm({ ...smtpForm, encryption: e.target.value })} className="w-full px-3 py-2 text-sm">
                                        <option value="starttls">STARTTLS (Port 587 – empfohlen)</option>
                                        <option value="ssl">SSL/TLS (Port 465)</option>
                                        <option value="none">Keine Verschlüsselung (Port 25)</option>
                                    </select>
                                </div>

                                {/* Zugangsdaten */}
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-1">Benutzername</label>
                                        <input type="text" value={smtpForm.username} onChange={e => setSmtpForm({ ...smtpForm, username: e.target.value })}
                                            placeholder="user@example.com" className="w-full px-3 py-2 text-sm" />
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-1">Passwort</label>
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
                                        <label className="block text-sm font-medium text-text-secondary mb-1">Absender E-Mail</label>
                                        <input type="email" value={smtpForm.from_email} onChange={e => setSmtpForm({ ...smtpForm, from_email: e.target.value })}
                                            placeholder="noreply@meinedomain.de" className="w-full px-3 py-2 text-sm" />
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-1">Absender Name</label>
                                        <input type="text" value={smtpForm.from_name} onChange={e => setSmtpForm({ ...smtpForm, from_name: e.target.value })}
                                            placeholder="DNS Manager" className="w-full px-3 py-2 text-sm" />
                                    </div>
                                </div>

                                {/* Buttons */}
                                <div className="flex items-center justify-between pt-2 border-t border-border">
                                    <div className="flex items-center gap-2">
                                        <button type="button" onClick={handleTestSmtp} disabled={testingSmtp || !smtpForm.host}
                                            className="px-4 py-2 text-sm font-medium border border-border rounded-lg text-text-secondary hover:text-text-primary hover:bg-bg-hover disabled:opacity-50 flex items-center gap-2">
                                            {testingSmtp ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wifi className="w-4 h-4" />}
                                            Verbindung testen
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
                                        Speichern
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
                                <h2 className="text-lg font-semibold text-text-primary">Test-E-Mail senden</h2>
                                <p className="text-sm text-text-muted">Sende eine Test-E-Mail um sicherzustellen, dass alles funktioniert</p>
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
            )}

            {/* =================== UPDATES TAB =================== */}
            {activeTab === 'updates' && (
                <div className="space-y-6">
                    <div className="glass-card p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                                <Download className="w-5 h-5 text-success" />
                            </div>
                            <div>
                                <h2 className="text-lg font-semibold text-text-primary">System aktualisieren</h2>
                                <p className="text-sm text-text-muted">Hol dir die neuesten Funktionen von GitHub</p>
                            </div>
                        </div>

                        <div className="p-5 rounded-xl bg-bg-primary border border-border mb-8">
                            <h3 className="text-sm font-semibold text-text-primary mb-3 text-accent-light">Wie aktualisiere ich das System?</h3>
                            <p className="text-sm text-text-secondary mb-5 leading-relaxed">
                                Aus Sicherheitsgründen (Docker-Isolierung) kann sich die Anwendung nicht durch einen Klick auf einen Button selbst neu installieren.
                                Das wäre ein Sicherheitsrisiko.
                                <br /><br />
                                Du kannst das System aber jederzeit ganz einfach über dein Server-Terminal aktualisieren:
                            </p>

                            <div className="relative group">
                                <div className="absolute inset-y-0 left-0 bg-accent w-1 rounded-l-lg"></div>
                                <pre className="bg-bg-hover text-text-primary p-4 rounded-r-lg rounded-l-sm text-sm font-mono overflow-x-auto pl-6 border border-border/50 border-l-0">
                                    <span className="text-text-muted"># 1. In den Ordner wechseln</span>{'\n'}
                                    <span className="text-accent-light font-medium">cd</span> /pfad/zu/deinem/dns-manager{'\n\n'}
                                    <span className="text-text-muted"># 2. Das Skript ausführen</span>{'\n'}
                                    <span className="text-accent-light font-medium">./update.sh</span>
                                </pre>
                            </div>

                            <p className="text-xs text-text-muted mt-4">
                                💡 Das Skript lädt automatisch die neuesten Updates herunter, baut das System komprimiert neu und startet es <strong className="text-text-secondary">ohne Datenverlust</strong>.
                            </p>
                        </div>

                        {/* Commits von GitHub */}
                        <div>
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2">
                                    <Github className="w-4 h-4" /> Letzte Änderungen (GitHub)
                                </h3>
                                <button onClick={loadCommits} disabled={loadingCommits} className="text-xs text-text-muted hover:text-text-primary flex items-center gap-1">
                                    <RefreshCw className={`w-3 h-3 ${loadingCommits ? 'animate-spin' : ''}`} /> {loadingCommits ? 'Lade...' : 'Neu laden'}
                                </button>
                            </div>

                            {commitError ? (
                                <div className="p-4 rounded-lg bg-bg-hover border border-border text-text-muted text-sm text-center flex flex-col items-center gap-2">
                                    <Lock className="w-5 h-5 opacity-50" />
                                    Dein Repository ist auf <strong>Privat</strong> gestellt. Die öffentlichen Updates können hier nicht eingeblendet werden. Das ist aber <strong>gut für die Sicherheit!</strong>
                                </div>
                            ) : commits.length === 0 && !loadingCommits ? (
                                <p className="text-sm text-text-muted">Keine Änderungen gefunden.</p>
                            ) : (
                                <div className="space-y-3">
                                    {commits.map((c, i) => (
                                        <div key={c.sha} className="p-4 rounded-lg bg-bg-primary border border-border flex gap-4 items-start">
                                            <div className="w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center shrink-0 mt-1">
                                                <Code className="w-4 h-4 text-accent-light" />
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span className="text-sm font-medium text-text-primary truncate">{c.commit.message.split('\n')[0]}</span>
                                                </div>
                                                <div className="flex items-center gap-3 text-xs text-text-muted">
                                                    <span>{new Date(c.commit.author.date).toLocaleString('de-DE')}</span>
                                                    <span className="font-mono bg-bg-hover px-1.5 py-0.5 rounded border border-border">{c.sha.substring(0, 7)}</span>
                                                    <span>von <strong className="text-text-secondary">{c.commit.author.name}</strong></span>
                                                </div>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}

            {/* =================== SERVERS TAB =================== */}
            {activeTab === 'servers' && (
                <div className="space-y-4">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold text-text-primary">PowerDNS Server</h2>
                        <div className="flex gap-2">
                            <button onClick={loadServers} className="flex items-center gap-2 px-3 py-2 text-sm text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-colors border border-border">
                                <RefreshCw className="w-4 h-4" /> Aktualisieren
                            </button>
                            <button onClick={openAdd} className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all">
                                <Plus className="w-4 h-4" /> Server hinzufügen
                            </button>
                        </div>
                    </div>

                    {/* Info-Box: DNS Server werden in der Datenbank gespeichert */}
                    <div className="p-4 rounded-xl bg-accent/5 border border-accent/20">
                        <p className="text-sm text-text-secondary">
                            <strong className="text-accent-light">💡 Hinweis:</strong> Server-Konfigurationen werden in der <strong>Datenbank</strong> gespeichert, nicht in der .env Datei.
                            Die .env-Variable <code className="px-1.5 py-0.5 bg-bg-hover rounded text-xs font-mono">PDNS_SERVERS</code> wird nur beim ersten Start importiert.
                            Danach werden alle Änderungen hier über die Weboberfläche verwaltet.
                        </p>
                    </div>

                    {servers.length === 0 ? (
                        <div className="glass-card p-12 text-center">
                            <Server className="w-16 h-16 mx-auto mb-4 text-text-muted opacity-30" />
                            <h3 className="text-lg font-semibold text-text-primary mb-2">Kein Server konfiguriert</h3>
                            <p className="text-sm text-text-muted mb-4">
                                Füge deinen ersten PowerDNS-Server hinzu, um loszulegen.<br />
                                Du brauchst die URL und den API-Key deines PowerDNS-Servers.
                            </p>
                            <button onClick={openAdd} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg font-medium text-sm">
                                <Plus className="w-4 h-4 inline mr-2" /> Ersten Server hinzufügen
                            </button>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {servers.map(s => (
                                <div key={s.id} className={`glass-card p-5 ${!s.is_active ? 'opacity-50' : ''}`}>
                                    <div className="flex items-start gap-4">
                                        {/* Status */}
                                        <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${s.is_online ? 'bg-success/20' : 'bg-danger/20'
                                            }`}>
                                            {s.is_online ? <Wifi className="w-6 h-6 text-success" /> : <WifiOff className="w-6 h-6 text-danger" />}
                                        </div>

                                        {/* Info */}
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <h3 className="font-semibold text-text-primary">{s.display_name || s.name}</h3>
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-bg-hover text-text-muted border border-border font-mono">{s.name}</span>
                                                <span className={`text-xs px-2 py-0.5 rounded-full ${s.is_online
                                                    ? 'bg-success/10 text-success border border-success/30'
                                                    : 'bg-danger/10 text-danger border border-danger/30'
                                                    }`}>
                                                    {s.is_online ? 'Online' : 'Offline'}
                                                </span>
                                                {!s.is_active && (
                                                    <span className="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">Deaktiviert</span>
                                                )}
                                                <button type="button" onClick={() => toggleAllowWrites(s)} className={`text-xs px-2 py-0.5 rounded-full border cursor-pointer hover:opacity-80 transition-opacity ${s.allow_writes !== false ? 'bg-success/10 text-success border-success/30' : 'bg-bg-hover text-text-muted border-border'}`} title={s.allow_writes !== false ? 'Klicken: Speichern deaktivieren (nur Lesen)' : 'Klicken: Speichern aktivieren'}>
                                                    {s.allow_writes !== false ? 'Speichern: Ja' : 'Speichern: Nein'}
                                                </button>
                                            </div>
                                            <p className="text-sm text-text-muted font-mono mt-1">{s.url}</p>
                                            <div className="flex items-center gap-4 mt-2 text-xs text-text-muted">
                                                {s.version && <span>Version: <span className="text-text-secondary">{s.version}</span></span>}
                                                {s.zone_count != null && <span>Zonen: <span className="text-text-secondary">{s.zone_count}</span></span>}
                                                {s.description && <span className="italic">{s.description}</span>}
                                            </div>
                                        </div>

                                        {/* Actions */}
                                        <div className="flex items-center gap-1 shrink-0">
                                            <button onClick={() => openEdit(s)} className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors" title="Bearbeiten">
                                                <Pencil className="w-4 h-4" />
                                            </button>
                                            <button onClick={() => toggleActive(s)} className={`p-2 rounded-lg transition-colors ${s.is_active ? 'text-text-muted hover:text-warning hover:bg-warning/10' : 'text-success hover:bg-success/10'
                                                }`} title={s.is_active ? 'Deaktivieren' : 'Aktivieren'}>
                                                {s.is_active ? <WifiOff className="w-4 h-4" /> : <Wifi className="w-4 h-4" />}
                                            </button>
                                            <button onClick={() => handleDelete(s.id, s.name)} className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors" title="Löschen">
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* =================== TEMPLATES TAB =================== */}
            {activeTab === 'templates' && (
                <div className="space-y-4">
                    <div className="flex items-center justify-between">
                        <div>
                            <h2 className="text-lg font-semibold text-text-primary">Zonen-Vorlagen</h2>
                            <p className="text-sm text-text-muted">Erstelle Vorlagen mit Nameservern, SOA-Einstellungen und Standard-DNS-Einträgen, die beim Anlegen neuer Domains automatisch übernommen werden.</p>
                        </div>
                        <button onClick={openTemplateAdd} className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all shrink-0">
                            <Plus className="w-4 h-4" /> Neue Vorlage
                        </button>
                    </div>

                    {loadingTemplates ? (
                        <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
                    ) : templates.length === 0 ? (
                        <div className="glass-card p-12 text-center">
                            <Copy className="w-16 h-16 mx-auto mb-4 text-text-muted opacity-30" />
                            <h3 className="text-lg font-semibold text-text-primary mb-2">Keine Vorlagen vorhanden</h3>
                            <p className="text-sm text-text-muted mb-4">Erstelle deine erste Vorlage, z.B. \"Standard\" mit deinen Nameservern und häufig genutzten DNS-Einträgen.</p>
                            <button onClick={openTemplateAdd} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg font-medium text-sm">
                                <Plus className="w-4 h-4 inline mr-2" /> Erste Vorlage erstellen
                            </button>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {templates.map(t => (
                                <div key={t.id} className="glass-card p-5">
                                    <div className="flex items-start gap-4">
                                        <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${t.is_default ? 'bg-warning/20' : 'bg-accent/20'}`}>
                                            {t.is_default ? <Star className="w-6 h-6 text-warning" /> : <Copy className="w-6 h-6 text-accent-light" />}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <h3 className="font-semibold text-text-primary">{t.name}</h3>
                                                {t.is_default && <span className="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">Standard</span>}
                                            </div>
                                            {t.description && <p className="text-sm text-text-muted mt-0.5">{t.description}</p>}
                                            <div className="flex items-center gap-4 mt-2 text-xs text-text-muted flex-wrap">
                                                <span>NS: <span className="text-text-secondary">{(t.nameservers || []).join(', ') || '–'}</span></span>
                                                <span>Typ: <span className="text-text-secondary">{t.kind}</span></span>
                                                <span>TTL: <span className="text-text-secondary">{t.default_ttl}s</span></span>
                                                <span>Records: <span className="text-text-secondary">{(t.records || []).length}</span></span>
                                            </div>
                                            {(t.records || []).length > 0 && (
                                                <div className="mt-2 flex flex-wrap gap-1">
                                                    {t.records.map((r, i) => (
                                                        <span key={i} className="text-xs px-2 py-0.5 rounded bg-bg-hover border border-border text-text-secondary font-mono">
                                                            {r.name} {r.type} {r.prio ? r.prio + ' ' : ''}{r.content}
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-1 shrink-0">
                                            <button onClick={() => openTemplateEdit(t)} className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors" title="Bearbeiten">
                                                <Pencil className="w-4 h-4" />
                                            </button>
                                            <button onClick={() => handleDeleteTemplate(t.id, t.name)} className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors" title="Löschen">
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* =================== TEMPLATE FORM MODAL =================== */}
            {showTemplateForm && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowTemplateForm(false)}>
                    <div className="glass-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-lg font-bold text-text-primary">
                                {editTemplateId ? 'Vorlage bearbeiten' : 'Neue Vorlage erstellen'}
                            </h2>
                            <button onClick={() => setShowTemplateForm(false)} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <form onSubmit={handleSaveTemplate} className="space-y-4">
                            {/* Name & Beschreibung */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Vorlagen-Name *</label>
                                    <input type="text" value={templateForm.name} onChange={e => setTemplateForm({ ...templateForm, name: e.target.value })}
                                        placeholder="z.B. Standard" className="w-full px-3 py-2 text-sm" required />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Beschreibung</label>
                                    <input type="text" value={templateForm.description} onChange={e => setTemplateForm({ ...templateForm, description: e.target.value })}
                                        placeholder="Mein Standard-Setup" className="w-full px-3 py-2 text-sm" />
                                </div>
                            </div>

                            {/* Nameservers */}
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">Nameserver</label>
                                <textarea value={templateForm.nameservers} onChange={e => setTemplateForm({ ...templateForm, nameservers: e.target.value })}
                                    placeholder="ns1.example.com., ns2.example.com." className="w-full px-3 py-2 text-sm min-h-[60px]" />
                                <p className="text-xs text-text-muted mt-1">Getrennt durch Komma. Achte auf den Punkt am Ende.</p>
                            </div>

                            {/* Kind, SOA-EDIT-API, TTL */}
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Typ</label>
                                    <select value={templateForm.kind} onChange={e => setTemplateForm({ ...templateForm, kind: e.target.value })} className="w-full px-3 py-2 text-sm">
                                        <option value="Native">Native</option>
                                        <option value="Master">Master</option>
                                        <option value="Slave">Slave</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">SOA-EDIT-API</label>
                                    <select value={templateForm.soa_edit_api} onChange={e => setTemplateForm({ ...templateForm, soa_edit_api: e.target.value })} className="w-full px-3 py-2 text-sm">
                                        <option value="DEFAULT">DEFAULT</option>
                                        <option value="INCEPTION-INCREMENT">INCEPTION-INCREMENT</option>
                                        <option value="EPOCH">EPOCH</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Standard-TTL</label>
                                    <select value={templateForm.default_ttl} onChange={e => setTemplateForm({ ...templateForm, default_ttl: parseInt(e.target.value) })} className="w-full px-3 py-2 text-sm">
                                        <option value={60}>1 Min</option>
                                        <option value={300}>5 Min</option>
                                        <option value={3600}>1 Std</option>
                                        <option value={14400}>4 Std</option>
                                        <option value={86400}>1 Tag</option>
                                    </select>
                                </div>
                            </div>

                            {/* Standard-Vorlage Checkbox */}
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input type="checkbox" checked={templateForm.is_default} onChange={e => setTemplateForm({ ...templateForm, is_default: e.target.checked })} className="w-4 h-4 rounded" />
                                <span className="text-sm text-text-secondary">Als <strong className="text-warning">Standard-Vorlage</strong> verwenden (wird automatisch vorausgewählt)</span>
                            </label>

                            {/* Records */}
                            <div className="border-t border-border pt-4">
                                <h3 className="text-sm font-semibold text-text-primary mb-3">Standard DNS-Einträge</h3>
                                <p className="text-xs text-text-muted mb-3">Diese Einträge werden automatisch erstellt, wenn du eine neue Domain mit dieser Vorlage anlegst. Nutze <code className="px-1 py-0.5 bg-bg-hover rounded">@</code> als Platzhalter für den Domainnamen.</p>

                                {/* Existing records */}
                                {templateForm.records.length > 0 && (
                                    <div className="space-y-1 mb-3">
                                        {templateForm.records.map((r, i) => (
                                            <div key={i} className="flex items-center gap-2 p-2 rounded-lg bg-bg-primary border border-border text-sm">
                                                <span className="font-mono text-text-secondary flex-1">
                                                    <span className="text-accent-light">{r.name}</span>{' '}
                                                    <span className="text-text-muted">{r.type}</span>{' '}
                                                    {r.prio != null && <span className="text-warning">{r.prio} </span>}
                                                    <span className="text-text-primary">{r.content}</span>{' '}
                                                    <span className="text-text-muted">TTL:{r.ttl}</span>
                                                </span>
                                                <button type="button" onClick={() => removeRecordFromTemplate(i)} className="p-1 rounded hover:bg-danger/10 text-text-muted hover:text-danger">
                                                    <Trash2 className="w-3 h-3" />
                                                </button>
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {/* Add new record row */}
                                <div className="grid grid-cols-12 gap-2 items-end">
                                    <div className="col-span-2">
                                        <label className="block text-xs text-text-muted mb-1">Name</label>
                                        <input type="text" value={newRecord.name} onChange={e => setNewRecord({ ...newRecord, name: e.target.value })}
                                            placeholder="@" className="w-full px-2 py-1.5 text-xs" />
                                    </div>
                                    <div className="col-span-2">
                                        <label className="block text-xs text-text-muted mb-1">Typ</label>
                                        <select value={newRecord.type} onChange={e => setNewRecord({ ...newRecord, type: e.target.value })} className="w-full px-2 py-1.5 text-xs">
                                            {['A','AAAA','CNAME','MX','TXT','NS','SRV','CAA','PTR'].map(t => <option key={t} value={t}>{t}</option>)}
                                        </select>
                                    </div>
                                    <div className="col-span-3">
                                        <label className="block text-xs text-text-muted mb-1">
                                            {{ A: 'IPv4-Adresse', AAAA: 'IPv6-Adresse', CNAME: 'Ziel-Domain', MX: 'Mailserver', TXT: 'Textinhalt', NS: 'Nameserver', SRV: 'Ziel', CAA: 'CA (z.B. letsencrypt.org)', PTR: 'Hostname' }[newRecord.type] || 'Inhalt'}
                                        </label>
                                        <input type="text" value={newRecord.content} onChange={e => setNewRecord({ ...newRecord, content: e.target.value })}
                                            placeholder={{ A: '93.184.216.34', AAAA: '2001:db8::1', CNAME: 'example.com.', MX: 'mail.example.com.', TXT: 'v=spf1 include:... ~all', NS: 'ns1.example.com.', SRV: 'server.example.com.', CAA: '0 issue "letsencrypt.org"', PTR: 'host.example.com.' }[newRecord.type] || '...'} className="w-full px-2 py-1.5 text-xs" />
                                    </div>
                                    <div className="col-span-2">
                                        <label className="block text-xs text-text-muted mb-1">TTL</label>
                                        <input type="number" value={newRecord.ttl} onChange={e => setNewRecord({ ...newRecord, ttl: parseInt(e.target.value) || 3600 })}
                                            className="w-full px-2 py-1.5 text-xs" />
                                    </div>
                                    {(newRecord.type === 'MX' || newRecord.type === 'SRV') && (
                                        <div className="col-span-1">
                                            <label className="block text-xs text-text-muted mb-1">Prio</label>
                                            <input type="number" value={newRecord.prio || ''} onChange={e => setNewRecord({ ...newRecord, prio: parseInt(e.target.value) || 0 })}
                                                placeholder="10" className="w-full px-2 py-1.5 text-xs" />
                                        </div>
                                    )}
                                    <div className={newRecord.type === 'MX' || newRecord.type === 'SRV' ? 'col-span-2' : 'col-span-3'}>
                                        <button type="button" onClick={addRecordToTemplate}
                                            className="w-full px-2 py-1.5 text-xs font-medium border border-accent/40 text-accent-light rounded-lg hover:bg-accent/10 flex items-center justify-center gap-1">
                                            <Plus className="w-3 h-3" /> Hinzufügen
                                        </button>
                                    </div>
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-2 border-t border-border">
                                <button type="button" onClick={() => setShowTemplateForm(false)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">
                                    Abbrechen
                                </button>
                                <button type="submit" disabled={savingTemplate} className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {savingTemplate && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {editTemplateId ? 'Speichern' : 'Erstellen'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* =================== ABOUT TAB =================== */}
            {activeTab === 'about' && (
                <div className="glass-card p-5">
                    <h2 className="text-lg font-semibold text-text-primary mb-4">Über DNS Manager</h2>
                    <div className="space-y-3">
                        {[
                            ['Version', appInfo?.app_version || '–'],
                            ['Frontend', 'React + Vite + Tailwind CSS'],
                            ['Backend', 'Python FastAPI'],
                            ['Datenbank', 'MariaDB 11'],
                            ['DNS-Engine', 'PowerDNS Authoritative'],
                            ['Auth', 'JWT (Bearer Token)'],
                        ].map(([k, v]) => (
                            <div key={k} className="flex justify-between p-3 bg-bg-primary rounded-lg border border-border">
                                <span className="text-sm text-text-muted">{k}</span>
                                <span className="text-sm font-medium text-text-primary">{v}</span>
                            </div>
                        ))}
                    </div>
                    <div className="mt-4 p-4 bg-accent/5 rounded-xl border border-accent/20">
                        <p className="text-sm text-text-secondary">
                            DNS Manager ist ein selbst gehostetes Admin-Panel für PowerDNS.
                            Open Source und kostenlos. 🚀
                        </p>
                    </div>
                </div>
            )}

            {/* =================== ADD/EDIT SERVER MODAL =================== */}
            {showForm && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowForm(false)}>
                    <div className="glass-card p-6 w-full max-w-xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-lg font-bold text-text-primary">
                                {editId ? 'Server bearbeiten' : 'Neuen Server hinzufügen'}
                            </h2>
                            <button onClick={() => setShowForm(false)} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <form onSubmit={handleSave} className="space-y-4">
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Server-Name *</label>
                                    <input
                                        type="text" value={form.name}
                                        onChange={e => setForm({ ...form, name: e.target.value })}
                                        placeholder="server1" className="w-full px-3 py-2 text-sm"
                                        required disabled={!!editId} minLength={1}
                                    />
                                    <p className="text-xs text-text-muted mt-0.5">Eindeutig, z.B. server1, server2</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Anzeigename</label>
                                    <input
                                        type="text" value={form.display_name}
                                        onChange={e => setForm({ ...form, display_name: e.target.value })}
                                        placeholder="Nameserver 1" className="w-full px-3 py-2 text-sm"
                                    />
                                </div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">PowerDNS API URL *</label>
                                <input
                                    type="url" value={form.url}
                                    onChange={e => setForm({ ...form, url: e.target.value })}
                                    placeholder="http://192.168.1.10:8081" className="w-full px-3 py-2 text-sm"
                                    required
                                />
                                <p className="text-xs text-text-muted mt-0.5">Die URL zum PowerDNS-Webserver (Standard-Port: 8081)</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">API Key *</label>
                                <div className="relative">
                                    <input
                                        type={showApiKey ? 'text' : 'password'} value={form.api_key}
                                        onChange={e => setForm({ ...form, api_key: e.target.value })}
                                        placeholder="dein-api-key" className="w-full px-3 py-2 pr-10 text-sm"
                                        required
                                    />
                                    <button type="button" onClick={() => setShowApiKey(!showApiKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                        {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                <p className="text-xs text-text-muted mt-0.5">Aus der PowerDNS-Konfiguration (api-key in pdns.conf)</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">Beschreibung</label>
                                <input
                                    type="text" value={form.description}
                                    onChange={e => setForm({ ...form, description: e.target.value })}
                                    placeholder="Hetzner Server DE, Hauptserver..." className="w-full px-3 py-2 text-sm"
                                />
                            </div>

                            <label className="flex items-center gap-3 cursor-pointer p-3 rounded-lg border border-border hover:bg-bg-hover/50 transition-colors">
                                <input
                                    type="checkbox"
                                    checked={form.allow_writes !== false}
                                    onChange={e => setForm({ ...form, allow_writes: e.target.checked })}
                                    className="w-4 h-4 rounded"
                                />
                                <div>
                                    <span className="text-sm font-medium text-text-primary">Auf diesem Server speichern</span>
                                    <p className="text-xs text-text-muted mt-0.5">Zonen und Änderungen werden auf diesem Server geschrieben. Bei gemeinsamer Datenbank (z. B. zwei Server-Einträge auf eine DB) nur bei einem Server aktivieren.</p>
                                </div>
                            </label>

                            {/* Test Connection */}
                            <div className="border-t border-border pt-4">
                                <button
                                    type="button" onClick={handleTest} disabled={testing || !form.url || !form.api_key}
                                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium border border-accent/40 text-accent-light rounded-lg hover:bg-accent/10 disabled:opacity-50 transition-all"
                                >
                                    {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                                    Verbindung testen
                                </button>

                                {testResult && (
                                    <div className={`mt-3 p-3 rounded-lg text-sm ${testResult.success
                                        ? 'bg-success/10 border border-success/30 text-success'
                                        : 'bg-danger/10 border border-danger/30 text-danger'
                                        }`}>
                                        {testResult.success ? (
                                            <div>
                                                <div className="flex items-center gap-2 font-medium mb-1">
                                                    <CheckCircle2 className="w-4 h-4" /> Verbindung erfolgreich!
                                                </div>
                                                <div className="text-xs space-y-0.5 text-text-secondary">
                                                    <p>Version: {testResult.server_info.version}</p>
                                                    <p>Typ: {testResult.server_info.daemon_type}</p>
                                                    <p>Zonen: {testResult.server_info.zone_count}</p>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="flex items-center gap-2">
                                                <AlertCircle className="w-4 h-4 shrink-0" />
                                                <span>{testResult.error}</span>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>

                            <div className="flex justify-end gap-3 pt-2 border-t border-border">
                                <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">
                                    Abbrechen
                                </button>
                                <button type="submit" disabled={saving} className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {editId ? 'Speichern' : 'Hinzufügen'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
