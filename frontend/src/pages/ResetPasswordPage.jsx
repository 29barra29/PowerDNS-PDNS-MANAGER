import React, { useState, useEffect } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Shield, Eye, EyeOff, Loader2 } from 'lucide-react'
import api from '../api'
import LanguageDropdown from '../components/LanguageDropdown'

export default function ResetPasswordPage() {
    const { t } = useTranslation()
    const [searchParams] = useSearchParams()
    const token = searchParams.get('token') || ''
    const [password, setPassword] = useState('')
    const [passwordConfirm, setPasswordConfirm] = useState('')
    const [showPw, setShowPw] = useState(false)
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)
    const [success, setSuccess] = useState(false)
    const [appInfo, setAppInfo] = useState({
        app_name: 'DNS Manager',
        app_tagline: 'PowerDNS Admin Panel',
        app_creator: '',
        app_logo_url: '',
        app_version: '',
    })

    useEffect(() => {
        api.getAppInfo().then(setAppInfo).catch(console.error)
        // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time error initialisation
        if (!token) setError(t('reset.invalidLink'))
        // eslint-disable-next-line react-hooks/exhaustive-deps -- t from i18n stable
    }, [token])

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
        setLoading(true)
        try {
            await api.resetPassword({ token, new_password: password })
            setSuccess(true)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-bg-primary relative overflow-hidden">
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent/10 rounded-full blur-3xl" />
            <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-600/10 rounded-full blur-3xl" />

            <div className="glass-card p-8 w-full max-w-md relative z-10">
                <div className="absolute top-4 right-4">
                    <LanguageDropdown />
                </div>
                <div className="text-center mb-8">
                    {appInfo.app_logo_url ? (
                        <img src={appInfo.app_logo_url} alt="App logo" className="w-16 h-16 rounded-2xl object-contain bg-bg-secondary mx-auto mb-4 shadow-lg shadow-accent/20" />
                    ) : (
                        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-accent/20">
                            <Shield className="w-8 h-8 text-white" />
                        </div>
                    )}
                    <h1 className="text-2xl font-bold text-text-primary">{appInfo.app_name}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('reset.title')}</p>
                </div>

                {success ? (
                    <div className="space-y-4">
                        <div className="p-4 rounded-lg bg-success/10 border border-success/30 text-success text-sm">
                            {t('reset.successMessage')}
                        </div>
                        <Link
                            to="/login"
                            className="block w-full py-2.5 text-center bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm"
                        >
                            {t('reset.toLogin')}
                        </Link>
                    </div>
                ) : !token ? (
                    <div className="space-y-4">
                        <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                            {error}
                        </div>
                        <Link to="/login" className="text-accent hover:underline text-sm block text-center">
                            {t('reset.backToLogin')}
                        </Link>
                    </div>
                ) : (
                    <>
                        {error && (
                            <div className="mb-4 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                                {error}
                            </div>
                        )}

                        <form onSubmit={handleSubmit} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('reset.newPassword')} *</label>
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
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('reset.passwordConfirm')} *</label>
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

                            <button
                                type="submit"
                                disabled={loading}
                                className="w-full py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
                            >
                                {loading ? (
                                    <>
                                        <Loader2 className="w-4 h-4 animate-spin" />
                                        {t('reset.submitting')}
                                    </>
                                ) : (
                                    t('reset.submit')
                                )}
                            </button>
                        </form>

                        <p className="text-center text-sm text-text-muted mt-6">
                            <Link to="/login" className="text-accent hover:underline">{t('reset.backToLogin')}</Link>
                        </p>
                    </>
                )}
                <p className="text-center text-xs text-text-muted mt-4">
                    {(appInfo.app_tagline || t('login.tagline'))}{appInfo.app_version ? ` • v${appInfo.app_version}` : ''}
                </p>
                {appInfo.app_creator && <p className="text-center text-xs text-text-muted mt-1">{appInfo.app_creator}</p>}
            </div>
        </div>
    )
}
