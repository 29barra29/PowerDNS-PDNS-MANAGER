import { useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import api from './api'
import i18n from './i18n'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import ForgotPasswordPage from './pages/ForgotPasswordPage'
import ResetPasswordPage from './pages/ResetPasswordPage'
import SetupWizard from './pages/SetupWizard'
import DashboardPage from './pages/DashboardPage'
import ZonesPage from './pages/ZonesPage'
import ZoneDetailPage from './pages/ZoneDetailPage'
import SearchPage from './pages/SearchPage'
import AuditLogPage from './pages/AuditLogPage'
import UsersPage from './pages/UsersPage'
import SettingsPage from './pages/SettingsPage'
import { UpdateAvailabilityProvider } from './context/UpdateAvailabilityContext'
import AppErrorBoundary from './components/AppErrorBoundary'

function ProtectedRoute({ children }) {
  const { t } = useTranslation()
  const [authChecked, setAuthChecked] = useState(api.isLoggedIn())
  const [authorized, setAuthorized] = useState(api.isLoggedIn())
  const [authError, setAuthError] = useState('')

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
        setAuthorized(true)
        setAuthChecked(true)
      })
      .catch((err) => {
        if (err?.status === 401) {
          setAuthorized(false)
        } else {
          setAuthError(err?.message || t('common.serverUnavailable', { defaultValue: 'Server nicht erreichbar. Bitte später erneut versuchen.' }))
        }
        setAuthChecked(true)
      })
  }, [t])
  /* eslint-enable react-hooks/set-state-in-effect */

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
          <h1 className="text-xl font-bold text-text-primary">{t('common.connectionProblem', { defaultValue: 'Verbindungsproblem' })}</h1>
          <p className="text-sm text-text-muted">{authError}</p>
          <button type="button" onClick={() => window.location.reload()} className="px-4 py-2 rounded-lg bg-accent text-white text-sm">
            {t('common.reloadPage', { defaultValue: 'Seite neu laden' })}
          </button>
        </div>
      </div>
    )
  }
  if (!authorized) {
    return <Navigate to="/login" replace />
  }
  return children
}

export default function App() {
  const { t } = useTranslation()
  const [setupStatus, setSetupStatus] = useState(null)
  const [setupError, setSetupError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Apply default language from server (set during install/setup) and sync document.title
    fetch('/api/v1/settings/app-info')
      .then(r => r.json())
      .then(data => {
        if (data.default_language && data.default_language !== i18n.language) i18n.changeLanguage(data.default_language)
        if (data.app_name) document.title = data.app_name
      })
      .catch(() => {})
  }, [])

  const checkSetupStatus = useCallback(async () => {
    setSetupError('')
    try {
      const res = await fetch('/api/v1/setup/status')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setSetupStatus(data)
    } catch (err) {
      console.error('Failed to check setup status:', err)
      setSetupError(t('common.setupStatusFailed', { defaultValue: 'Setup-Status konnte nicht geladen werden. Bitte prüfe, ob Backend und Datenbank laufen.' }))
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
          <h1 className="text-xl font-bold text-text-primary">{t('common.connectionProblem', { defaultValue: 'Verbindungsproblem' })}</h1>
          <p className="text-sm text-text-muted">{setupError}</p>
          <button type="button" onClick={checkSetupStatus} className="px-4 py-2 rounded-lg bg-accent text-white text-sm">
            {t('common.retry', { defaultValue: 'Erneut versuchen' })}
          </button>
        </div>
      </div>
    )
  }

  // Redirect to setup if needed
  if (setupStatus && !setupStatus.has_users && setupStatus.registration_enabled) {
    return (
      <Routes>
        <Route path="/setup" element={<SetupWizard />} />
        <Route path="*" element={<Navigate to="/setup" replace />} />
      </Routes>
    )
  }

  return (
    <AppErrorBoundary>
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
              </UpdateAvailabilityProvider>
            </ProtectedRoute>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="zones" element={<ZonesPage />} />
          <Route path="zones/:server/:zoneId" element={<ZoneDetailPage />} />
          <Route path="search" element={<SearchPage />} />
          <Route path="audit" element={<AuditLogPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppErrorBoundary>
  )
}
