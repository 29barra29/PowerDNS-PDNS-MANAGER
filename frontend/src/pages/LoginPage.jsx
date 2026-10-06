import React, { useRef, useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Shield, Eye, EyeOff, Loader2, KeyRound, LogIn } from 'lucide-react'
import { startAuthentication, browserSupportsWebAuthn } from '@simplewebauthn/browser'
import api from '../api'
import LanguageDropdown from '../components/LanguageDropdown'
import CaptchaWidget from '../components/CaptchaWidget'
import {
    DEFAULT_PROVIDERS, loginVisibility, normalizeProviders, readLoginUrlFlags, ssoErrorKey,
} from '../components/sso/ssoModel'

// Anmeldeseite (F10 §2.1/§6.3, WS-F10-APP-FE): lokale Anmeldung, LDAP im selben Formular, SSO-Buttons (OIDC),
// Fehlerbanner aus ?sso_error=, 2FA-Schritt nach SSO (?sso_2fa=1, Pending-Token im HttpOnly-Cookie) und
// Notfallzugang ?local=1. Die URL-Parameter werden einmalig gelesen und danach aus der Adresszeile entfernt.
export default function LoginPage() {
    const { t } = useTranslation()
    const navigate = useNavigate()
    // Initialisierer statt Effekt (ESLint set-state-in-effect)
    const [urlFlags] = useState(() => readLoginUrlFlags(typeof window !== 'undefined' ? window.location.search : ''))
    const [providers, setProviders] = useState(DEFAULT_PROVIDERS)
    const [ssoBusy, setSsoBusy] = useState(false)
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [showPw, setShowPw] = useState(false)
    const [error, setError] = useState(() => (urlFlags.ssoError
        ? t(ssoErrorKey(urlFlags.ssoError), { code: urlFlags.ssoDetail || '–' })
        : ''))
    const [loading, setLoading] = useState(false)
    const [needTwoFactor, setNeedTwoFactor] = useState(() => urlFlags.sso2fa)
    const [twoFactorToken, setTwoFactorToken] = useState('')
    const [totpCode, setTotpCode] = useState('')
    const [captchaToken, setCaptchaToken] = useState('')
    const [pkBusy, setPkBusy] = useState(false)
    const [pkSupported, setPkSupported] = useState(false)
    const captchaRef = useRef(null)
    const [appInfo, setAppInfo] = useState({
        app_name: 'PDNS Manager',
        app_version: '',
        registration_enabled: false,
        forgot_password_enabled: false,
        app_tagline: 'PowerDNS Admin Panel',
        app_creator: '',
        app_logo_url: '',
        captcha_provider: 'none',
        captcha_site_key: '',
    })

    React.useEffect(() => {
        api.getAppInfo().then(setAppInfo).catch(console.error)
    }, [])

    // SSO-Anbieter; scheitert der Abruf, bleibt die Seite wie bisher (Formular, keine SSO-Buttons)
    React.useEffect(() => {
        const ctrl = new AbortController()
        api.getSsoProviders({ signal: ctrl.signal })
            .then((res) => setProviders(normalizeProviders(res)))
            .catch(() => {})
        return () => ctrl.abort()
    }, [])

    // Parameter nur einmal auswerten: aus der Adresszeile entfernen (?local=1 bleibt stehen)
    React.useEffect(() => {
        if (urlFlags.hadParams) navigate(urlFlags.local ? '/login?local=1' : '/login', { replace: true })
    }, [urlFlags, navigate])

    React.useEffect(() => {
        // Passkeys nur anbieten, wenn der Browser WebAuthn kann.
        queueMicrotask(() => {
            try { setPkSupported(browserSupportsWebAuthn()) } catch { setPkSupported(false) }
        })
    }, [])

    const vis = loginVisibility(providers, urlFlags, appInfo)
    const ssoTwoFactor = urlFlags.sso2fa && needTwoFactor && !twoFactorToken
    // Captcha gehoert zum Formular (nicht zum 2FA-Schritt nach SSO)
    const captchaActive = !ssoTwoFactor && vis.showForm && appInfo.captcha_provider && appInfo.captcha_provider !== 'none' && appInfo.captcha_site_key
    const ssoName = providers.providers[0]?.label || 'SSO'

    const handleSsoStart = (provider) => {
        if (ssoBusy) return
        setError('')
        setSsoBusy(true)
        window.location.assign(provider.start_url)
    }

    const handlePasskeyLogin = async () => {
        setError('')
        setPkBusy(true)
        try {
            const { options, challenge_token } = await api.passkeyLoginBegin()
            const credential = await startAuthentication({ optionsJSON: options })
            await api.passkeyLoginComplete(challenge_token, credential)
            navigate('/')
        } catch (err) {
            // Abbruch durch den Nutzer (z. B. Dialog geschlossen) ist kein Fehler.
            if (err?.name === 'NotAllowedError' || err?.name === 'AbortError') {
                setError('')
            } else {
                setError(err?.message || t('auth.passkeyFailed'))
            }
        } finally {
            setPkBusy(false)
        }
    }

    const handleSubmit = async (e) => {
        e.preventDefault()
        setError('')

        if (captchaActive && !captchaToken) {
            setError(t('auth.captchaRequired'))
            return
        }
        setLoading(true)

        try {
            if (needTwoFactor && (twoFactorToken || urlFlags.sso2fa)) {
                // Nach SSO ohne Token im Body: das Backend liest das Pending-Token aus dem Cookie
                await api.completeLogin2fa(twoFactorToken || null, totpCode)
                navigate('/')
                return
            }
            const out = await api.login(username, password, captchaToken || null)
            if (out?.needTwoFactor) {
                setNeedTwoFactor(true)
                setTwoFactorToken(out.twoFactorToken || '')
                setError('')
                return
            }
            navigate('/')
        } catch (err) {
            setError(err.message)
            // Token ist nach jedem Submit verbraucht - Widget zuruecksetzen,
            // damit der User es bei einem zweiten Versuch neu loesen kann.
            if (captchaActive) {
                setCaptchaToken('')
                captchaRef.current?.reset()
            }
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
                <div className="absolute top-4 right-4">
                    <LanguageDropdown />
                </div>
                {/* Logo */}
                <div className="text-center mb-8">
                    {appInfo.app_logo_url ? (
                        <img src={appInfo.app_logo_url} alt={t('common.appLogoAlt')} className="w-16 h-16 rounded-2xl object-contain bg-bg-secondary mx-auto mb-4 shadow-lg shadow-accent/20" />
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
                    <div role="alert" className="mb-4 p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">
                        {error}
                    </div>
                )}

                {vis.showLocalModeHint && (
                    <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm">
                        {t('login.localModeHint')}
                    </div>
                )}

                {/* SSO-Buttons (OIDC) */}
                {vis.showSsoButtons && (
                    <div className="space-y-2 mb-4">
                        {providers.providers.map((p) => (
                            <button
                                key={p.id}
                                type="button"
                                onClick={() => handleSsoStart(p)}
                                disabled={ssoBusy || loading || pkBusy}
                                className={vis.ssoPrimary
                                    ? 'w-full py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50'
                                    : 'w-full py-2.5 border border-border hover:border-accent/60 hover:bg-bg-hover text-text-primary rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50'}
                            >
                                {ssoBusy ? (
                                    <>
                                        <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                                        {t('login.ssoRedirecting')}
                                    </>
                                ) : (
                                    <>
                                        <LogIn className="w-4 h-4" aria-hidden="true" />
                                        {t('login.ssoButton', { name: p.label })}
                                    </>
                                )}
                            </button>
                        ))}
                    </div>
                )}

                {vis.showDivider && (
                    <div className="flex items-center gap-3 mb-4">
                        <span className="h-px flex-1 bg-border/60" />
                        <span className="text-xs text-text-muted text-center">{t('login.orLocal')}</span>
                        <span className="h-px flex-1 bg-border/60" />
                    </div>
                )}

                {/* 2FA-Schritt nach SSO: nur der Code */}
                {ssoTwoFactor && (
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <p className="text-sm text-text-secondary">{t('login.sso2faHint', { name: ssoName })}</p>
                        <div>
                            <label htmlFor="login-sso-totp" className="block text-sm font-medium text-text-secondary mb-1.5">{t('auth.totpCode')}</label>
                            <input
                                id="login-sso-totp"
                                type="text"
                                inputMode="numeric"
                                autoComplete="one-time-code"
                                value={totpCode}
                                onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                                className="w-full px-4 py-2.5 text-sm font-mono tracking-widest"
                                placeholder="123456"
                                maxLength={8}
                                autoFocus
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={loading || totpCode.length < 6}
                            className="w-full py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
                        >
                            {loading ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : null}
                            {t('auth.verifyTotp')}
                        </button>
                        <p className="text-center text-sm">
                            {/* Vollstaendiges Neuladen: verwirft den 2FA-Zustand der Seite */}
                            <a href="/login" className="text-accent hover:underline">{t('login.sso2faRestart')}</a>
                        </p>
                    </form>
                )}

                {/* Form */}
                {!ssoTwoFactor && vis.showForm && (
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
                                required={!needTwoFactor}
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
                                    required={!needTwoFactor}
                                    disabled={needTwoFactor}
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

                        {needTwoFactor && (
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('auth.totpCode')}</label>
                                <input
                                    type="text"
                                    inputMode="numeric"
                                    autoComplete="one-time-code"
                                    value={totpCode}
                                    onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 8))}
                                    className="w-full px-4 py-2.5 text-sm font-mono tracking-widest"
                                    placeholder="123456"
                                    maxLength={8}
                                />
                            </div>
                        )}

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
                            disabled={loading || (captchaActive && !captchaToken) || (needTwoFactor && totpCode.length < 4)}
                            className="w-full py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
                        >
                            {loading ? (
                                <>
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                    {t('login.submitting')}
                                </>
                            ) : needTwoFactor ? (
                                t('auth.verifyTotp')
                            ) : (
                                t('login.submit')
                            )}
                        </button>

                        {vis.showLdapHint && !needTwoFactor && (
                            <p className="text-xs text-text-muted">{t('login.ldapHint', { name: providers.ldap.label })}</p>
                        )}

                        {pkSupported && vis.showPasskey && !needTwoFactor && (
                            <>
                                <div className="flex items-center gap-3 my-1">
                                    <span className="h-px flex-1 bg-border/60" />
                                    <span className="text-xs text-text-muted uppercase tracking-wide">{t('auth.orPasskey')}</span>
                                    <span className="h-px flex-1 bg-border/60" />
                                </div>
                                <button
                                    type="button"
                                    onClick={handlePasskeyLogin}
                                    disabled={pkBusy || loading}
                                    className="w-full py-2.5 border border-border hover:border-accent/60 hover:bg-bg-hover text-text-primary rounded-lg font-medium text-sm transition-all duration-200 flex items-center justify-center gap-2 disabled:opacity-50"
                                >
                                    {pkBusy ? (
                                        <>
                                            <Loader2 className="w-4 h-4 animate-spin" />
                                            {t('auth.passkeyWait')}
                                        </>
                                    ) : (
                                        <>
                                            <KeyRound className="w-4 h-4" />
                                            {t('auth.passkeyLogin')}
                                        </>
                                    )}
                                </button>
                            </>
                        )}

                        {(vis.showForgot || vis.showRegister) && (
                            <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 mt-4 text-sm">
                                {vis.showForgot && (
                                    <Link to="/forgot-password" className="text-accent hover:underline">
                                        {t('login.forgotPassword')}
                                    </Link>
                                )}
                                {vis.showRegister && (
                                    <Link to="/register" className="text-accent hover:underline">
                                        {t('login.register')}
                                    </Link>
                                )}
                            </div>
                        )}
                    </form>
                )}

                {(vis.showEmergencyLink || vis.showBackToSso) && !ssoTwoFactor && (
                    <p className="text-center text-xs mt-4">
                        {/* Vollstaendiges Neuladen, damit die Seite die Parameter neu auswertet */}
                        {vis.showEmergencyLink
                            ? <a href="/login?local=1" className="text-text-muted hover:text-accent hover:underline">{t('login.emergencyLink')}</a>
                            : <a href="/login" className="text-text-muted hover:text-accent hover:underline">{t('login.backToSso')}</a>}
                    </p>
                )}

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
