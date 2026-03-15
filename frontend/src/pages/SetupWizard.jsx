import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  User,
  Mail,
  Key,
  Server,
  CheckCircle,
  AlertTriangle
} from 'lucide-react';

export default function SetupWizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [setupStatus, setSetupStatus] = useState(null);

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

  const [pdnsConfig, setPdnsConfig] = useState({
    enabled: false,
    name: 'ns1',
    url: 'http://localhost:8081',
    api_key: '',
  });

  useEffect(() => {
    // Check setup status
    checkSetupStatus();
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
      setError('Passwörter stimmen nicht überein');
      return;
    }

    if (userData.password.length < 8) {
      setError('Passwort muss mindestens 8 Zeichen haben');
      return;
    }

    setLoading(true);

    try {
      // Register first admin
      const res = await fetch('/api/v1/setup/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: userData.username,
          email: userData.email,
          password: userData.password,
          display_name: userData.displayName || userData.username,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Registrierung fehlgeschlagen');
      }

      const data = await res.json();

      // Save token
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('user', JSON.stringify(data.user));

      // Configure email if enabled
      if (emailConfig.enabled) {
        await configureEmail(data.access_token);
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

  const configureEmail = async (token) => {
    try {
      await fetch('/api/v1/setup/configure-email', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          smtp_host: emailConfig.smtp_host,
          smtp_port: emailConfig.smtp_port,
          smtp_user: emailConfig.smtp_user,
          smtp_password: emailConfig.smtp_password,
          smtp_from: emailConfig.smtp_from || emailConfig.smtp_user,
        }),
      });
    } catch (err) {
      console.error('Email configuration failed:', err);
    }
  };

  if (!setupStatus) {
    return (
      <div className="min-h-screen bg-gray-900 flex items-center justify-center">
        <div className="text-white">Lade...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 via-blue-900 to-gray-900">
      <div className="container mx-auto px-4 py-16">
        <div className="max-w-2xl mx-auto">
          {/* Header */}
          <div className="text-center mb-8">
            <h1 className="text-4xl font-bold text-white mb-2">
              🌐 DNS Manager Setup
            </h1>
            <p className="text-gray-300">
              Willkommen! Lass uns deinen DNS Manager einrichten.
            </p>
          </div>

          {/* Progress */}
          <div className="flex justify-between mb-8">
            {[1, 2, 3, 4].map((s) => (
              <div
                key={s}
                className={`flex-1 h-2 mx-1 rounded-full transition-all ${
                  s <= step ? 'bg-blue-500' : 'bg-gray-700'
                }`}
              />
            ))}
          </div>

          {/* Card */}
          <div className="bg-gray-800 rounded-lg shadow-xl p-8">
            {error && (
              <div className="mb-6 p-4 bg-red-900/50 border border-red-500 rounded-lg flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-red-400" />
                <span className="text-red-200">{error}</span>
              </div>
            )}

            {/* Step 1: Admin Account */}
            {step === 1 && (
              <div>
                <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                  <User className="w-6 h-6" />
                  Administrator Account
                </h2>

                <div className="space-y-4">
                  <div>
                    <label className="block text-gray-300 mb-2">Benutzername</label>
                    <input
                      type="text"
                      className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg focus:ring-2 focus:ring-blue-500"
                      value={userData.username}
                      onChange={(e) => setUserData({...userData, username: e.target.value})}
                      placeholder="admin"
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-gray-300 mb-2">E-Mail</label>
                    <input
                      type="email"
                      className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg focus:ring-2 focus:ring-blue-500"
                      value={userData.email}
                      onChange={(e) => setUserData({...userData, email: e.target.value})}
                      placeholder="admin@example.com"
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-gray-300 mb-2">Anzeigename</label>
                    <input
                      type="text"
                      className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg focus:ring-2 focus:ring-blue-500"
                      value={userData.displayName}
                      onChange={(e) => setUserData({...userData, displayName: e.target.value})}
                      placeholder="Administrator"
                    />
                  </div>

                  <div>
                    <label className="block text-gray-300 mb-2">Passwort</label>
                    <input
                      type="password"
                      className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg focus:ring-2 focus:ring-blue-500"
                      value={userData.password}
                      onChange={(e) => setUserData({...userData, password: e.target.value})}
                      placeholder="Mindestens 8 Zeichen"
                      required
                    />
                  </div>

                  <div>
                    <label className="block text-gray-300 mb-2">Passwort wiederholen</label>
                    <input
                      type="password"
                      className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg focus:ring-2 focus:ring-blue-500"
                      value={userData.passwordConfirm}
                      onChange={(e) => setUserData({...userData, passwordConfirm: e.target.value})}
                      placeholder="Passwort bestätigen"
                      required
                    />
                  </div>
                </div>

                <div className="mt-8 flex justify-end">
                  <button
                    onClick={() => setStep(2)}
                    disabled={!userData.username || !userData.email || !userData.password}
                    className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Weiter →
                  </button>
                </div>
              </div>
            )}

            {/* Step 2: Email (Optional) */}
            {step === 2 && (
              <div>
                <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                  <Mail className="w-6 h-6" />
                  E-Mail Konfiguration (Optional)
                </h2>

                <div className="mb-6">
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      className="w-5 h-5 rounded text-blue-600"
                      checked={emailConfig.enabled}
                      onChange={(e) => setEmailConfig({...emailConfig, enabled: e.target.checked})}
                    />
                    <span className="text-white">E-Mail-Benachrichtigungen aktivieren</span>
                  </label>
                </div>

                {emailConfig.enabled && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <label className="block text-gray-300 mb-2">SMTP Server</label>
                        <input
                          type="text"
                          className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg"
                          value={emailConfig.smtp_host}
                          onChange={(e) => setEmailConfig({...emailConfig, smtp_host: e.target.value})}
                        />
                      </div>
                      <div>
                        <label className="block text-gray-300 mb-2">Port</label>
                        <input
                          type="number"
                          className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg"
                          value={emailConfig.smtp_port}
                          onChange={(e) => setEmailConfig({...emailConfig, smtp_port: parseInt(e.target.value)})}
                        />
                      </div>
                    </div>

                    <div>
                      <label className="block text-gray-300 mb-2">SMTP Benutzer</label>
                      <input
                        type="email"
                        className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg"
                        value={emailConfig.smtp_user}
                        onChange={(e) => setEmailConfig({...emailConfig, smtp_user: e.target.value})}
                        placeholder="deine-email@gmail.com"
                      />
                    </div>

                    <div>
                      <label className="block text-gray-300 mb-2">SMTP Passwort</label>
                      <input
                        type="password"
                        className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg"
                        value={emailConfig.smtp_password}
                        onChange={(e) => setEmailConfig({...emailConfig, smtp_password: e.target.value})}
                        placeholder="App-spezifisches Passwort"
                      />
                    </div>

                    <div>
                      <label className="block text-gray-300 mb-2">Absender E-Mail</label>
                      <input
                        type="email"
                        className="w-full px-4 py-2 bg-gray-700 text-white rounded-lg"
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
                    className="px-6 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700"
                  >
                    ← Zurück
                  </button>
                  <button
                    onClick={() => setStep(3)}
                    className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
                  >
                    Weiter →
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: Review & Complete */}
            {step === 3 && (
              <div>
                <h2 className="text-2xl font-bold text-white mb-6 flex items-center gap-2">
                  <Key className="w-6 h-6" />
                  Setup abschließen
                </h2>

                <div className="space-y-4 mb-8">
                  <div className="p-4 bg-gray-700 rounded-lg">
                    <h3 className="font-semibold text-white mb-2">Administrator</h3>
                    <p className="text-gray-300">Benutzername: {userData.username}</p>
                    <p className="text-gray-300">E-Mail: {userData.email}</p>
                  </div>

                  {emailConfig.enabled && (
                    <div className="p-4 bg-gray-700 rounded-lg">
                      <h3 className="font-semibold text-white mb-2">E-Mail</h3>
                      <p className="text-gray-300">SMTP: {emailConfig.smtp_host}:{emailConfig.smtp_port}</p>
                      <p className="text-gray-300">Benutzer: {emailConfig.smtp_user}</p>
                    </div>
                  )}

                  <div className="p-4 bg-blue-900/50 border border-blue-500 rounded-lg">
                    <p className="text-blue-200">
                      PowerDNS Server können nach der Installation über das Admin-Panel hinzugefügt werden.
                    </p>
                  </div>
                </div>

                <div className="flex justify-between">
                  <button
                    onClick={() => setStep(2)}
                    className="px-6 py-2 bg-gray-600 text-white rounded-lg hover:bg-gray-700"
                    disabled={loading}
                  >
                    ← Zurück
                  </button>
                  <button
                    onClick={handleRegister}
                    disabled={loading}
                    className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
                  >
                    {loading ? 'Wird eingerichtet...' : '✓ Setup abschließen'}
                  </button>
                </div>
              </div>
            )}

            {/* Step 4: Success */}
            {step === 4 && (
              <div className="text-center py-8">
                <CheckCircle className="w-16 h-16 text-green-500 mx-auto mb-4" />
                <h2 className="text-2xl font-bold text-white mb-4">
                  Setup erfolgreich!
                </h2>
                <p className="text-gray-300 mb-8">
                  Dein DNS Manager ist bereit. Du wirst gleich zum Dashboard weitergeleitet...
                </p>
                <div className="animate-pulse">
                  <div className="w-16 h-1 bg-blue-500 mx-auto rounded-full"></div>
                </div>
              </div>
            )}
          </div>

          {/* Help text */}
          <div className="mt-8 text-center text-gray-400">
            <p>Brauchst du Hilfe? Schau in die <a href="https://github.com/29barra29/dns-manager/wiki" className="text-blue-400 hover:underline">Dokumentation</a></p>
          </div>
        </div>
      </div>
    </div>
  );
}