import { useState, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
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

function ProtectedRoute({ children }) {
  const [authChecked, setAuthChecked] = useState(api.isLoggedIn())
  const [authorized, setAuthorized] = useState(api.isLoggedIn())

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
      .catch(() => {
        setAuthorized(false)
        setAuthChecked(true)
      })
  }, [])

  if (!authChecked) {
    return (
      <div className="min-h-screen bg-bg-primary flex items-center justify-center">
        <div className="text-text-muted">Anmeldung wird geprüft...</div>
      </div>
    )
  }
  if (!authorized) {
    return <Navigate to="/login" replace />
  }
  return children
}

export default function App() {
  const [setupStatus, setSetupStatus] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Apply default language from server (set during install/setup)
    fetch('/api/v1/settings/app-info')
      .then(r => r.json())
      .then(data => { if (data.default_language && data.default_language !== i18n.language) i18n.changeLanguage(data.default_language) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    // Check setup status on app load
    checkSetupStatus()
  }, [])

  const checkSetupStatus = async () => {
    try {
      const res = await fetch('/api/v1/setup/status')
      const data = await res.json()
      setSetupStatus(data)
    } catch (err) {
      console.error('Failed to check setup status:', err)
    } finally {
      setLoading(false)
    }
  }

  // Loading state
  if (loading) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-white text-xl">Lade DNS Manager...</div>
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
            <Layout />
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
  )
}
