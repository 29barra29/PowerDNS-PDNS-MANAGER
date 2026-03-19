import React, { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Shield, Mail, Loader2 } from 'lucide-react'
import api from '../api'

export default function ForgotPasswordPage() {
    const { t, i18n } = useTranslation()
    const navigate = useNavigate()
    const [email, setEmail] = useState('')
    const [error, setError] = useState('')
    const [loading, setLoading] = useState(false)
    const [sent, setSent] = useState(false)
    const [appInfo, setAppInfo] = useState({
        app_name: 'DNS Manager',
        forgot_password_enabled: false,
        app_tagline: 'PowerDNS Admin Panel',
        app_creator: '',
        app_logo_url: '',
        app_version: '',
    })

    useEffect(() => {
        api.getAppInfo().then((info) => {
            setAppInfo(info)
            if (!info.forgot_password_enabled) navigate('/login')
        }).catch(() => navigate('/login'))
    }, [navigate])

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')
        if (!email.trim()) {
            setError(t('forgot.enterEmailOrUser'))
            return
        }
        setLoading(true)
        try {
            const isEmail = email.includes('@')
            await api.requestPasswordReset(
                isEmail ? { email: email.trim() } : { username: email.trim() }
            )
            setSent(true)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    if (!appInfo.forgot_password_enabled) return null

    return (
        <div className="min-h-screen flex items-center justify-center bg-bg-primary relative overflow-hidden">
            <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent/10 rounded-full blur-3xl" />
            <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-600/10 rounded-full blur-3xl" />

            <div className="glass-card p-8 w-full max-w-md relative z-10">
                <div className="absolute top-4 right-4 flex gap-1 text-xs">
                    <button type="button" onClick={() => i18n.changeLanguage('de')} className={`px-2 py-1 rounded ${i18n.language === 'de' ? 'bg-accent/20 text-accent-light font-medium' : 'text-text-muted hover:text-text-primary'}`}>DE</button>
                    <span className="text-text-muted">|</span>
                    <button type="button" onClick={() => i18n.changeLanguage('en')} className={`px-2 py-1 rounded ${i18n.language === 'en' ? 'bg-accent/20 text-accent-light font-medium' : 'text-text-muted hover:text-text-primary'}`}>EN</button>
                </div>
                <div className="text-center mb-8">
                    {appInfo.app_logo_url ? (
                        <img src={appInfo.app_logo_url} alt="App logo" className="w-16 h-16 rounded-2xl object-cover mx-auto mb-4 shadow-lg shadow-accent/20" />
                    ) : (
                        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-accent/20">
                            <Shield className="w-8 h-8 text-white" />
                        </div>
                    )}
                    <h1 className="text-2xl font-bold text-text-primary">{appInfo.app_name}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('forgot.title')}</p>
                </div>

                {sent ? (
                    <div className="space-y-4">
                        <div className="p-4 rounded-lg bg-success/10 border border-success/30 text-success text-sm">
                            {t('forgot.sentHint')}
                        </div>
                        <p className="text-center text-sm text-text-muted">
                            <Link to="/login" className="text-accent hover:underline">{t('forgot.backToLogin')}</Link>
                        </p>
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
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">
                                    {t('forgot.emailOrUser')}
                                </label>
                                <div className="relative">
                                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                                    <input
                                        type="text"
                                        value={email}
                                        onChange={(e) => setEmail(e.target.value)}
                                        className="w-full pl-10 pr-4 py-2.5 text-sm"
                                        placeholder="mail@beispiel.de oder benutzername"
                                        required
                                    />
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
                                        {t('forgot.submitting')}
                                    </>
                                ) : (
                                    t('forgot.submit')
                                )}
                            </button>
                        </form>

                        <p className="text-center text-sm text-text-muted mt-6">
                            <Link to="/login" className="text-accent hover:underline">{t('forgot.backToLogin')}</Link>
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
