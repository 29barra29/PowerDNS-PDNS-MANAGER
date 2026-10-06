import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Check, Copy } from 'lucide-react'

// Einrichtungsanleitung DynDNS (F9 §2.2): FRITZ!Box, andere Router (dyndns2), curl (dyndns2 und JSON), ddclient,
// Antwortcodes. Erscheint aufklappbar in der DynDNS-Karte und in der Einmal-Anzeige eines neuen Tokens - dort mit
// eingesetztem Token und erstem Hostnamen.
// Props: { baseUrl, token = '<TOKEN>', hostname = 'home.example.com' }
//   baseUrl: info.base_url (System-Setting app_base_url); leer -> Adresse des Browsers + Hinweis.
// Der Token steht immer im Passwort-Feld (HTTP-Basic-Auth) bzw. im Bearer-Header, nie in der URL: der Server
// lehnt Tokens im Query-String ab und deaktiviert sie (sie landen sonst in Access-/Proxy-Logs).

const EXAMPLE_IP = '203.0.113.5'

function cleanBase(baseUrl) {
    const fallback = typeof window !== 'undefined' ? window.location.origin : 'https://dns.example.com'
    const raw = String(baseUrl || '').trim().replace(/\/+$/, '')
    return { base: raw || fallback, missing: !raw }
}

function hostOf(base) {
    try {
        return new URL(base).host
    } catch {
        return base.replace(/^[a-z]+:\/\//i, '').replace(/\/.*$/, '')
    }
}

// Code-Block mit Kopier-Button (Erfolg/Fehler sichtbar, Text bleibt stehen)
function CodeBlock({ code, label }) {
    const { t } = useTranslation()
    const [state, setState] = useState('idle') // idle | copied | failed
    const timer = useRef(null)
    useEffect(() => () => clearTimeout(timer.current), [])

    async function copy() {
        clearTimeout(timer.current)
        try {
            if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
            await navigator.clipboard.writeText(code)
            setState('copied')
            timer.current = setTimeout(() => setState('idle'), 2000)
        } catch {
            setState('failed')
        }
    }

    return (
        <div className="relative">
            <pre
                className="p-2 pr-10 rounded-lg bg-bg-primary border border-border text-[11px] font-mono text-text-primary overflow-x-auto whitespace-pre-wrap break-all"
                aria-label={label}
            >
                {code}
            </pre>
            <button
                type="button"
                onClick={copy}
                className="absolute top-1.5 right-1.5 p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover"
                title={t('common.copy')}
                aria-label={t('common.copy')}
            >
                {state === 'copied' ? <Check className="w-3.5 h-3.5 text-success" aria-hidden="true" /> : <Copy className="w-3.5 h-3.5" aria-hidden="true" />}
            </button>
            <span aria-live="polite" className="text-[11px]">
                {state === 'copied' && <span className="text-success">{t('dyndns.copied')}</span>}
                {state === 'failed' && <span className="text-danger">{t('secretModal.copyFailed')}</span>}
            </span>
        </div>
    )
}

function Field({ label, value, mono = true }) {
    return (
        <div className="grid grid-cols-1 sm:grid-cols-[9rem_1fr] gap-x-3 gap-y-0.5 text-xs">
            <dt className="text-text-muted">{label}</dt>
            <dd className={`text-text-primary break-all ${mono ? 'font-mono' : ''}`}>{value}</dd>
        </div>
    )
}

export default function DyndnsGuide({ baseUrl, token = '<TOKEN>', hostname = 'home.example.com' }) {
    const { t } = useTranslation()
    const { base, missing } = cleanBase(baseUrl)
    const host = hostOf(base)
    const insecure = /^http:\/\//i.test(base)
    const hostShown = String(hostname || 'home.example.com').replace(/\.$/, '')
    const updateUrl = `${base}/nic/update`

    // FRITZ!Box ersetzt <domain>, <ipaddr> und <ip6addr> selbst; Benutzer/Passwort NICHT als Platzhalter in der URL
    const fritzUrl = `${updateUrl}?hostname=<domain>&myip=<ipaddr>,<ip6addr>`
    const curlBasic = `curl -u "dyndns:${token}" "${updateUrl}?hostname=${hostShown}&myip=${EXAMPLE_IP}"`
    const curlAuto = `curl -u "dyndns:${token}" "${updateUrl}?hostname=${hostShown}"`
    const curlJson = `curl -H "Authorization: Bearer ${token}" "${base}/api/v1/dyndns/update?hostname=${hostShown}"`
    const ddclient = [
        'protocol=dyndns2',
        'use=web, web=https://api.ipify.org',
        'ssl=yes',
        `server=${host}`,
        'login=dyndns',
        `password='${token}'`,
        hostShown,
    ].join('\n')

    return (
        <div className="space-y-4 text-sm text-text-secondary">
            <p className="text-xs">{t('dyndns.guideIntro')}</p>
            {missing && <p className="text-xs text-text-muted">{t('dyndns.guideBaseUrlMissing')}</p>}
            {insecure && (
                <p role="alert" className="flex items-start gap-2 p-2 rounded-lg bg-danger/10 border border-danger/30 text-danger text-xs">
                    <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                    {t('dyndns.guideHttpWarning')}
                </p>
            )}

            <section className="space-y-2">
                <h4 className="text-sm font-semibold text-text-primary">{t('dyndns.guideFritzTitle')}</h4>
                <p className="text-xs">{t('dyndns.guideFritzSteps')}</p>
                <dl className="space-y-1.5">
                    <Field label={t('dyndns.guideUpdateUrl')} value={fritzUrl} />
                    <Field label={t('dyndns.guideDomain')} value={hostShown} />
                    <Field label={t('dyndns.guideUsername')} value={t('dyndns.guideUsernameValue')} mono={false} />
                    <Field
                        label={t('dyndns.guidePassword')}
                        value={token === '<TOKEN>' ? t('dyndns.guidePasswordValue') : token}
                        mono={token !== '<TOKEN>'}
                    />
                </dl>
                <CodeBlock code={fritzUrl} label={t('dyndns.guideUpdateUrl')} />
                <p className="flex items-start gap-2 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-200 text-xs">
                    <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                    {t('dyndns.guideNoPassInUrl')}
                </p>
            </section>

            <section className="space-y-2">
                <h4 className="text-sm font-semibold text-text-primary">{t('dyndns.guideGenericTitle')}</h4>
                <p className="text-xs">{t('dyndns.guideGenericBody', { host })}</p>
            </section>

            <section className="space-y-2">
                <h4 className="text-sm font-semibold text-text-primary">{t('dyndns.guideCurlTitle')}</h4>
                <p className="text-xs">{t('dyndns.guideCurlBasic')}</p>
                <CodeBlock code={curlBasic} label={t('dyndns.guideCurlTitle')} />
                <CodeBlock code={curlAuto} label={t('dyndns.guideCurlTitle')} />
                <p className="text-xs">{t('dyndns.guideCurlJson')}</p>
                <CodeBlock code={curlJson} label={t('dyndns.guideCurlTitle')} />
                <p className="text-xs text-text-muted">{t('dyndns.guideCurlHint')}</p>
            </section>

            <section className="space-y-2">
                <h4 className="text-sm font-semibold text-text-primary">{t('dyndns.guideDdclientTitle')}</h4>
                <CodeBlock code={ddclient} label={t('dyndns.guideDdclientTitle')} />
            </section>

            <section className="space-y-1">
                <h4 className="text-sm font-semibold text-text-primary">{t('dyndns.guideResponsesTitle')}</h4>
                <p className="text-xs">{t('dyndns.guideResponses')}</p>
                <p className="text-xs text-text-muted">{t('dyndns.guideIpHint')}</p>
            </section>
        </div>
    )
}
