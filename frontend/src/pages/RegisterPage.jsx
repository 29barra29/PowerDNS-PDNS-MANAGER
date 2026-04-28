import React, { useState, useEffect, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Shield, Eye, EyeOff, Loader2 } from 'lucide-react'
import api from '../api'
import LanguageDropdown from '../components/LanguageDropdown'
import CaptchaWidget from '../components/CaptchaWidget'

export default function RegisterPage() {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const [username, setUsername] = useState('')
    const [email, setEmail] = useState('')
    const [displayName, setDisplayName] = useState('')
    const [password, setPassword] = useState('')
    const [passwordConfirm, setPasswordConfirm] = useState('')
    const [showPw, setShowPw] = useState(false)
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)
    const [captchaToken, setCaptchaToken] = useState('')
    const captchaRef = useRef(null)
    const [appInfo, setAppInfo] = useState({
        app_name: 'PDNS Manager',
        registration_enabled: false,
        app_tagline: 'PowerDNS Admin Panel',
        app_creator: '',
        app_logo_url: '',
        app_version: '',
        captcha_provider: 'none',
        captcha_site_key: '',
    })

    useEffect(() => {
        api.getAppInfo().then((info) => {
            setAppInfo(info)
            if (!info.registration_enabled) navigate('/login')
        }).catch(() => navigate('/login'))
    }, [navigate])

    const captchaActive = appInfo.captcha_provider && appInfo.captcha_provider !== 'none' && appInfo.captcha_site_key

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        if (password !== passwordConfirm) {
            setError(t('register.passwordsDoNotMatch'))
            return
        }
        if (password.length < 8) {
            setError(t('register.passwordMinLength'))
            return
        }
        if (captchaActive && !captchaToken) {
            setError(t('auth.captchaRequired'))
            return
        }
        setLoading(true)
        try {
            await api.register({
                username: username.trim(),
                email: email.trim() || undefined,
                display_name: displayName.trim() || undefined,
                password,
                captcha_token: captchaToken || undefined,
            })
            navigate('/login', { state: { message: t('register.successMessage') } })
        } catch (err) {
            setError(err.message)
            if (captchaActive) {
                setCaptchaToken('')
                captchaRef.current?.reset()
            }
        } finally {
            setLoading(false)
        }
    }

    if (!appInfo.registration_enabled) return null

    return (
        <div className="min-h-screen flex items-center justify-center bg-bg-primary relative overflow-hidden">
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent/10 rounded-full blur-3xl" />
            <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-600/10 rounded-full blur-3xl" />

            <div className="glass-card p-8 w-full max-w-md relative z-10">
                <div className="text-center mb-8">
                    {appInfo.app_logo_url ? (
                        <img src={appInfo.app_logo_url} alt="App logo" className="w-16 h-16 rounded-2xl object-contain bg-bg-secondary mx-auto mb-4 shadow-lg shadow-accent/20" />
                    ) : (
                        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-accent/20">
                            <Shield className="w-8 h-8 text-white" />
                        </div>
                    )}
                    <h1 className="text-2xl font-bold text-text-primary">{appInfo.app_name}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('register.title')}</p>
                </div>
                <div className="absolute top-4 right-4">
                    <LanguageDropdown />
                </div>

                {error && (
                    <div className="mb-4 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                        {error}
                    </div>
                )}

                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('register.username')} *</label>
                        <input
                            type="text"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            className="w-full px-4 py-2.5 text-sm"
                            placeholder={t('register.usernamePlaceholder')}
                            required
                            minLength={3}
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('register.email')}</label>
                        <input
                            type="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            className="w-full px-4 py-2.5 text-sm"
                            placeholder={t('register.emailPlaceholder')}
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('register.displayName')}</label>
                        <input
                            type="text"
                            value={displayName}
                            onChange={(e) => setDisplayName(e.target.value)}
                            className="w-full px-4 py-2.5 text-sm"
                            placeholder={t('register.displayNamePlaceholder')}
                        />
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('register.password')} *</label>
                        <div className="relative">
                            <input
                                type={showPw ? 'text' : 'password'}
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full px-4 py-2.5 pr-10 text-sm"
                                placeholder="••••••••"
                                required
                                minLength={8}
                                maxLength={128}
                            />
                            <button
                                type="button"
                                onClick={() => setShowPw(!showPw)}
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors"
                            >
                                {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                            </button>
                        </div>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('register.passwordConfirm')} *</label>
                        <input
                            type={showPw ? 'text' : 'password'}
                            value={passwordConfirm}
                            onChange={(e) => setPasswordConfirm(e.target.value)}
                            className="w-full px-4 py-2.5 text-sm"
                            placeholder="••••••••"
                            required
                            minLength={8}
                            maxLength={128}
                        />
                    </div>

                    {captchaActive && (
                        <CaptchaWidget
                            ref={captchaRef}
                            provider={appInfo.captcha_provider}
                            siteKey={appInfo.captcha_site_key}
                            onToken={setCaptchaToken}
                            onExpire={() => setCaptchaToken('')}
                            onError={() => setCaptchaToken('')}
                        />
                    )}

                    <button
                        type="submit"
                        disabled={loading || (captchaActive && !captchaToken)}
                        className="w-full py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                        {loading ? (
                            <>
                                <Loader2 className="w-4 h-4 animate-spin" />
                                {t('register.submitting')}
                            </>
                        ) : (
                            t('register.submit')
                        )}
                    </button>
                </form>

                <p className="text-center text-sm text-text-muted mt-6">
                    {t('register.alreadyAccount')} <Link to="/login" className="text-accent hover:underline">{t('register.signIn')}</Link>
                </p>
                <p className="text-center text-xs text-text-muted mt-4">
                    {(appInfo.app_tagline || t('login.tagline'))}{appInfo.app_version ? ` • v${appInfo.app_version}` : ''}
                </p>
                {appInfo.app_creator && <p className="text-center text-xs text-text-muted mt-1">{appInfo.app_creator}</p>}
            </div>
        </div>
    )
}
