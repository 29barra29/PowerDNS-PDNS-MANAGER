import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  User,
  Mail,
  Key,
  Server,
  CheckCircle,
  AlertTriangle,
  Shield,
  Loader2
} from 'lucide-react';
import api from '../api';
import LanguageDropdown from '../components/LanguageDropdown';

export default function SetupWizard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [setupStatus, setSetupStatus] = useState(null);
  const [appInfo, setAppInfo] = useState({
    app_name: 'PDNS Manager',
    app_tagline: 'PowerDNS Admin Panel',
    app_creator: '',
    app_logo_url: '',
    app_version: '',
  });

  // Form data
  const [userData, setUserData] = useState({
    username: '',
    email: '',
    password: '',
    passwordConfirm: '',
    displayName: '',
  });

  const [emailConfig, setEmailConfig] = useState({
    enabled: false,
    smtp_host: 'smtp.gmail.com',
    smtp_port: 587,
    smtp_user: '',
    smtp_password: '',
    smtp_from: '',
  });

  const [_pdnsConfig, _setPdnsConfig] = useState({
    enabled: false,
    name: 'server1',
    url: 'http://localhost:8081',
    api_key: '',
  });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/immutability -- function declared below, called once on mount
    checkSetupStatus();
    api.getAppInfo().then(setAppInfo).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once on mount
  }, []);

  const checkSetupStatus = async () => {
    try {
      const res = await fetch('/api/v1/setup/status');
      const data = await res.json();
      setSetupStatus(data);

      // If setup is complete or registration disabled, redirect
      if (data.is_setup_complete || !data.registration_enabled) {
        navigate('/login');
      }
    } catch (err) {
      console.error('Failed to check setup status:', err);
    }
  };

  const handleRegister = async () => {
    setError('');

    // Validate
    if (userData.password !== userData.passwordConfirm) {
      setError(t('setup.passwordsMismatch'));
      return;
    }

    if (userData.password.length < 8) {
      setError(t('setup.passwordTooShort'));
      return;
    }

    setLoading(true);

    try {
      // Register first admin
      const res = await fetch('/api/v1/setup/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          username: userData.username,
          email: userData.email,
          password: userData.password,
          display_name: userData.displayName || userData.username,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || t('setup.registerFailed'));
      }

      const data = await res.json();

      // Token wird per HttpOnly-Cookie gesetzt; User nur im Speicher.
      // Es gibt absichtlich keinen access_token mehr im Body (Sicherheit gegen XSS).
      api.setUser(data.user);

      // SMTP wird (falls konfiguriert) erst NACH dem Setup über Einstellungen → SMTP gesetzt.
      // Hier nur eine Notiz im UI – wir reichen die Eingaben nicht weiter, weil der Setup-Endpoint sie nicht persistiert hätte.
      if (emailConfig.enabled) {
        try {
          await api.updateSmtpSettings({
            smtp_host: emailConfig.smtp_host,
            smtp_port: emailConfig.smtp_port,
            smtp_user: emailConfig.smtp_user,
            smtp_password: emailConfig.smtp_password,
            smtp_from: emailConfig.smtp_from || emailConfig.smtp_user,
            smtp_use_tls: true,
          });
        } catch (smtpErr) {
          // SMTP-Setup-Fehler ist nicht kritisch – Admin kann das später manuell korrigieren.
          console.warn('SMTP konnte nicht gespeichert werden:', smtpErr);
        }
      }

      // Success - redirect to dashboard
      setStep(4);
      setTimeout(() => {
        window.location.href = '/';
      }, 3000);

    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  if (!setupStatus) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-white">{t('setup.loading')}</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg-primary relative overflow-hidden py-16">
      {/* Background glow effects to match LoginPage */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-accent/10 rounded-full blur-3xl" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-purple-600/10 rounded-full blur-3xl" />

      <div className="container mx-auto px-4 relative z-10">
        <div className="max-w-2xl mx-auto">
          {/* Header */}
          <div className="text-center mb-8 relative">
            {!appInfo.app_logo_url ? (
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center mx-auto mb-4 shadow-lg shadow-accent/20">
                <Shield className="w-8 h-8 text-white" />
              </div>
            ) : (
              <img src={appInfo.app_logo_url} alt="App logo" className="w-16 h-16 rounded-2xl object-contain bg-bg-secondary mx-auto mb-4 shadow-lg shadow-accent/20" />
            )}
            <h1 className="text-3xl font-bold text-text-primary mb-2">
              {appInfo.app_name || t('setup.welcomeTitle')}
            </h1>
            <p className="text-text-muted text-sm">
              {t('setup.welcomeSubtitle')}
            </p>
            <div className="absolute top-4 right-4">
              <LanguageDropdown />
            </div>
          </div>

          {/* Progress */}
          <div className="flex justify-between mb-8">
            {[1, 2, 3, 4].map((s) => (
              <div
                key={s}
                className={`flex-1 h-2 mx-1 rounded-full transition-all ${
                  s <= step ? 'bg-gradient-to-r from-accent to-purple-600' : 'bg-surface border border-border'
                }`}
              />
            ))}
          </div>

          {/* Card */}
          <div className="glass-card p-8">
            {error && (
              <div className="mb-6 p-4 bg-danger/10 border border-danger/30 rounded-lg flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-danger" />
                <span className="text-danger text-sm">{error}</span>
              </div>
             )}

            {/* Step 1: Admin Account */}
            {step === 1 && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                <h2 className="text-xl font-bold text-text-primary mb-6 flex items-center gap-3">
                  <div className="p-2 bg-surface rounded-lg border border-border">
                    <User className="w-5 h-5 text-accent" />
                  </div>
                  {t('setup.step1Title')}
                </h2>

                <div className="space-y-5">
                  <div>
                    <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('login.username')}</label>
                    <input
                      type="text"
                      className="w-full px-4 py-2.5 text-sm"
                      value={userData.username}
                      onChange={(e) => setUserData({...userData, username: e.target.value})}
                      placeholder="admin"
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('register.email')}</label>
                    <input
                      type="email"
                      className="w-full px-4 py-2.5 text-sm"
                      value={userData.email}
                      onChange={(e) => setUserData({...userData, email: e.target.value})}
                      placeholder="admin@example.com"
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('register.displayName')}</label>
                    <input
                      type="text"
                      className="w-full px-4 py-2.5 text-sm"
                      value={userData.displayName}
                      onChange={(e) => setUserData({...userData, displayName: e.target.value})}
                      placeholder={t('setup.adminPlaceholder')}
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('login.password')}</label>
                    <input
                      type="password"
                      className="w-full px-4 py-2.5 text-sm"
                      value={userData.password}
                      onChange={(e) => setUserData({...userData, password: e.target.value})}
                      placeholder={t('setup.passwordPlaceholder')}
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('setup.passwordRepeat')}</label>
                    <input
                      type="password"
                      className="w-full px-4 py-2.5 text-sm"
                      value={userData.passwordConfirm}
                      onChange={(e) => setUserData({...userData, passwordConfirm: e.target.value})}
                      placeholder={t('setup.confirmPlaceholder')}
                      required
                    />
                  </div>
                </div>

                <div className="mt-8 flex justify-end">
                  <button
                    onClick={() => setStep(2)}
                    disabled={!userData.username || !userData.email || !userData.password}
                    className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {t('setup.next')}
                  </button>
                </div>
              </div>
            )}

            {/* Step 2: Email (Optional) */}
            {step === 2 && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                <h2 className="text-xl font-bold text-text-primary mb-6 flex items-center gap-3">
                  <div className="p-2 bg-surface rounded-lg border border-border">
                    <Mail className="w-5 h-5 text-accent" />
                  </div>
                  {t('setup.step2Title')}
                </h2>

                <div className="mb-6 p-4 bg-surface border border-border rounded-lg">
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      className="w-5 h-5 rounded border-border bg-bg-primary text-accent focus:ring-accent"
                      checked={emailConfig.enabled}
                      onChange={(e) => setEmailConfig({...emailConfig, enabled: e.target.checked})}
                    />
                    <span className="text-sm font-medium text-text-primary">{t('setup.enableEmail')}</span>
                  </label>
                </div>

                {emailConfig.enabled && (
                  <div className="space-y-5">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('setup.smtpServer')}</label>
                        <input
                          type="text"
                          className="w-full px-4 py-2.5 text-sm"
                          value={emailConfig.smtp_host}
                          onChange={(e) => setEmailConfig({...emailConfig, smtp_host: e.target.value})}
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('setup.port')}</label>
                        <input
                          type="number"
                          className="w-full px-4 py-2.5 text-sm"
                          value={emailConfig.smtp_port}
                          onChange={(e) => setEmailConfig({...emailConfig, smtp_port: parseInt(e.target.value)})}
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('setup.smtpUser')}</label>
                      <input
                        type="email"
                        className="w-full px-4 py-2.5 text-sm"
                        value={emailConfig.smtp_user}
                        onChange={(e) => setEmailConfig({...emailConfig, smtp_user: e.target.value})}
                        placeholder={t('setup.smtpUserPlaceholder')}
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('setup.smtpPassword')}</label>
                      <input
                        type="password"
                        className="w-full px-4 py-2.5 text-sm"
                        value={emailConfig.smtp_password}
                        onChange={(e) => setEmailConfig({...emailConfig, smtp_password: e.target.value})}
                        placeholder={t('setup.appPasswordPlaceholder')}
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-text-secondary mb-1.5">{t('setup.senderEmail')}</label>
                      <input
                        type="email"
                        className="w-full px-4 py-2.5 text-sm"
                        value={emailConfig.smtp_from}
                        onChange={(e) => setEmailConfig({...emailConfig, smtp_from: e.target.value})}
                        placeholder={emailConfig.smtp_user || 'noreply@example.com'}
                      />
                    </div>
                  </div>
                )}

                <div className="mt-8 flex justify-between">
                  <button
                    onClick={() => setStep(1)}
                    className="px-6 py-2.5 bg-surface text-text-primary border border-border rounded-lg hover:bg-surface-hover font-medium text-sm transition-all duration-200"
                  >
                    {t('setup.back')}
                  </button>
                  <button
                    onClick={() => setStep(3)}
                    className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all duration-200"
                  >
                    {t('setup.next')}
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: Review & Complete */}
            {step === 3 && (
              <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                <h2 className="text-xl font-bold text-text-primary mb-6 flex items-center gap-3">
                  <div className="p-2 bg-surface rounded-lg border border-border">
                    <Key className="w-5 h-5 text-accent" />
                  </div>
                  {t('setup.step3Title')}
                </h2>

                <div className="space-y-4 mb-8">
                  <div className="p-5 bg-surface border border-border rounded-lg">
                    <h3 className="font-semibold text-text-primary mb-3">{t('setup.step1Title')}</h3>
                    <div className="space-y-2 text-sm text-text-secondary">
                        <p><span className="text-text-muted">{t('login.username')}:</span> {userData.username}</p>
                        <p><span className="text-text-muted">{t('register.email')}:</span> {userData.email}</p>
                    </div>
                  </div>

                  {emailConfig.enabled && (
                    <div className="p-5 bg-surface border border-border rounded-lg">
                      <h3 className="font-semibold text-text-primary mb-3">{t('register.email')}</h3>
                      <div className="space-y-2 text-sm text-text-secondary">
                          <p><span className="text-text-muted">{t('setup.smtpServer')}:</span> {emailConfig.smtp_host}:{emailConfig.smtp_port}</p>
                          <p><span className="text-text-muted">{t('setup.smtpUser')}:</span> {emailConfig.smtp_user}</p>
                      </div>
                    </div>
                  )}

                  <div className="p-5 bg-accent/10 border border-accent/20 rounded-lg">
                    <p className="text-accent text-sm">
                      {t('setup.pdnsHint')}
                    </p>
                  </div>
                </div>

                <div className="mt-8 flex justify-between">
                  <button
                    onClick={() => setStep(2)}
                    className="px-6 py-2.5 bg-surface text-text-primary border border-border rounded-lg hover:bg-surface-hover font-medium text-sm transition-all duration-200"
                    disabled={loading}
                  >
                    {t('setup.back')}
                  </button>
                  <button
                    onClick={handleRegister}
                    disabled={loading}
                    className="px-6 py-2.5 bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700 text-white rounded-lg font-medium text-sm transition-all duration-200 disabled:opacity-50 flex items-center gap-2"
                  >
                    {loading ? (
                        <>
                            <Loader2 className="w-4 h-4 animate-spin" />
                            {t('setup.settingUp')}
                        </>
                    ) : (
                        t('setup.completeSetup')
                    )}
                  </button>
                </div>
              </div>
            )}

            {/* Step 4: Success */}
            {step === 4 && (
              <div className="text-center py-12 animate-in zoom-in duration-500">
                <div className="w-20 h-20 rounded-full bg-green-500/20 flex items-center justify-center mx-auto mb-6">
                    <CheckCircle className="w-10 h-10 text-green-500" />
                </div>
                <h2 className="text-2xl font-bold text-text-primary mb-4">
                  {t('setup.step4Title')}
                </h2>
                <p className="text-text-muted text-sm mb-8 max-w-sm mx-auto">
                  {t('setup.step4Subtitle')}
                </p>
                <div className="w-48 h-1.5 bg-surface border border-border mx-auto rounded-full overflow-hidden">
                    <div className="h-full bg-gradient-to-r from-accent to-purple-600 animate-pulse/2"></div>
                </div>
              </div>
            )}
          </div>

          {/* Help text */}
          <div className="mt-8 text-center text-xs text-text-muted">
            <p>{t('setup.needHelp')} <a href="https://github.com/29barra29/PowerDNS-PDNS-MANAGER/blob/main/INSTALL.md" target="_blank" rel="noopener noreferrer" className="text-accent hover:text-accent-hover transition-colors">{t('setup.documentation')}</a></p>
            <p className="mt-2">{(appInfo.app_tagline || t('login.tagline'))}{appInfo.app_version ? ` • v${appInfo.app_version}` : ''}</p>
            {appInfo.app_creator ? <p>{appInfo.app_creator}</p> : null}
          </div>
        </div>
      </div>
    </div>
  );
}