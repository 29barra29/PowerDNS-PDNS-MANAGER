import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Check as CheckIcon, Copy, Eye, EyeOff, Info, Loader2, ShieldAlert } from 'lucide-react'

// Kleine Bausteine fuer den Admin-Tab "Anmeldung / SSO" (F10 §2.5/§6.4, WS-F10-APP-FE).
// Markup nach Karten-Konvention der Einstellungsseite (glass-card, Icon-Kachel, Primaer-Button Gradient).

const NOTICE_TONES = {
    warning: 'bg-amber-500/10 border-amber-500/30 text-amber-300',
    danger: 'bg-danger/10 border-danger/30 text-danger',
    info: 'bg-sky-500/10 border-sky-500/30 text-sky-300',
    success: 'bg-success/10 border-success/30 text-success',
}

export function Notice({ tone = 'info', children, role }) {
    const Icon = tone === 'danger' ? ShieldAlert : tone === 'warning' ? AlertTriangle : Info
    return (
        <div role={role} className={`p-3 rounded-lg border text-sm flex items-start gap-2 ${NOTICE_TONES[tone] || NOTICE_TONES.info}`}>
            <Icon className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
            <div className="min-w-0 flex-1 break-words space-y-1">{children}</div>
        </div>
    )
}

export function Card({ icon: Icon, title, subtitle, children }) {
    return (
        <div className="glass-card p-6 space-y-5">
            <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center shrink-0">
                    <Icon className="w-5 h-5 text-accent-light" aria-hidden="true" />
                </div>
                <div className="min-w-0">
                    <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
                    {subtitle && <p className="text-sm text-text-muted">{subtitle}</p>}
                </div>
            </div>
            {children}
        </div>
    )
}

export function Field({ id, label, hint, children }) {
    return (
        <div>
            <label htmlFor={id} className="block text-sm font-medium text-text-secondary mb-1.5">{label}</label>
            {children}
            {hint && <p className="text-xs text-text-muted mt-1">{hint}</p>}
        </div>
    )
}

export function TextInput({ id, value, onChange, mono = false, ...rest }) {
    return (
        <input
            id={id}
            type="text"
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value)}
            className={`w-full px-3 py-2 text-sm ${mono ? 'font-mono' : ''}`}
            autoComplete="off"
            spellCheck={false}
            {...rest}
        />
    )
}

export function LinesInput({ id, value, onChange, rows = 3, ...rest }) {
    return (
        <textarea
            id={id}
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value)}
            rows={rows}
            className="w-full px-3 py-2 font-mono text-xs"
            spellCheck={false}
            {...rest}
        />
    )
}

export function Select({ id, value, onChange, options, ...rest }) {
    return (
        <select id={id} value={value} onChange={(e) => onChange(e.target.value)} className="w-full md:w-2/3 px-3 py-2 text-sm" {...rest}>
            {options.map((o) => (
                <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
            ))}
        </select>
    )
}

export function Check({ id, checked, onChange, label, hint, disabled }) {
    return (
        <label htmlFor={id} className={`flex items-start gap-2 text-sm text-text-secondary ${disabled ? 'opacity-60' : ''}`}>
            <input id={id} type="checkbox" className="mt-0.5" checked={!!checked} onChange={(e) => onChange(e.target.checked)} disabled={disabled} />
            <span>
                {label}
                {hint && <span className="block text-xs text-text-muted">{hint}</span>}
            </span>
        </label>
    )
}

// Secret-Feld: Neueingabe (leer = gespeichertes behalten) und "Gespeichertes Secret entfernen".
export function SecretField({ id, label, isSet, unreadable, value, onChange, remove, onRemove }) {
    const { t } = useTranslation()
    const [show, setShow] = useState(false)
    return (
        <Field id={id} label={label} hint={isSet ? t('settings.sso.secretSet') : t('settings.sso.secretNotSet')}>
            <div className="relative">
                <input
                    id={id}
                    type={show ? 'text' : 'password'}
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    placeholder={isSet ? '••••••••' : ''}
                    className="w-full px-3 py-2 pr-10 text-sm font-mono"
                    autoComplete="new-password"
                    maxLength={1000}
                    disabled={remove}
                />
                <button
                    type="button"
                    onClick={() => setShow((s) => !s)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary"
                    aria-label={show ? t('settings.sso.secretHide') : t('settings.sso.secretShow')}
                >
                    {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
            </div>
            {unreadable && (
                <p className="text-xs text-danger mt-1">{t('settings.sso.secretUnreadable')}</p>
            )}
            {isSet && (
                <div className="mt-2">
                    <Check id={`${id}-remove`} checked={remove} onChange={onRemove} label={t('settings.sso.secretRemove')} />
                </div>
            )}
        </Field>
    )
}

// Nur-Lesen-Wert mit Kopieren-Knopf (Redirect-URI, Notfall-URL)
export function CopyValue({ value }) {
    const { t } = useTranslation()
    const [copied, setCopied] = useState(false)
    async function copy() {
        try {
            await navigator.clipboard.writeText(value)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        } catch {
            setCopied(false)
        }
    }
    return (
        <div className="flex flex-wrap items-center gap-2">
            <code className="text-xs font-mono break-all bg-bg-primary/80 px-2 py-1.5 rounded border border-border flex-1 min-w-0">{value}</code>
            <button
                type="button"
                onClick={copy}
                className="shrink-0 px-2 py-1.5 rounded-lg border border-border hover:bg-bg-hover text-text-secondary text-xs flex items-center gap-1"
                title={t('common.copy')}
            >
                {copied ? <CheckIcon className="w-4 h-4 text-success" /> : <Copy className="w-4 h-4" />}
                {copied ? t('settings.sso.copied') : t('common.copy')}
            </button>
        </div>
    )
}

export function SaveBar({ busy, saveKey, testKey, onSave, onTest, saveDisabled, children }) {
    const { t } = useTranslation()
    return (
        <div className="flex flex-wrap items-center gap-3 pt-2">
            {onTest && (
                <button
                    type="button"
                    onClick={onTest}
                    disabled={!!busy}
                    className="px-4 py-2 rounded-lg border border-border hover:bg-bg-hover text-text-primary text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                    {busy === testKey ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : null}
                    {busy === testKey ? t('settings.sso.testing') : t('settings.sso.test')}
                </button>
            )}
            <button
                type="button"
                onClick={onSave}
                disabled={!!busy || saveDisabled}
                className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
            >
                {busy === saveKey ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <CheckIcon className="w-4 h-4" aria-hidden="true" />}
                {t('common.save')}
            </button>
            {children}
        </div>
    )
}

export function WarningList({ warnings }) {
    const { t } = useTranslation()
    if (!warnings || !warnings.length) return null
    return (
        <Notice tone="warning" role="status">
            <p className="font-medium">{t('settings.sso.warnings')}</p>
            <ul className="list-disc pl-4 space-y-0.5">
                {warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
        </Notice>
    )
}
