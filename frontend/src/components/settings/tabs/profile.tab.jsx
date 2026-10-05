import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, AlertCircle, AlertTriangle, Eye, EyeOff, UserCog, Lock, Mail, User } from 'lucide-react'
import api from '../../../api'
import { LANGUAGES, applyLanguage } from '../../../i18n'
import { buildAppInfoPayload, buildProfilePayload } from '../../../lib/settingsForms.js'
import { useDateFormat } from '../../../lib/useDateFormat'
import PageSpinner from '../../PageSpinner'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'profile', order: 10, labelKey: 'settings.profile', icon: UserCog, adminOnly: false }

// Tab "Profil": eigenes Profil, Sprache, Branding/Systemeinstellungen (nur Admin), Passwort ändern.
// F8 (Welle 1): keine Sprachaenderung beim Laden, Sprachwechsel mit Rollback (A07, f92); App-Info-Ladefehler
// sperrt die Admin-Felder statt Fallbacks zu speichern (D01); Felder leerbar (D02, D04); erst Profil, dann
// App-Info speichern (D03); Datumsformat der UI-Sprache (A12).
export default function ProfileTab() {
    const { t, i18n } = useTranslation()
    const { fmtDate, fmtDateTime } = useDateFormat()
    const { profile, setProfile, isAdmin, adminInfo, notify } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    // Ohne Profil (getMe fehlgeschlagen) bleibt das Formular leer wie in 2.4.1
    const [formReady, setFormReady] = useState(!profile)
    // App-Info (Systemtitel, Registrierung, Branding) geladen? Sonst Admin-Felder gesperrt und nicht gespeichert
    const [appInfoLoaded, setAppInfoLoaded] = useState(false)
    const [appInfoFailed, setAppInfoFailed] = useState(false)
    const [savingLanguage, setSavingLanguage] = useState(false)
    const initialized = useRef(false)

    const [profileForm, setProfileForm] = useState({
        username: '', display_name: '', email: '', app_name: '', app_base_url: '',
        registration_enabled: false, forgot_password_enabled: false,
        app_tagline: '', app_creator: '', app_logo_url: '',
        phone: '', company: '', street: '', postal_code: '', city: '', country: '', date_of_birth: '', preferred_language: '',
    })
    const [savingProfile, setSavingProfile] = useState(false)

    // Password change
    const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '', confirm_password: '' })
    const [savingPassword, setSavingPassword] = useState(false)
    const [showCurrentPw, setShowCurrentPw] = useState(false)
    const [showNewPw, setShowNewPw] = useState(false)
    const [uploadingLogo, setUploadingLogo] = useState(false)

    // Formular einmalig aus Profil + öffentlicher App-Info füllen (2.4.1: loadProfile).
    // Die Sprache wird hier NICHT umgestellt (A07): das macht Layout beim Laden (preferred_language).
    useEffect(() => {
        if (!profile || initialized.current) return
        initialized.current = true
        const data = profile
        const fillProfile = (prev) => ({
            ...prev,
            username: data.username || '',
            display_name: data.display_name || '',
            email: data.email || '',
            phone: data.phone || '',
            company: data.company || '',
            street: data.street || '',
            postal_code: data.postal_code || '',
            city: data.city || '',
            country: data.country || '',
            date_of_birth: data.date_of_birth || '',
            preferred_language: data.preferred_language || '',
        })
        api.getAppInfo()
            .then((app) => {
                setProfileForm((prev) => ({
                    ...fillProfile(prev),
                    app_name: app?.app_name || '',
                    // app_base_url kommt für Admins aus getAdminInfo (siehe Effekt unten)
                    registration_enabled: !!app?.registration_enabled,
                    forgot_password_enabled: !!app?.forgot_password_enabled,
                    app_tagline: app?.app_tagline || '',
                    app_creator: app?.app_creator || '',
                    app_logo_url: app?.app_logo_url || '',
                }))
                setAppInfoLoaded(true)
            })
            .catch(() => {
                // Kein Fallback speichern (f84): Admin-Felder bleiben gesperrt, gespeichert wird nur das Profil
                setProfileForm(fillProfile)
                setAppInfoFailed(true)
            })
            .finally(() => setFormReady(true))
    }, [profile])

    useEffect(() => {
        if (!adminInfo) return
        // eslint-disable-next-line react-hooks/set-state-in-effect -- app_base_url für Admins nachreichen (2.4.1)
        setProfileForm((prev) => ({ ...prev, app_base_url: adminInfo.app_base_url || prev.app_base_url || '' }))
    }, [adminInfo])

    // Admin-Felder nur bearbeitbar, wenn die App-Info geladen ist (D01); die Basis-URL zusaetzlich nur mit admin-info
    const appFieldsDisabled = !appInfoLoaded
    const baseUrlLoaded = !!adminInfo

    // ===== Sprache (A07, f92): sofort umschalten, dann speichern; bei Fehler zurück =====
    async function handleLanguageChange(code) {
        const prevForm = profileForm.preferred_language
        const prevLang = i18n.resolvedLanguage || i18n.language
        setProfileForm((f) => ({ ...f, preferred_language: code }))
        setSavingLanguage(true)
        setError('')
        try {
            await applyLanguage(code)
            await api.updateProfile({ preferred_language: code })
            if (profile) {
                const updated = { ...profile, preferred_language: code }
                setProfile(updated)
                api.setUser(updated)
            }
        } catch (err) {
            setProfileForm((f) => ({ ...f, preferred_language: prevForm }))
            try { await applyLanguage(prevLang) } catch { /* alte Sprache ist bereits geladen */ }
            setError(t('settings.languageSaveFailed', { error: err?.message || String(err) }))
        } finally {
            setSavingLanguage(false)
        }
    }

    // ===== Profile functions =====
    // Zwei Schritte (D03): erst das eigene Profil, dann (Admin) die App-Info. Scheitert nur der zweite Schritt,
    // ist das Profil trotzdem gespeichert und sofort sichtbar.
    async function handleSaveProfile(e) {
        e.preventDefault()
        setSavingProfile(true)
        setError('')
        setSuccess('')
        try {
            let result
            try {
                result = await api.updateProfile(buildProfilePayload(profileForm))
            } catch (err) {
                setError(err.message)
                return
            }
            if (result?.user) {
                api.setUser(result.user)
                setProfile(result.user)
                const lang = result.user.preferred_language
                if (lang && lang !== (i18n.resolvedLanguage || i18n.language)) {
                    applyLanguage(lang).catch((err) => console.warn('Sprache konnte nicht geladen werden:', err))
                }
            }
            if (isAdmin && appInfoLoaded) {
                try {
                    await api.updateAppInfo(buildAppInfoPayload(profileForm, { includeBaseUrl: baseUrlLoaded }))
                } catch (err) {
                    setError(t('settings.profileSavedAppInfoFailed', { error: err.message }))
                    return
                }
            }
            setSuccess(t('settings.profileSaveSuccess'))
        } finally {
            setSavingProfile(false)
        }
    }

    async function handleChangePassword(e) {
        e.preventDefault()
        if (passwordForm.new_password !== passwordForm.confirm_password) {
            setError(t('settings.passwordsDoNotMatch'))
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

    if (!formReady) return <PageSpinner />

    return (
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
                            value={profileForm.preferred_language || i18n.resolvedLanguage || 'en'}
                            disabled={savingLanguage}
                            onChange={(e) => handleLanguageChange(e.target.value)}
                            className="w-full max-w-xs px-3 py-2 text-sm border border-border rounded-lg bg-bg-primary disabled:opacity-60"
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

                    {!isAdmin && profile?.zones?.length !== undefined && (
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

                    {isAdmin && (
                        <>
                            {appInfoFailed && (
                                <div className="p-3 rounded-lg bg-warning/10 border border-warning/30 text-warning text-sm flex items-start gap-2" role="status">
                                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                                    <span>{t('settings.appInfoLoadFailed')}</span>
                                </div>
                            )}
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">
                                    {t('settings.systemTitle')}
                                </label>
                                <input
                                    type="text"
                                    value={profileForm.app_name}
                                    onChange={e => setProfileForm({ ...profileForm, app_name: e.target.value })}
                                    placeholder={t('settings.systemTitlePlaceholder')}
                                    className="w-full px-3 py-2 text-sm disabled:opacity-50"
                                    disabled={appFieldsDisabled}
                                    required={!appFieldsDisabled}
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
                                    className="w-full px-3 py-2 text-sm disabled:opacity-50"
                                    disabled={appFieldsDisabled || !baseUrlLoaded}
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
                                        disabled={appFieldsDisabled}
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
                                        disabled={appFieldsDisabled}
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
                                        disabled={appFieldsDisabled}
                                        className="w-full px-3 py-2 text-sm disabled:opacity-50"
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
                                        disabled={appFieldsDisabled}
                                        className="w-full px-3 py-2 text-sm disabled:opacity-50"
                                        maxLength={200}
                                    />
                                    <p className="text-xs text-text-muted mt-1">{t('settings.creatorTextHint')}</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.logoUploadLabel')}</label>
                                    <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" onChange={handleUploadLogo} disabled={appFieldsDisabled} className="w-full text-sm disabled:opacity-50" />
                                    {uploadingLogo && <p className="text-xs text-text-muted mt-1">{t('settings.logoUploading')}</p>}
                                    {profileForm.app_logo_url && (
                                        <div className="mt-2 flex items-center gap-3">
                                            <img src={profileForm.app_logo_url} alt={t('common.appLogoAlt')} className="w-10 h-10 rounded-lg object-contain bg-bg-secondary border border-border" />
                                            <button
                                                type="button"
                                                onClick={() => setProfileForm({ ...profileForm, app_logo_url: '' })}
                                                disabled={appFieldsDisabled}
                                                className="text-xs text-danger hover:underline disabled:opacity-50"
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
                            <span>{t('settings.role')}: <span className="text-accent-light font-medium">{isAdmin ? t('settings.administrator') : t('layout.user')}</span></span>
                            {profile.created_at && <span>{t('settings.created')}: {fmtDate(profile.created_at)}</span>}
                            {profile.last_login && <span>{t('settings.lastLogin')}: {fmtDateTime(profile.last_login)}</span>}
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
    )
}
