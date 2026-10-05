import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, AlertCircle, Eye, EyeOff, UserCog, Lock, Mail, User } from 'lucide-react'
import api from '../../../api'
import { LANGUAGES } from '../../../i18n'
import PageSpinner from '../../PageSpinner'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'profile', order: 10, labelKey: 'settings.profile', icon: UserCog, adminOnly: false }

// Tab "Profil": eigenes Profil, Sprache, Branding/Systemeinstellungen (nur Admin), Passwort ändern.
// Mechanisch aus SettingsPage.jsx 2.4.1 übernommen.
export default function ProfileTab() {
    const { t, i18n } = useTranslation()
    const { profile, setProfile, adminInfo, notify } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    // Ohne Profil (getMe fehlgeschlagen) bleibt das Formular leer wie in 2.4.1
    const [appLoaded, setAppLoaded] = useState(!profile)
    const initialized = useRef(false)

    const [profileForm, setProfileForm] = useState({
        username: '', display_name: '', email: '', app_name: 'PDNS Manager', app_base_url: '',
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
    const [uploadingLogo, setUploadingLogo] = useState(false)

    // Formular einmalig aus Profil + öffentlicher App-Info füllen (2.4.1: loadProfile)
    useEffect(() => {
        if (!profile || initialized.current) return
        initialized.current = true
        api.getAppInfo().catch(() => ({ app_name: 'PDNS Manager' })).then((app) => {
            const data = profile
            const lang = data.preferred_language || 'de'
            if (lang !== i18n.language) i18n.changeLanguage(lang)
            setProfileForm((prev) => ({
                username: data.username || '',
                display_name: data.display_name || '',
                email: data.email || '',
                app_name: app.app_name || 'PDNS Manager',
                // app_base_url kommt für Admins aus getAdminInfo (siehe Effekt unten)
                app_base_url: prev.app_base_url || '',
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
            }))
            setAppLoaded(true)
        })
    }, [profile, i18n])

    useEffect(() => {
        if (!adminInfo) return
        // eslint-disable-next-line react-hooks/set-state-in-effect -- app_base_url für Admins nachreichen (2.4.1)
        setProfileForm((prev) => ({ ...prev, app_base_url: adminInfo.app_base_url || prev.app_base_url || '' }))
    }, [adminInfo])

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

    if (!appLoaded) return <PageSpinner />

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
    )
}
