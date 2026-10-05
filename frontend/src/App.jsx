import { useState, useEffect, useCallback, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import api, { PASSWORD_CHANGE_EVENT } from './api'
import { applyLanguage, getStoredLanguage } from './i18n'
import { lazyWithReload } from './lib/lazyWithReload'
// Statisch im Entry-Chunk: Anmeldung, Rahmen, Fehlerseite, Rollen-Gate, Spinner, Passwortzwang (F8-B02)
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import AppErrorBoundary from './components/AppErrorBoundary'
import RequireAdmin from './components/RequireAdmin'
import PageSpinner from './components/PageSpinner'
import ForcePasswordChange from './components/ForcePasswordChange'
import Dialogs from './components/dialogs/Dialogs'
import { UpdateAvailabilityProvider } from './context/UpdateAvailabilityContext'

// Alle uebrigen Seiten als eigene Chunks; nach einem Deploy fehlende Chunks loesen genau einen Reload aus (F8-B03)
const RegisterPage = lazyWithReload(() => import('./pages/RegisterPage'))
const ForgotPasswordPage = lazyWithReload(() => import('./pages/ForgotPasswordPage'))
const ResetPasswordPage = lazyWithReload(() => import('./pages/ResetPasswordPage'))
const SetupWizard = lazyWithReload(() => import('./pages/SetupWizard'))
const DashboardPage = lazyWithReload(() => import('./pages/DashboardPage'))
const ZonesPage = lazyWithReload(() => import('./pages/ZonesPage'))
const ZoneDetailPage = lazyWithReload(() => import('./pages/ZoneDetailPage'))
const SearchPage = lazyWithReload(() => import('./pages/SearchPage'))
const AuditLogPage = lazyWithReload(() => import('./pages/AuditLogPage'))
const UsersPage = lazyWithReload(() => import('./pages/UsersPage'))
const SettingsPage = lazyWithReload(() => import('./pages/SettingsPage'))

// Vollbild-Fallback fuer oeffentliche Seiten (ohne Layout)
function FullPageSpinner() {
  return (
    <div className="min-h-screen bg-bg-primary flex items-center justify-center">
      <PageSpinner />
    </div>
  )
}

function ProtectedRoute({ children }) {
  const { t } = useTranslation()
  const [authChecked, setAuthChecked] = useState(api.isLoggedIn())
  const [authorized, setAuthorized] = useState(api.isLoggedIn())
  const [authError, setAuthError] = useState('')
  // Passwortwechsel erzwungen (F3 §6.2): aus dem Login-/me-Objekt oder per 403-Event aus api.js
  const [mustChange, setMustChange] = useState(() => !!api.getUser()?.must_change_password)

  /* eslint-disable react-hooks/set-state-in-effect -- sync auth state from api on mount */
  useEffect(() => {
    if (api.isLoggedIn()) {
      setAuthorized(true)
      setAuthChecked(true)
      return
    }
    api.getMe()
      .then((user) => {
        api.setUser(user)
        setMustChange(!!user?.must_change_password)
        setAuthorized(true)
        setAuthChecked(true)
      })
      .catch((err) => {
        if (err?.status === 401) {
          setAuthorized(false)
        } else {
          setAuthError(err?.message || t('common.serverUnavailable'))
        }
        setAuthChecked(true)
      })
  }, [t])
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    const onFlag = () => setMustChange(true)
    window.addEventListener(PASSWORD_CHANGE_EVENT, onFlag)
    return () => window.removeEventListener(PASSWORD_CHANGE_EVENT, onFlag)
  }, [])

  if (!authChecked) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center">
        <div className="text-text-muted">{t('common.checkingAuth')}</div>
      </div>
    )
  }
  if (authError) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center p-6">
        <div className="glass-card max-w-lg w-full p-6 space-y-3">
          <h1 className="text-xl font-bold text-text-primary">{t('common.connectionProblem')}</h1>
          <p className="text-sm text-text-muted">{authError}</p>
          <button type="button" onClick={() => window.location.reload()} className="px-4 py-2 rounded-lg bg-accent text-white text-sm">
            {t('common.reloadPage')}
          </button>
        </div>
      </div>
    )
  }
  if (!authorized) {
    return <Navigate to="/login" replace />
  }
  if (mustChange) {
    // Keine Seiten mounten, keine Seiten-Requests (F3 §2.6)
    return (
      <ForcePasswordChange
        onDone={(u) => {
          api.setUser(u)
          setMustChange(!!u?.must_change_password)
        }}
      />
    )
  }
  return children
}

export default function App() {
  const { t } = useTranslation()
  const [setupStatus, setSetupStatus] = useState(null)
  const [setupError, setSetupError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Server-Default-Sprache nur ohne eigene Wahl im Browser und ohne Speichern (F8-A04, §2.1 Stufe 3);
    // die Profilsprache setzt Layout danach mit Vorrang. Dazu document.title aus dem App-Namen.
    api.getAppInfo()
      .then((data) => {
        if (data?.default_language && !getStoredLanguage()) {
          applyLanguage(data.default_language, { remember: false })
            .catch((err) => console.warn('Server-Standardsprache konnte nicht geladen werden:', err))
        }
        if (data?.app_name) document.title = data.app_name
      })
      .catch(() => {})
  }, [])

  const checkSetupStatus = useCallback(async () => {
    setSetupError('')
    try {
      const data = await api.request('GET', '/setup/status', null, { authRedirect: false })
      setSetupStatus(data)
    } catch (err) {
      console.error('Failed to check setup status:', err)
      setSetupError(t('common.setupStatusFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    // Check setup status on app load
    queueMicrotask(() => checkSetupStatus())
  }, [checkSetupStatus])
  if (loading) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center">
        <div className="text-text-primary text-xl">{t('common.appLoading')}</div>
      </div>
    )
  }

  if (setupError) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center p-6">
        <div className="glass-card max-w-lg w-full p-6 space-y-3">
          <h1 className="text-xl font-bold text-text-primary">{t('common.connectionProblem')}</h1>
          <p className="text-sm text-text-muted">{setupError}</p>
          <button type="button" onClick={checkSetupStatus} className="px-4 py-2 rounded-lg bg-accent text-white text-sm">
            {t('common.retry')}
          </button>
        </div>
      </div>
    )
  }

  // Redirect to setup if needed
  if (setupStatus && !setupStatus.has_users && setupStatus.registration_enabled) {
    return (
      <AppErrorBoundary>
        <Suspense fallback={<FullPageSpinner />}>
          <Routes>
            <Route path="/setup" element={<SetupWizard />} />
            <Route path="*" element={<Navigate to="/setup" replace />} />
          </Routes>
        </Suspense>
      </AppErrorBoundary>
    )
  }

  return (
    <AppErrorBoundary>
      {/* Fallback nur fuer oeffentliche Seiten; geschuetzte Seiten haben ihren Suspense im Layout */}
      <Suspense fallback={<FullPageSpinner />}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/setup" element={<SetupWizard />} />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <UpdateAvailabilityProvider>
                  <Layout />
                  {/* Dialog-Slot (z. B. Step-up, S8): components/dialogs/*.dialog.jsx */}
                  <Dialogs />
                </UpdateAvailabilityProvider>
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="zones" element={<ZonesPage />} />
            <Route path="zones/:server/:zoneId" element={<ZoneDetailPage />} />
            <Route path="search" element={<SearchPage />} />
            <Route path="audit" element={<RequireAdmin><AuditLogPage /></RequireAdmin>} />
            <Route path="users" element={<RequireAdmin><UsersPage /></RequireAdmin>} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </AppErrorBoundary>
  )
}
