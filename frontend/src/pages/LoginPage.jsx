import React, { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Shield, Eye, EyeOff, Loader2 } from 'lucide-react'
import api from '../api'

export default function LoginPage() {
    const { t, i18n } = useTranslation()
    const navigate = useNavigate()
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [showPw, setShowPw] = useState(false)
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)
    const [appInfo, setAppInfo] = useState({
        app_name: 'DNS Manager',
        app_version: '',
        registration_enabled: false,
        forgot_password_enabled: false,
        app_tagline: 'PowerDNS Admin Panel',
        app_creator: '',
        app_logo_url: '',
    })

    React.useEffect(() => {
        api.getAppInfo().then(setAppInfo).catch(console.error)
    }, [])

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        setLoading(true)

        try {
            await api.login(username, password)
            navigate('/')
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-bg-primary relative overflow-hidden">
            {/* Background glow effects */}
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent/10 rounded-full blur-3xl" />
            <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-600/10 rounded-full blur-3xl" />

            <div className="glass-card p-8 w-full max-w-md relative z-10">
                {/* Language switcher */}
                <div className="absolute top-4 right-4 flex gap-1 text-xs">
                    <button type="button" onClick={() => i18n.changeLanguage('de')} className={`px-2 py-1 rounded ${i18n.language === 'de' ? 'bg-accent/20 text-accent-light font-medium' : 'text-text-muted hover:text-text-primary'}`}>DE</button>
                    <span className="text-text-muted">|</span>
                    <button type="button" onClick={() => i18n.changeLanguage('en')} className={`px-2 py-1 rounded ${i18n.language === 'en' ? 'bg-accent/20 text-accent-light font-medium' : 'text-text-muted hover:text-text-primary'}`}>EN</button>
                </div>
                {/* Logo */}
                <div className="text-center mb-8">
                    {appInfo.app_logo_url ? (
                        <img src={appInfo.app_logo_url} alt="App logo" className="w-16 h-16 rounded-2xl object-contain bg-bg-secondary mx-auto mb-4 shadow-lg shadow-accent/20" />
                    ) : (
                        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-accent/20">
                            <Shield className="w-8 h-8 text-white" />
                        </div>
                    )}
                    <h1 className="text-2xl font-bold text-text-primary">{appInfo.app_name}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('login.title')}</p>
                </div>

                {/* Error */}
                {error && (
                    <div className="mb-4 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                        {error}
                    </div>
                )}

                {/* Form */}
                <form onSubmit={handleSubmit} className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('login.username')}</label>
                        <input
                            type="text"
                            value={username}
                            onChange={(e) => setUsername(e.target.value)}
                            className="w-full px-4 py-2.5 text-sm"
                            placeholder="admin"
                            autoFocus
                            required
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('login.password')}</label>
                        <div className="relative">
                            <input
                                type={showPw ? 'text' : 'password'}
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full px-4 py-2.5 pr-10 text-sm"
                                placeholder="••••••••"
                                required
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

                    <button
                        type="submit"
                        disabled={loading}
                        className="w-full py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
                    >
                        {loading ? (
                            <>
                                <Loader2 className="w-4 h-4 animate-spin" />
                                {t('login.submitting')}
                            </>
                        ) : (
                            t('login.submit')
                        )}
                    </button>

                    {(appInfo.forgot_password_enabled || appInfo.registration_enabled) && (
                        <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 mt-4 text-sm">
                            {appInfo.forgot_password_enabled && (
                                <Link to="/forgot-password" className="text-accent hover:underline">
                                    {t('login.forgotPassword')}
                                </Link>
                            )}
                            {appInfo.registration_enabled && (
                                <Link to="/register" className="text-accent hover:underline">
                                    {t('login.register')}
                                </Link>
                            )}
                        </div>
                    )}
                </form>

                <p className="text-center text-xs text-text-muted mt-6">
                    {(appInfo.app_tagline || t('login.tagline'))}{appInfo.app_version ? ` • v${appInfo.app_version}` : ''}
                </p>
                {appInfo.app_creator && (
                    <p className="text-center text-xs text-text-muted mt-1">{appInfo.app_creator}</p>
                )}
            </div>
        </div>
    )
}
