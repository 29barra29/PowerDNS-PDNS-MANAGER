import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Settings, Server, Database, Plus, Trash2, Pencil, Loader2, AlertCircle, CheckCircle2, RefreshCw, Wifi, WifiOff, Eye, EyeOff, X, Zap, UserCog, Lock, Mail, User, Download, GitCommit, Code, Sliders, Copy, Check, Star, Send, Shield, UserPlus } from 'lucide-react'
import CaptchaWidget from '../components/CaptchaWidget'
import api from '../api'
import { ALL_RECORD_TYPE_KEYS, TEMPLATE_CONTENT_PLACEHOLDERS } from '../constants/dnsRecordTypes'
import DnsRecordTypeHint from '../components/DnsRecordTypeHint'
import { useUpdateAvailability } from '../hooks/useUpdateAvailability'
import { compareSemver } from '../utils/semverCompare'
import { LANGUAGES } from '../i18n'

export default function SettingsPage() {
    const { t, i18n } = useTranslation()
    const { updateAvailable, dismissUpdate, latestVersion, currentVersion } = useUpdateAvailability()
    const [activeTab, setActiveTab] = useState('profile')
    const [servers, setServers] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')

    // Profile
    const [profile, setProfile] = useState(null)
    const [profileForm, setProfileForm] = useState({
        username: '', display_name: '', email: '', app_name: 'DNS Manager', app_base_url: '',
        registration_enabled: false, forgot_password_enabled: false,
        app_tagline: '', app_creator: '', app_logo_url: '',
        phone: '', company: '', street: '', postal_code: '', city: '', country: '', date_of_birth: '', preferred_language: 'de',
    })
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
    // Admin-only Daten (install_path, app_base_url) – nicht öffentlich
    const [adminInfo, setAdminInfo] = useState(null)
    // API-Key on-demand: { [serverId]: 'plaintext-key' }
    const [revealedKeys, setRevealedKeys] = useState({})
    const [revealingKey, setRevealingKey] = useState(false)

    // Add/Edit Server
    const [showForm, setShowForm] = useState(false)
    const [editId, setEditId] = useState(null)
    const [form, setForm] = useState({ name: '', display_name: '', url: '', api_key: '', description: '' })
    const [saving, setSaving] = useState(false)
    const [showApiKey, setShowApiKey] = useState(false)

    // Test connection
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState(null)
    const [uploadingLogo, setUploadingLogo] = useState(false)
    // Modal-spezifische Fehler – damit man sie nicht hinter dem Overlay verliert
    const [serverModalError, setServerModalError] = useState('')
    const [templateModalError, setTemplateModalError] = useState('')
    // "Befehl kopiert"-Toast für den Update-Tab
    const [updateCmdCopied, setUpdateCmdCopied] = useState(false)

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
    }, []) // eslint-disable-line react-hooks/exhaustive-deps -- run once on mount

    useEffect(() => {
        if (profile?.role === 'admin') {
            loadServers()
            loadTemplates()
            // install_path / app_base_url nur für Admins laden – steht nicht mehr im öffentlichen /app-info
            api.getAdminInfo()
                .then((info) => {
                    setAdminInfo(info)
                    setProfileForm((prev) => ({ ...prev, app_base_url: info.app_base_url || prev.app_base_url || '' }))
                })
                .catch(() => setAdminInfo(null))
        }
    }, [profile?.role])

    useEffect(() => {
        if (activeTab === 'about' || activeTab === 'updates') api.getAppInfo().then(setAppInfo).catch(() => setAppInfo(null))
    }, [activeTab])

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- guard rail to keep non-admins on allowed tab
        if (profile && profile.role !== 'admin' && !['profile', 'about'].includes(activeTab)) setActiveTab('profile')
        // eslint-disable-next-line react-hooks/exhaustive-deps -- profile intentionally partial to avoid loops
    }, [profile?.role, activeTab])

    useEffect(() => {
        if (profile?.role !== 'admin') return
        if (activeTab === 'updates' && commits.length === 0) {
            loadCommits()
        }
        if (activeTab === 'smtp') {
            loadSmtp()
        }
        if (activeTab === 'security') {
            loadCaptcha()
        }
        if (activeTab === 'welcome') {
            loadWelcome()
        }
    }, [activeTab, profile?.role]) // eslint-disable-line react-hooks/exhaustive-deps -- load* stable, commits.length intentional

    // Erfolgsmeldung verschwindet nach 4 s automatisch (verdeckt nichts dauerhaft)
    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    // "Befehl kopiert"-Toast nach 2 s zurücksetzen
    useEffect(() => {
        if (!updateCmdCopied) return
        const t = setTimeout(() => setUpdateCmdCopied(false), 2000)
        return () => clearTimeout(t)
    }, [updateCmdCopied])

    // Roter Punkt entfernen, sobald der Reiter „Updates“ geöffnet wurde
    useEffect(() => {
        if (activeTab === 'updates' && updateAvailable) {
            dismissUpdate()
        }
    }, [activeTab, updateAvailable, dismissUpdate])

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
            const lang = data.preferred_language || 'de'
            if (lang !== i18n.language) i18n.changeLanguage(lang)
            setProfileForm({
                username: data.username || '',
                display_name: data.display_name || '',
                email: data.email || '',
                app_name: app.app_name || 'DNS Manager',
                // app_base_url wird gleich für Admins aus getAdminInfo nachgereicht (siehe useEffect oben)
                app_base_url: '',
                registration_enabled: !!app.registration_enabled,
                forgot_password_enabled: !!app.forgot_password_enabled,
                app_tagline: app.app_tagline || 'PowerDNS Admin Panel',
                app_creator: app.app_creator || 'Created by GemTec Games • Barra',
                app_logo_url: app.app_logo_url || '',
                phone: data.phone || '',
                company: data.company || '',
                street: data.street || '',
                postal_code: data.postal_code || '',
                city: data.city || '',
                country: data.country || '',
                date_of_birth: data.date_of_birth || '',
                preferred_language: data.preferred_language || 'de',
            })
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    async function loadServers() {
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
                phone: profileForm.phone || undefined,
                company: profileForm.company || undefined,
                street: profileForm.street || undefined,
                postal_code: profileForm.postal_code || undefined,
                city: profileForm.city || undefined,
                country: profileForm.country || undefined,
                date_of_birth: profileForm.date_of_birth || undefined,
                preferred_language: profileForm.preferred_language || undefined,
            })
            if (profile?.role === 'admin') {
                await api.updateAppInfo({
                    app_name: profileForm.app_name,
                    app_base_url: profileForm.app_base_url || undefined,
                    registration_enabled: profileForm.registration_enabled,
                    forgot_password_enabled: profileForm.forgot_password_enabled,
                    app_tagline: profileForm.app_tagline || undefined,
                    app_creator: profileForm.app_creator || undefined,
                    app_logo_url: profileForm.app_logo_url || undefined,
                })
            }
            // Trigger a minor refresh on the layout without full reload, by delaying localstorage
            setSuccess(t('settings.profileSaveSuccess'))
            if (result.user) {
                api.setUser(result.user)
                setProfile(result.user)
                if (result.user.preferred_language && result.user.preferred_language !== i18n.language) {
                    i18n.changeLanguage(result.user.preferred_language)
                }
            }
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
        if (passwordForm.new_password.length < 8) {
            setError(t('settings.passwordMinLength'))
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
            setSuccess(t('settings.passwordChangeSuccess'))
            setPasswordForm({ current_password: '', new_password: '', confirm_password: '' })
            setShowCurrentPw(false)
            setShowNewPw(false)
        } catch (err) {
            setError(err.message)
        } finally {
            setSavingPassword(false)
        }
    }

    async function handleUploadLogo(e) {
        const file = e.target.files?.[0]
        if (!file) return
        setUploadingLogo(true)
        setError('')
        try {
            const res = await api.uploadAppLogo(file)
            setProfileForm(prev => ({ ...prev, app_logo_url: res.app_logo_url || '' }))
            setSuccess(t('settings.logoUploadedSuccess'))
        } catch (err) {
            setError(err.message)
        } finally {
            setUploadingLogo(false)
        }
    }

    async function loadTemplates() {
        setLoadingTemplates(true)
        try {
            const data = await api.getTemplates()
            setTemplates(data.templates || [])
        } catch { /* ignore */ }
        finally { setLoadingTemplates(false) }
    }

    function openTemplateAdd() {
        setEditTemplateId(null)
        setTemplateForm({ ...emptyTemplate })
        setNewRecord({ ...emptyRecord })
        setTemplateModalError('')
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
        setTemplateModalError('')
        setShowTemplateForm(true)
    }

    function closeTemplateModal() {
        setShowTemplateForm(false)
        setTemplateModalError('')
    }

    function addRecordToTemplate() {
        if (!newRecord.content.trim()) return
        const rec = { ...newRecord }
        if (rec.type !== 'MX') rec.prio = null
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
        setTemplateModalError('')
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
            closeTemplateModal()
            loadTemplates()
        } catch (err) {
            setTemplateModalError(err.message)
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

    // ===== Captcha =====
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

    // ===== Welcome E-Mail =====
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
            app_name: profileForm.app_name || 'DNS Manager',
            login_url: (adminInfo?.app_base_url || profileForm.app_base_url || 'http://localhost:5380').replace(/\/$/, '') + '/login',
        }
        const replace = (s) => s.replace(/\{(\w+)\}/g, (_m, k) => (k in sample ? sample[k] : `{${k}}`))
        return { subject: replace(subject), body: replace(body) }
    }

    // ===== Server functions =====
    function openAdd() {
        setEditId(null)
        setForm({ name: '', display_name: '', url: '', api_key: '', description: '', allow_writes: true })
        setTestResult(null)
        setShowApiKey(false)
        setServerModalError('')
        setShowForm(true)
    }

    function openEdit(s) {
        setEditId(s.id)
        setForm({
            name: s.name,
            display_name: s.display_name || '',
            url: s.url,
            // API-Key wird beim Bearbeiten NIE vorausgefüllt. Leeres Feld = Backend behält den bestehenden Schlüssel.
            // Admin kann auf "Anzeigen" klicken, um den aktuellen Schlüssel auf Anforderung zu sehen (audit-loggt).
            api_key: '',
            description: s.description || '',
            allow_writes: s.allow_writes !== false,
        })
        setTestResult(null)
        setShowApiKey(false)
        setServerModalError('')
        setShowForm(true)
    }

    function closeServerModal() {
        setShowForm(false)
        setServerModalError('')
        setTestResult(null)
    }

    async function handleRevealApiKey() {
        if (!editId) return
        if (revealedKeys[editId]) {
            // Bereits geladen → einfach ins Form übernehmen und sichtbar machen
            setForm((prev) => ({ ...prev, api_key: revealedKeys[editId] }))
            setShowApiKey(true)
            return
        }
        setRevealingKey(true)
        try {
            const res = await api.revealServerApiKey(editId)
            const key = res?.api_key || ''
            setRevealedKeys((prev) => ({ ...prev, [editId]: key }))
            setForm((prev) => ({ ...prev, api_key: key }))
            setShowApiKey(true)
        } catch (err) {
            setError(err.message || 'API-Key konnte nicht geladen werden')
        } finally {
            setRevealingKey(false)
        }
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
        setServerModalError('')
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
            closeServerModal()
            loadServers()
        } catch (err) {
            setServerModalError(err.message)
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

    const allTabs = [
        { id: 'profile', labelKey: 'settings.profile', icon: UserCog },
        { id: 'servers', labelKey: 'settings.servers', icon: Server },
        { id: 'templates', labelKey: 'settings.templates', icon: Copy },
        { id: 'smtp', labelKey: 'settings.smtp', icon: Mail },
        { id: 'welcome', labelKey: 'settings.welcomeMail.tab', icon: UserPlus },
        { id: 'security', labelKey: 'settings.captcha.tab', icon: Shield },
        { id: 'updates', labelKey: 'settings.updates', icon: Download },
        { id: 'about', labelKey: 'settings.about', icon: Database },
    ]
    const tabs = profile?.role === 'admin' ? allTabs : allTabs.filter(tab => tab.id === 'profile' || tab.id === 'about')

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">{t('settings.title')}</h1>
                <p className="text-text-muted text-sm mt-1">{t('settings.subtitle')}</p>
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
            {/* overflow-x-auto + shrink-0/whitespace-nowrap auf den Buttons:
                auf Mobile scrollt die Tab-Leiste selbst, nicht die ganze Seite. */}
            <div className="flex gap-1 p-1 bg-bg-secondary rounded-xl border border-border overflow-x-auto">
                {tabs.map((tab) => (
                    <button
                        key={tab.id}
                        type="button"
                        onClick={() => setActiveTab(tab.id)}
                        className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all whitespace-nowrap shrink-0 ${activeTab === tab.id
                            ? 'bg-accent/20 text-accent-light'
                            : 'text-text-muted hover:text-text-primary hover:bg-bg-hover'
                            }`}
                    >
                        <tab.icon className="w-4 h-4 shrink-0" />
                        <span className="text-left">{t(tab.labelKey)}</span>
                        {tab.id === 'updates' && updateAvailable ? (
                            <span
                                className="w-2 h-2 rounded-full bg-red-500 shrink-0"
                                title={t('layout.newVersionDot')}
                                aria-hidden
                            />
                        ) : null}
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
                                <h2 className="text-lg font-semibold text-text-primary">{t('settings.profileEdit')}</h2>
                                <p className="text-sm text-text-muted">{t('settings.profileSubtitle')}</p>
                            </div>
                        </div>

                        <form onSubmit={handleSaveProfile} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.language')}</label>
                                <select
                                    value={profileForm.preferred_language || 'de'}
                                    onChange={async (e) => {
                                        const code = e.target.value
                                        setProfileForm({ ...profileForm, preferred_language: code })
                                        i18n.changeLanguage(code)
                                        try {
                                            await api.updateProfile({ preferred_language: code })
                                            if (profile) {
                                                const updated = { ...profile, preferred_language: code }
                                                setProfile(updated)
                                                api.setUser(updated)
                                            }
                                        } catch { setProfileForm(prev => ({ ...prev, preferred_language: i18n.language })) }
                                    }}
                                    className="w-full max-w-xs px-3 py-2 text-sm border border-border rounded-lg bg-bg-primary"
                                >
                                    {LANGUAGES.map(({ code, label, flag, wip }) => (
                                        <option key={code} value={code}>
                                            {flag ? `${flag} ` : ''}{label}{wip ? ' (WIP)' : ''}
                                        </option>
                                    ))}
                                </select>
                                <p className="text-xs text-text-muted mt-1">{t('settings.languageHint')}</p>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        {t('settings.username')}
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
                                    <p className="text-xs text-text-muted mt-1">{t('settings.usernameHint')}</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        {t('settings.displayName')}
                                    </label>
                                    <input
                                        type="text"
                                        value={profileForm.display_name}
                                        onChange={e => setProfileForm({ ...profileForm, display_name: e.target.value })}
                                        placeholder={t('settings.displayNamePlaceholder')}
                                        className="w-full px-3 py-2 text-sm"
                                    />
                                    <p className="text-xs text-text-muted mt-1">{t('settings.displayNameHint')}</p>
                                </div>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">
                                    {t('settings.email')}
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

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-border">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.phone')}</label>
                                    <input type="text" value={profileForm.phone} onChange={e => setProfileForm({ ...profileForm, phone: e.target.value })}
                                        placeholder="+49 123 456789" className="w-full px-3 py-2 text-sm" maxLength={25}
                                        pattern="(^$|.*[0-9].*)" title={t('settings.phoneTitle')} />
                                    <p className="text-xs text-text-muted mt-1">{t('settings.phoneHint')}</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.company')}</label>
                                    <input type="text" value={profileForm.company} onChange={e => setProfileForm({ ...profileForm, company: e.target.value })}
                                        placeholder={t('settings.companyPlaceholder')} className="w-full px-3 py-2 text-sm" maxLength={255} />
                                </div>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.street')}</label>
                                <input type="text" value={profileForm.street} onChange={e => setProfileForm({ ...profileForm, street: e.target.value })}
                                    placeholder={t('settings.streetPlaceholder')} className="w-full px-3 py-2 text-sm" maxLength={255} />
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.postalCode')}</label>
                                    <input type="text" value={profileForm.postal_code} onChange={e => setProfileForm({ ...profileForm, postal_code: e.target.value })}
                                        placeholder={t('settings.postalCodePlaceholder')} className="w-full px-3 py-2 text-sm" maxLength={20} />
                                    <p className="text-xs text-text-muted mt-1">{t('settings.postalCodeHint')}</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.city')}</label>
                                    <input type="text" value={profileForm.city} onChange={e => setProfileForm({ ...profileForm, city: e.target.value })}
                                        placeholder={t('settings.cityPlaceholder')} className="w-full px-3 py-2 text-sm" maxLength={100} />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.country')}</label>
                                    <input type="text" value={profileForm.country} onChange={e => setProfileForm({ ...profileForm, country: e.target.value })}
                                        placeholder={t('settings.countryPlaceholder')} className="w-full px-3 py-2 text-sm" maxLength={100} />
                                </div>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.dateOfBirth')}</label>
                                <input type="date" value={profileForm.date_of_birth} onChange={e => setProfileForm({ ...profileForm, date_of_birth: e.target.value })}
                                    className="w-full px-3 py-2 text-sm" />
                            </div>

                            {profile?.role !== 'admin' && profile?.zones?.length !== undefined && (
                                <div className="pt-2 border-t border-border">
                                    <p className="text-sm font-medium text-text-secondary mb-2">{t('settings.myZones')}</p>
                                    <p className="text-xs text-text-muted mb-2">{t('settings.myZonesHint')}</p>
                                    <ul className="text-sm text-text-primary list-disc list-inside">
                                        {profile.zones.length === 0 ? (
                                            <li className="text-text-muted">{t('settings.noZonesAssigned')}</li>
                                        ) : (
                                            profile.zones.map(z => <li key={z}>{z}</li>)
                                        )}
                                    </ul>
                                </div>
                            )}

                            {profile?.role === 'admin' && (
                                <>
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-1">
                                            {t('settings.systemTitle')}
                                        </label>
                                        <input
                                            type="text"
                                            value={profileForm.app_name}
                                            onChange={e => setProfileForm({ ...profileForm, app_name: e.target.value })}
                                            placeholder={t('settings.systemTitlePlaceholder')}
                                            className="w-full px-3 py-2 text-sm"
                                            required
                                        />
                                        <p className="text-xs text-text-muted mt-1">{t('settings.systemTitleHint')}</p>
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-text-secondary mb-1">
                                            {t('settings.appBaseUrl')}
                                        </label>
                                        <input
                                            type="url"
                                            value={profileForm.app_base_url}
                                            onChange={e => setProfileForm({ ...profileForm, app_base_url: e.target.value })}
                                            placeholder={t('settings.appBaseUrlPlaceholder')}
                                            className="w-full px-3 py-2 text-sm"
                                        />
                                        <p className="text-xs text-text-muted mt-1">{t('settings.appBaseUrlHint')}</p>
                                    </div>
                                    <div className="space-y-3 pt-2 border-t border-border">
                                        <p className="text-sm font-medium text-text-secondary">{t('settings.loginRegistration')}</p>
                                        <label className="flex items-center gap-3 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={!!profileForm.registration_enabled}
                                                onChange={e => setProfileForm({ ...profileForm, registration_enabled: e.target.checked })}
                                                className="rounded border-border"
                                            />
                                            <span className="text-sm text-text-primary">{t('settings.allowRegistration')}</span>
                                        </label>
                                        <p className="text-xs text-text-muted ml-6">{t('settings.allowRegistrationHint')}</p>
                                        <label className="flex items-center gap-3 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={!!profileForm.forgot_password_enabled}
                                                onChange={e => setProfileForm({ ...profileForm, forgot_password_enabled: e.target.checked })}
                                                className="rounded border-border"
                                            />
                                            <span className="text-sm text-text-primary">{t('settings.allowForgotPassword')}</span>
                                        </label>
                                        <p className="text-xs text-text-muted ml-6">{t('settings.allowForgotPasswordHint')}</p>
                                    </div>
                                    <div className="space-y-3 pt-2 border-t border-border">
                                        <p className="text-sm font-medium text-text-secondary">{t('settings.brandingTitle')}</p>
                                        <div>
                                            <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.footerText')}</label>
                                            <input
                                                type="text"
                                                value={profileForm.app_tagline}
                                                onChange={e => setProfileForm({ ...profileForm, app_tagline: e.target.value })}
                                                placeholder={t('settings.taglinePlaceholder')}
                                                className="w-full px-3 py-2 text-sm"
                                                maxLength={200}
                                            />
                                            <p className="text-xs text-text-muted mt-1">{t('settings.footerTextHint')}</p>
                                        </div>
                                        <div>
                                            <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.creatorText')}</label>
                                            <input
                                                type="text"
                                                value={profileForm.app_creator}
                                                onChange={e => setProfileForm({ ...profileForm, app_creator: e.target.value })}
                                                placeholder={t('settings.creatorPlaceholder')}
                                                className="w-full px-3 py-2 text-sm"
                                                maxLength={200}
                                            />
                                            <p className="text-xs text-text-muted mt-1">{t('settings.creatorTextHint')}</p>
                                        </div>
                                        <div>
                                            <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.logoUploadLabel')}</label>
                                            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={handleUploadLogo} className="w-full text-sm" />
                                            {uploadingLogo && <p className="text-xs text-text-muted mt-1">{t('settings.logoUploading')}</p>}
                                            {profileForm.app_logo_url && (
                                                <div className="mt-2 flex items-center gap-3">
                                                    <img src={profileForm.app_logo_url} alt="App logo" className="w-10 h-10 rounded-lg object-contain bg-bg-secondary border border-border" />
                                                    <button
                                                        type="button"
                                                        onClick={() => setProfileForm({ ...profileForm, app_logo_url: '' })}
                                                        className="text-xs text-danger hover:underline"
                                                    >
                                                        {t('settings.logoRemoveOnSave')}
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                        <p className="text-xs text-text-muted">{t('settings.brandingTransparentHint')}</p>
                                    </div>
                                </>
                            )}

                            {profile && (
                                <div className="flex items-center gap-4 pt-2 text-xs text-text-muted">
                                    <span>{t('settings.role')}: <span className="text-accent-light font-medium">{profile.role === 'admin' ? t('settings.administrator') : t('layout.user')}</span></span>
                                    {profile.created_at && <span>{t('settings.created')}: {new Date(profile.created_at).toLocaleDateString()}</span>}
                                    {profile.last_login && <span>{t('settings.lastLogin')}: {new Date(profile.last_login).toLocaleString()}</span>}
                                </div>
                            )}

                            <div className="flex justify-end pt-2 border-t border-border">
                                <button
                                    type="submit"
                                    disabled={savingProfile}
                                    className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 transition-all"
                                >
                                    {savingProfile && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {t('settings.saveProfile')}
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
                                <h2 className="text-lg font-semibold text-text-primary">{t('settings.passwordChange')}</h2>
                                <p className="text-sm text-text-muted">{t('settings.passwordChangeHint')}</p>
                            </div>
                        </div>

                        <form onSubmit={handleChangePassword} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">
                                    {t('settings.currentPassword')}
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
                                        {t('settings.newPassword')}
                                    </label>
                                    <div className="relative">
                                        <input
                                            type={showNewPw ? 'text' : 'password'}
                                            value={passwordForm.new_password}
                                            onChange={e => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                                            placeholder="••••••••"
                                            className="w-full px-3 py-2 pr-10 text-sm"
                                            required
                                            minLength={8}
                                            maxLength={128}
                                        />
                                        <button type="button" onClick={() => setShowNewPw(!showNewPw)}
                                            className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                            {showNewPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                        </button>
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">
                                        {t('settings.newPasswordConfirm')}
                                    </label>
                                    <input
                                        type={showNewPw ? 'text' : 'password'}
                                        value={passwordForm.confirm_password}
                                        onChange={e => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })}
                                        placeholder="••••••••"
                                        className="w-full px-3 py-2 text-sm"
                                        required
                                        minLength={8}
                                        maxLength={128}
                                    />
                                </div>
                            </div>

                            {passwordForm.new_password && passwordForm.confirm_password && passwordForm.new_password !== passwordForm.confirm_password && (
                                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm flex items-center gap-2">
                                    <AlertCircle className="w-4 h-4 shrink-0" />
                                    {t('settings.passwordsDoNotMatch')}
                                </div>
                            )}

                            <div className="flex justify-end pt-2 border-t border-border">
                                <button
                                    type="submit"
                                    disabled={savingPassword || (passwordForm.new_password !== passwordForm.confirm_password)}
                                    className="px-5 py-2 bg-gradient-to-r from-warning/80 to-orange-600 hover:from-warning hover:to-orange-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 transition-all"
                                >
                                    {savingPassword && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {t('settings.changePasswordButton')}
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
            )}

            {/* =================== WELCOME-MAIL TAB =================== */}
            {activeTab === 'welcome' && profile?.role === 'admin' && (
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
            )}

            {/* =================== SECURITY (CAPTCHA) TAB =================== */}
            {activeTab === 'security' && profile?.role === 'admin' && (
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
            )}

            {/* =================== UPDATES TAB =================== */}
            {activeTab === 'updates' && (
                <div className="space-y-6">
                    {(() => {
                        const isUpToDate = !!(latestVersion && currentVersion && compareSemver(latestVersion, currentVersion) <= 0)
                        const hasUpdate = !!(latestVersion && currentVersion && compareSemver(latestVersion, currentVersion) > 0)
                        return (
                            <div className={`p-4 rounded-xl border text-sm ${
                                hasUpdate
                                    ? 'border-amber-500/40 bg-amber-500/10 text-text-secondary'
                                    : isUpToDate
                                        ? 'border-success/40 bg-success/10 text-text-secondary'
                                        : 'border-border bg-bg-hover/40 text-text-muted'
                            }`}>
                                <div className="flex items-center justify-between gap-4 flex-wrap">
                                    <div className="flex-1 min-w-0">
                                        {hasUpdate ? (
                                            <>
                                                <p className="font-medium text-amber-200 mb-1">{t('settingsMore.newVersionBannerTitle')}</p>
                                                <p>{t('settingsMore.newVersionBannerBody', { current: currentVersion, latest: latestVersion })}</p>
                                            </>
                                        ) : isUpToDate ? (
                                            <>
                                                <p className="font-medium text-success mb-1">{t('settingsMore.upToDateTitle')}</p>
                                                <p>{t('settingsMore.upToDateBody', { current: currentVersion })}</p>
                                            </>
                                        ) : (
                                            <p>{t('settingsMore.versionUnknown')}</p>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-3 text-xs">
                                        <div className="flex items-center gap-1.5">
                                            <span className="text-text-muted">{t('settingsMore.versionInstalled')}:</span>
                                            <code className="font-mono text-text-primary">{currentVersion || '–'}</code>
                                        </div>
                                        <div className="flex items-center gap-1.5">
                                            <span className="text-text-muted">{t('settingsMore.versionLatest')}:</span>
                                            <code className="font-mono text-text-primary">{latestVersion || '–'}</code>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        )
                    })()}

                    <div className="glass-card p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                                <Download className="w-5 h-5 text-success" />
                            </div>
                            <div>
                                <h2 className="text-lg font-semibold text-text-primary">{t('settingsMore.updateTitle')}</h2>
                                <p className="text-sm text-text-muted">{t('settingsMore.updateSubtitle')}</p>
                            </div>
                        </div>

                        <div className="p-5 rounded-xl bg-bg-primary border border-border mb-8">
                            <h3 className="text-sm font-semibold text-text-primary mb-3 text-accent-light">{t('settingsMore.updateHowTitle')}</h3>
                            <p className="text-sm text-text-secondary mb-5 leading-relaxed">
                                {t('settingsMore.updateSecurityPara')}
                                <br /><br />
                                {t('settingsMore.updateTerminalPara')}
                            </p>

                            {(() => {
                                const installPath = adminInfo?.install_path || ''
                                const updateCmd = installPath
                                    ? `cd ${installPath} && ./update.sh`
                                    : `cd <DEIN-INSTALLATIONS-PFAD> && ./update.sh`
                                const onCopy = async () => {
                                    try {
                                        if (navigator.clipboard?.writeText) {
                                            await navigator.clipboard.writeText(updateCmd)
                                        } else {
                                            const ta = document.createElement('textarea')
                                            ta.value = updateCmd
                                            ta.style.position = 'fixed'
                                            ta.style.opacity = '0'
                                            document.body.appendChild(ta)
                                            ta.select()
                                            document.execCommand('copy')
                                            document.body.removeChild(ta)
                                        }
                                        setUpdateCmdCopied(true)
                                    } catch { /* clipboard blockiert – egal */ }
                                }
                                return (
                                    <div className="relative">
                                        <div className="absolute inset-y-0 left-0 bg-accent w-1 rounded-l-lg"></div>
                                        <pre className="bg-bg-hover text-text-primary p-4 pr-28 rounded-r-lg rounded-l-sm text-sm font-mono overflow-x-auto pl-6 border border-border/50 border-l-0">
                                            <span className="text-text-muted"># {t('settingsMore.updateStep1Comment')}</span>{'\n'}
                                            <span className="text-accent-light font-medium">cd</span> {installPath || t('settingsMore.updatePathPlaceholder')}{'\n\n'}
                                            <span className="text-text-muted"># {t('settingsMore.updateStep2Comment')}</span>{'\n'}
                                            <span className="text-accent-light font-medium">./update.sh</span>
                                        </pre>
                                        <button
                                            type="button"
                                            onClick={onCopy}
                                            className="absolute top-2 right-2 px-2.5 py-1.5 text-xs rounded-md bg-bg-primary border border-border text-text-secondary hover:text-text-primary hover:border-accent/50 flex items-center gap-1.5 transition-colors"
                                            title={t('common.copy')}
                                        >
                                            {updateCmdCopied ? (
                                                <>
                                                    <Check className="w-3.5 h-3.5 text-success" />
                                                    {t('common.copied')}
                                                </>
                                            ) : (
                                                <>
                                                    <Copy className="w-3.5 h-3.5" />
                                                    {t('common.copy')}
                                                </>
                                            )}
                                        </button>
                                    </div>
                                )
                            })()}

                            <p className="text-xs text-text-muted mt-4">
                                💡 {t('settingsMore.updatesScriptHint')}
                            </p>
                            {!adminInfo?.install_path && (
                                <p className="text-xs text-amber-400/90 mt-2">
                                    {t('settingsMore.updatePathMissingHint')}
                                </p>
                            )}
                        </div>

                        {/* Commits von GitHub */}
                        <div>
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2">
                                    <GitCommit className="w-4 h-4" /> {t('settingsMore.lastChanges')}
                                </h3>
                                <button onClick={loadCommits} disabled={loadingCommits} className="text-xs text-text-muted hover:text-text-primary flex items-center gap-1">
                                    <RefreshCw className={`w-3 h-3 ${loadingCommits ? 'animate-spin' : ''}`} /> {loadingCommits ? t('settings.loadingDot') : t('settings.reload')}
                                </button>
                            </div>

                            {commitError ? (
                                <div className="p-4 rounded-lg bg-bg-hover border border-border text-text-muted text-sm text-center flex flex-col items-center gap-2">
                                    <Lock className="w-5 h-5 opacity-50" />
                                    {t('settings.updatesPrivateRepo')}
                                </div>
                            ) : commits.length === 0 && !loadingCommits ? (
                                <p className="text-sm text-text-muted">{t('settingsMore.noChangesFound')}</p>
                            ) : (
                                <div className="space-y-3">
                                    {commits.map((c) => (
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
                                                    <span>{t('settingsMore.by')} <strong className="text-text-secondary">{c.commit.author.name}</strong></span>
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
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <h2 className="text-lg font-semibold text-text-primary">{t('settingsMore.powerDnsServers')}</h2>
                        <div className="flex gap-2 flex-wrap">
                            <button onClick={loadServers} className="flex items-center gap-2 px-3 py-2 text-sm text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-colors border border-border">
                                <RefreshCw className="w-4 h-4" /> {t('settingsMore.refresh')}
                            </button>
                            <button onClick={openAdd} className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all">
                                <Plus className="w-4 h-4" /> {t('settingsMore.addServer')}
                            </button>
                        </div>
                    </div>

                    {/* Info-Box: DNS Server werden in der Datenbank gespeichert */}
                    <div className="p-4 rounded-xl bg-accent/5 border border-accent/20">
                        <p className="text-sm text-text-secondary">
                            <strong className="text-accent-light">💡 {t('common.hint')}:</strong> {t('settings.serversHint')}
                            {t('settingsMore.serversManagedHint')}
                        </p>
                    </div>

                    {servers.length === 0 ? (
                        <div className="glass-card p-12 text-center">
                            <Server className="w-16 h-16 mx-auto mb-4 text-text-muted opacity-30" />
                            <h3 className="text-lg font-semibold text-text-primary mb-2">{t('settingsMore.noServerConfigured')}</h3>
                            <p className="text-sm text-text-muted mb-4">
                                {t('settingsMore.noServerHint')}
                            </p>
                            <button onClick={openAdd} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg font-medium text-sm">
                                <Plus className="w-4 h-4 inline mr-2" /> {t('settingsMore.addFirstServer')}
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
                                                <button type="button" onClick={() => toggleAllowWrites(s)} className={`text-xs px-2 py-0.5 rounded-full border cursor-pointer hover:opacity-80 transition-opacity ${s.allow_writes !== false ? 'bg-success/10 text-success border-success/30' : 'bg-bg-hover text-text-muted border-border'}`} title={s.allow_writes !== false ? t('settings.allowWritesTitleOn') : t('settings.allowWritesTitleOff')}>
                                                    {s.allow_writes !== false ? t('settings.allowWritesYes') : t('settings.allowWritesNo')}
                                                </button>
                                            </div>
                                            <p className="text-sm text-text-muted font-mono mt-1">{s.url}</p>
                                            <div className="flex items-center gap-4 mt-2 text-xs text-text-muted">
                                                {s.version && <span>Version: <span className="text-text-secondary">{s.version}</span></span>}
                                                {s.zone_count != null && <span>{t('settings.zonesCount')}: <span className="text-text-secondary">{s.zone_count}</span></span>}
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
                    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                            <h2 className="text-lg font-semibold text-text-primary">{t('settings.templatesTitle')}</h2>
                            <p className="text-sm text-text-muted">{t('settings.templatesSubtitle')}</p>
                        </div>
                        <button onClick={openTemplateAdd} className="self-start sm:self-auto flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all shrink-0">
                            <Plus className="w-4 h-4" /> {t('templates.newTemplate')}
                        </button>
                    </div>

                    {loadingTemplates ? (
                        <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
                    ) : templates.length === 0 ? (
                        <div className="glass-card p-12 text-center">
                            <Copy className="w-16 h-16 mx-auto mb-4 text-text-muted opacity-30" />
                            <h3 className="text-lg font-semibold text-text-primary mb-2">{t('templates.noTemplatesYet')}</h3>
                            <p className="text-sm text-text-muted mb-4">{t('templates.createFirstTemplateHint')}</p>
                            <button onClick={openTemplateAdd} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg font-medium text-sm">
                                <Plus className="w-4 h-4 inline mr-2" /> {t('templates.firstTemplateCreate')}
                            </button>
                        </div>
                    ) : (
                        <div className="space-y-3">
                            {templates.map(tmpl => (
                                <div key={tmpl.id} className="glass-card p-5">
                                    <div className="flex items-start gap-4">
                                        <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${tmpl.is_default ? 'bg-warning/20' : 'bg-accent/20'}`}>
                                            {tmpl.is_default ? <Star className="w-6 h-6 text-warning" /> : <Copy className="w-6 h-6 text-accent-light" />}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <h3 className="font-semibold text-text-primary">{tmpl.name}</h3>
                                                {tmpl.is_default && <span className="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">{t('templates.defaultLabel')}</span>}
                                            </div>
                                            {tmpl.description && <p className="text-sm text-text-muted mt-0.5">{tmpl.description}</p>}
                                            <div className="flex items-center gap-4 mt-2 text-xs text-text-muted flex-wrap">
                                                <span>NS: <span className="text-text-secondary">{(tmpl.nameservers || []).join(', ') || '–'}</span></span>
                                                <span>Typ: <span className="text-text-secondary">{tmpl.kind}</span></span>
                                                <span>TTL: <span className="text-text-secondary">{tmpl.default_ttl}s</span></span>
                                                <span>Records: <span className="text-text-secondary">{(tmpl.records || []).length}</span></span>
                                            </div>
                                            {(tmpl.records || []).length > 0 && (
                                                <div className="mt-2 flex flex-wrap gap-1">
                                                    {tmpl.records.map((r, i) => (
                                                        <span key={i} className="text-xs px-2 py-0.5 rounded bg-bg-hover border border-border text-text-secondary font-mono">
                                                            {r.name} {r.type} {r.prio ? r.prio + ' ' : ''}{r.content}
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <div className="flex items-center gap-1 shrink-0">
                                            <button onClick={() => openTemplateEdit(tmpl)} className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors" title={t('templates.editTitle')}>
                                                <Pencil className="w-4 h-4" />
                                            </button>
                                            <button onClick={() => handleDeleteTemplate(tmpl.id, tmpl.name)} className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors" title={t('templates.deleteTitle')}>
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
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
                    onClick={() => { if (!savingTemplate) closeTemplateModal() }}
                >
                    <div className="glass-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-lg font-bold text-text-primary">
                                {editTemplateId ? t('templates.editTemplate') : t('templates.createNewTemplate')}
                            </h2>
                            <button onClick={closeTemplateModal} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        {templateModalError && (
                            <div className="mb-4 p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3">
                                <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                                <p className="text-sm flex-1 break-words">{templateModalError}</p>
                                <button type="button" onClick={() => setTemplateModalError('')} className="text-xs hover:underline shrink-0" aria-label={t('common.close')}>×</button>
                            </div>
                        )}

                        <form onSubmit={handleSaveTemplate} className="space-y-4">
                            {/* Name & Beschreibung */}
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('templates.templateName')} *</label>
                                    <input type="text" value={templateForm.name} onChange={e => setTemplateForm({ ...templateForm, name: e.target.value })}
                                        placeholder={t('templates.templateNamePlaceholder')} className="w-full px-3 py-2 text-sm" required />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('templates.description')}</label>
                                    <input type="text" value={templateForm.description} onChange={e => setTemplateForm({ ...templateForm, description: e.target.value })}
                                        placeholder={t('templates.descriptionPlaceholder')} className="w-full px-3 py-2 text-sm" />
                                </div>
                            </div>

                            {/* Nameservers */}
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('templates.nameservers')}</label>
                                <textarea value={templateForm.nameservers} onChange={e => setTemplateForm({ ...templateForm, nameservers: e.target.value })}
                                    placeholder="ns1.example.com., ns2.example.com." className="w-full px-3 py-2 text-sm min-h-[60px]" />
                                <p className="text-xs text-text-muted mt-1">{t('templates.nameserversHint')}</p>
                            </div>

                            {/* Kind, SOA-EDIT-API, TTL */}
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('dashboard.type')}</label>
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
                                        <option value={60}>{t('templates.ttl1Min')}</option>
                                        <option value={300}>{t('templates.ttl5Min')}</option>
                                        <option value={3600}>{t('templates.ttl1Hour')}</option>
                                        <option value={14400}>{t('templates.ttl4Hours')}</option>
                                        <option value={86400}>{t('templates.ttl1Day')}</option>
                                    </select>
                                </div>
                            </div>

                            {/* Standard-Vorlage Checkbox */}
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input type="checkbox" checked={templateForm.is_default} onChange={e => setTemplateForm({ ...templateForm, is_default: e.target.checked })} className="w-4 h-4 rounded" />
                                <span className="text-sm text-text-secondary">{t('settings.defaultTemplate')}</span>
                            </label>

                            {/* Records */}
                            <div className="border-t border-border pt-4">
                                <h3 className="text-sm font-semibold text-text-primary mb-3">{t('templates.standardDnsRecords')}</h3>
                                <p className="text-xs text-text-muted mb-3">{t('settings.templateRecordsHint')}</p>

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

                                {/* Add new record row – flex-wrap, genug Platz für Prio (nur MX) */}
                                <div className="space-y-2">
                                    <div className="flex flex-wrap items-end gap-3">
                                        <div className="min-w-[7rem] w-[28%] max-w-[10rem] shrink-0">
                                            <label className="block text-xs font-medium text-text-secondary mb-1">{t('zoneDetail.name')}</label>
                                            <input type="text" value={newRecord.name} onChange={e => setNewRecord({ ...newRecord, name: e.target.value })}
                                                placeholder="@" className="w-full h-9 px-2.5 text-xs rounded-lg border border-border bg-bg-primary" />
                                        </div>
                                        <div className="min-w-[5.5rem] w-24 shrink-0">
                                            <label className="block text-xs font-medium text-text-secondary mb-1">{t('dashboard.type')}</label>
                                            <select value={newRecord.type} onChange={e => setNewRecord({ ...newRecord, type: e.target.value })} className="w-full h-9 px-2 text-xs rounded-lg border border-border bg-bg-primary">
                                                {ALL_RECORD_TYPE_KEYS.map((tp) => (
                                                    <option key={tp} value={tp}>{tp}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="min-w-[12rem] flex-1 basis-[min(100%,18rem)]">
                                            <label className="block text-xs font-medium text-text-secondary mb-1">
                                                {t(`templates.recordLabel${newRecord.type}`, { defaultValue: t('templates.recordLabelRdata') })}
                                            </label>
                                            <input type="text" value={newRecord.content} onChange={e => setNewRecord({ ...newRecord, content: e.target.value })}
                                                placeholder={TEMPLATE_CONTENT_PLACEHOLDERS[newRecord.type] || '…'}
                                                className="w-full min-w-0 h-9 px-2.5 text-xs rounded-lg border border-border bg-bg-primary" />
                                        </div>
                                        <div className="min-w-[5.5rem] w-24 shrink-0">
                                            <label className="block text-xs font-medium text-text-secondary mb-1">{t('zoneDetail.ttl')}</label>
                                            <input type="number" value={newRecord.ttl} onChange={e => setNewRecord({ ...newRecord, ttl: parseInt(e.target.value, 10) || 3600 })}
                                                className="w-full h-9 px-2.5 text-xs rounded-lg border border-border bg-bg-primary" />
                                        </div>
                                        {newRecord.type === 'MX' && (
                                            <div className="min-w-[6.5rem] w-28 shrink-0">
                                                <label className="block text-xs font-medium text-text-secondary mb-1">{t('zoneDetail.fieldPriority')}</label>
                                                <input type="number" value={newRecord.prio ?? ''} onChange={e => setNewRecord({ ...newRecord, prio: parseInt(e.target.value, 10) || 0 })}
                                                    placeholder="10" className="w-full h-9 px-2.5 text-xs rounded-lg border border-border bg-bg-primary" />
                                            </div>
                                        )}
                                        <div className="min-w-[8.5rem] shrink-0 pb-px">
                                            <label className="block text-xs font-medium text-text-secondary mb-1 opacity-0 pointer-events-none select-none" aria-hidden="true">.</label>
                                            <button type="button" onClick={addRecordToTemplate}
                                                className="w-full h-9 px-3 text-xs font-medium border border-accent/40 text-accent-light rounded-lg hover:bg-accent/10 flex items-center justify-center gap-1">
                                                <Plus className="w-3.5 h-3.5" /> {t('settings.add')}
                                            </button>
                                        </div>
                                    </div>
                                    <DnsRecordTypeHint recordType={newRecord.type} compact />
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-2 border-t border-border">
                                <button type="button" onClick={closeTemplateModal} disabled={savingTemplate} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">
                                    {t('common.cancel')}
                                </button>
                                <button type="submit" disabled={savingTemplate} className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {savingTemplate && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {editTemplateId ? t('templates.saveButton') : t('templates.createButton')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* =================== ABOUT TAB =================== */}
            {activeTab === 'about' && (
                <div className="glass-card p-5">
                    <h2 className="text-lg font-semibold text-text-primary mb-4">{t('settingsMore.aboutTitle')}</h2>
                    <div className="space-y-3">
                        {[
                            [t('settingsMore.version'), appInfo?.app_version || '–'],
                            [t('settingsMore.frontend'), 'React + Vite + Tailwind CSS'],
                            [t('settingsMore.backend'), 'Python FastAPI'],
                            [t('settingsMore.database'), 'MariaDB 11'],
                            [t('settingsMore.dnsEngine'), 'PowerDNS Authoritative'],
                            [t('settingsMore.auth'), 'JWT (Bearer Token)'],
                        ].map(([k, v]) => (
                            <div key={k} className="flex justify-between p-3 bg-bg-primary rounded-lg border border-border">
                                <span className="text-sm text-text-muted">{k}</span>
                                <span className="text-sm font-medium text-text-primary">{v}</span>
                            </div>
                        ))}
                    </div>
                    <div className="mt-4 p-4 bg-accent/5 rounded-xl border border-accent/20">
                        <p className="text-sm text-text-secondary">
                            {t('settings.aboutText')}
                            {t('settingsMore.aboutOpenSource')} 🚀
                        </p>
                    </div>
                </div>
            )}

            {/* =================== ADD/EDIT SERVER MODAL =================== */}
            {showForm && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
                    onClick={() => { if (!saving) closeServerModal() }}
                >
                    <div className="glass-card p-6 w-full max-w-xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-lg font-bold text-text-primary">
                                {editId ? t('settingsMore.editServer') : t('settingsMore.addNewServer')}
                            </h2>
                            <button onClick={closeServerModal} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        {serverModalError && (
                            <div className="mb-4 p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3">
                                <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">
                                        {editId ? t('settingsMore.updateErrorTitle') : t('settingsMore.createErrorTitle')}
                                    </p>
                                    <p className="text-sm mt-1 break-words">{serverModalError}</p>
                                </div>
                                <button type="button" onClick={() => setServerModalError('')} className="text-xs hover:underline shrink-0" aria-label={t('common.close')}>×</button>
                            </div>
                        )}

                        <form onSubmit={handleSave} className="space-y-4">
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.serverName')} *</label>
                                    <input
                                        type="text" value={form.name}
                                        onChange={e => setForm({ ...form, name: e.target.value })}
                                        placeholder="server1" className="w-full px-3 py-2 text-sm"
                                        required disabled={!!editId} minLength={1}
                                    />
                                    <p className="text-xs text-text-muted mt-0.5">{t('settingsMore.serverNameHint')}</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.serverDisplayName')}</label>
                                    <input
                                        type="text" value={form.display_name}
                                        onChange={e => setForm({ ...form, display_name: e.target.value })}
                                        placeholder={t('settings.nameserverPlaceholder')} className="w-full px-3 py-2 text-sm"
                                    />
                                </div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.powerDnsApiUrl')} *</label>
                                <input
                                    type="url" value={form.url}
                                    onChange={e => setForm({ ...form, url: e.target.value })}
                                    placeholder={t('settings.pdnsApiUrlPlaceholder')} className="w-full px-3 py-2 text-sm"
                                    required
                                />
                                <p className="text-xs text-text-muted mt-0.5">{t('settingsMore.powerDnsApiUrlHint')}</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">
                                    {t('settingsMore.apiKey')}{!editId ? ' *' : ''}
                                </label>
                                <div className="relative">
                                    <input
                                        type={showApiKey ? 'text' : 'password'} value={form.api_key}
                                        onChange={e => setForm({ ...form, api_key: e.target.value })}
                                        placeholder={editId ? t('settings.apiKeyKeepPlaceholder') : t('settings.apiKeyPlaceholder')}
                                        className="w-full px-3 py-2 pr-20 text-sm"
                                        required={!editId}
                                    />
                                    <button type="button" onClick={() => setShowApiKey(!showApiKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                        {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                {editId && (
                                    <button
                                        type="button"
                                        onClick={handleRevealApiKey}
                                        disabled={revealingKey}
                                        className="mt-1 text-xs text-accent-light hover:text-accent flex items-center gap-1"
                                    >
                                        {revealingKey ? <Loader2 className="w-3 h-3 animate-spin" /> : <Eye className="w-3 h-3" />}
                                        {t('settings.revealExistingApiKey')}
                                    </button>
                                )}
                                <p className="text-xs text-text-muted mt-0.5">{t('settingsMore.apiKeyHint')}</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.description')}</label>
                                <input
                                    type="text" value={form.description}
                                    onChange={e => setForm({ ...form, description: e.target.value })}
                                    placeholder={t('settings.serverDescriptionPlaceholder')} className="w-full px-3 py-2 text-sm"
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
                                    <span className="text-sm font-medium text-text-primary">{t('settingsMore.saveOnThisServer')}</span>
                                    <p className="text-xs text-text-muted mt-0.5">{t('settings.writeToServerHint')}</p>
                                </div>
                            </label>

                            {/* Test Connection */}
                            <div className="border-t border-border pt-4">
                                <button
                                    type="button" onClick={handleTest} disabled={testing || !form.url || !form.api_key}
                                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium border border-accent/40 text-accent-light rounded-lg hover:bg-accent/10 disabled:opacity-50 transition-all"
                                >
                                    {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                                    {t('settingsMore.testConnection')}
                                </button>

                                {testResult && (
                                    <div className={`mt-3 p-3 rounded-lg text-sm ${testResult.success
                                        ? 'bg-success/10 border border-success/30 text-success'
                                        : 'bg-danger/10 border border-danger/30 text-danger'
                                        }`}>
                                        {testResult.success ? (
                                            <div>
                                                <div className="flex items-center gap-2 font-medium mb-1">
                                                    <CheckCircle2 className="w-4 h-4" /> {t('settingsMore.connectionSuccess')}
                                                </div>
                                                <div className="text-xs space-y-0.5 text-text-secondary">
                                                    <p>{t('settingsMore.version')}: {testResult.server_info.version}</p>
                                                    <p>{t('dashboard.type')}: {testResult.server_info.daemon_type}</p>
                                                    <p>{t('settings.zonesCount')}: {testResult.server_info.zone_count}</p>
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
                                <button type="button" onClick={closeServerModal} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">
                                    {t('common.cancel')}
                                </button>
                                <button type="submit" disabled={saving} className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {editId ? t('common.save') : t('settings.add')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
