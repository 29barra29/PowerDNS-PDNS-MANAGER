import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Webhook, X } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import {
    NAME_MAX, URL_MAX,
    buildEventFilters, categoryLabel, createWebhookPayload, diffWebhookUpdate, eventLabel, groupEvents,
    initialWebhookForm, isCoveredEvent, parseEventFilters, removeLegacyFilter, toggleEventToken,
    validateWebhookForm,
} from './webhookUi'

// Anlegen/Bearbeiten eines Webhooks (F6 2.2/2.3, 6.3/6.4).
// Props: { mode: 'create'|'edit', hook, availableEvents, eventCategories, isAdmin, onClose, onSaved(result) }
// Fehler erscheinen nur im Modal (ModalErrorBanner). Beim Bearbeiten werden nur geaenderte Felder gesendet;
// ohne Aenderung schliesst das Modal ohne Request (onSaved(null)).
export default function WebhookFormModal({ mode, hook, availableEvents, eventCategories, isAdmin, onClose, onSaved }) {
    const { t } = useTranslation()
    const isEdit = mode === 'edit'
    const titleId = useId()
    const formRef = useRef(null)
    const nameRef = useRef(null)
    const groups = useMemo(() => groupEvents(availableEvents, eventCategories), [availableEvents, eventCategories])
    const [form, setForm] = useState(() => initialWebhookForm(isEdit ? hook : null))
    const [events, setEvents] = useState(() => parseEventFilters(isEdit ? hook?.events : ['*'], groups))
    const [saving, setSaving] = useState(false)
    const [modalError, setModalError] = useState('')
    const urlUnreadable = isEdit && hook?.has_url === false

    useEffect(() => {
        nameRef.current?.focus()
    }, [])

    // ESC schliesst (nicht waehrend des Speicherns), Strg/Cmd+Enter sendet ab.
    useEffect(() => {
        function onKey(e) {
            if (e.key === 'Escape' && !saving) {
                e.preventDefault()
                onClose()
            } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !saving) {
                e.preventDefault()
                formRef.current?.requestSubmit()
            }
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [saving, onClose])

    function setField(key, value) {
        setForm((f) => ({ ...f, [key]: value }))
    }

    async function handleSubmit(e) {
        e.preventDefault()
        if (saving) return
        setModalError('')
        const filters = buildEventFilters(events, groups)
        const errKey = validateWebhookForm(form, filters, { urlRequired: !isEdit || urlUnreadable })
        if (errKey) {
            setModalError(t(errKey, { max: NAME_MAX }))
            return
        }
        let body
        if (isEdit) {
            body = diffWebhookUpdate(hook, form, filters)
            if (Object.keys(body).length === 0) {
                onSaved(null)
                return
            }
        } else {
            body = createWebhookPayload(form, filters)
        }
        setSaving(true)
        try {
            const res = isEdit ? await api.updateWebhook(hook.id, body) : await api.createWebhook(body)
            onSaved(res)
        } catch (err) {
            setModalError(err.message)
            setSaving(false)
        }
    }

    const tokenChecked = (token) => events.all || events.tokens.has(token)
    const inputCls = 'w-full px-3 py-2 text-sm'

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
            <div
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className="glass-card w-full max-w-2xl max-h-[90vh] flex flex-col"
            >
                <div className="flex items-center justify-between gap-3 px-6 pt-5 pb-3 border-b border-border/50">
                    <h2 id={titleId} className="text-lg font-bold flex items-center gap-2 text-text-primary">
                        <Webhook className="w-5 h-5" aria-hidden="true" />
                        {isEdit ? t('webhooks.editTitle') : t('webhooks.createTitle')}
                    </h2>
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={saving}
                        className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-40"
                        aria-label={t('common.close')}
                        title={t('common.close')}
                    >
                        <X className="w-5 h-5" aria-hidden="true" />
                    </button>
                </div>
                <form ref={formRef} onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-6 py-4 space-y-5" noValidate>
                    <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />

                    <div>
                        <label htmlFor={`${titleId}-name`} className="block text-xs font-medium text-text-secondary mb-0.5">
                            {t('settings.integrations.webhookFieldName')}
                        </label>
                        <p className="text-[11px] text-text-muted mb-1">{t('webhooks.nameHint')}</p>
                        <input
                            id={`${titleId}-name`}
                            ref={nameRef}
                            value={form.name}
                            maxLength={NAME_MAX}
                            onChange={(e) => setField('name', e.target.value)}
                            className={inputCls}
                            autoComplete="off"
                        />
                    </div>

                    <div>
                        <label htmlFor={`${titleId}-url`} className="block text-xs font-medium text-text-secondary mb-0.5">
                            {t('webhooks.fieldUrl')}
                        </label>
                        <p className="text-[11px] text-text-muted mb-1">{t('webhooks.urlHint')}</p>
                        {urlUnreadable && (
                            <p className="text-xs text-danger mb-1">{t('webhooks.urlUnreadableEdit')}</p>
                        )}
                        <input
                            id={`${titleId}-url`}
                            type="url"
                            inputMode="url"
                            value={form.url}
                            maxLength={URL_MAX}
                            onChange={(e) => setField('url', e.target.value)}
                            className={`${inputCls} font-mono`}
                            placeholder="https://…"
                            autoComplete="off"
                            spellCheck={false}
                        />
                    </div>

                    <fieldset>
                        <legend className="text-xs font-medium text-text-secondary mb-1">{t('webhooks.scopeLabel')}</legend>
                        <div className="space-y-1.5">
                            <label className="flex items-start gap-2 text-sm cursor-pointer">
                                <input
                                    type="radio"
                                    name={`${titleId}-scope`}
                                    className="mt-0.5"
                                    checked={form.scope === 'own'}
                                    onChange={() => setField('scope', 'own')}
                                />
                                <span>{t('webhooks.scopeOwn')}</span>
                            </label>
                            <label className="flex items-start gap-2 text-sm cursor-pointer">
                                <input
                                    type="radio"
                                    name={`${titleId}-scope`}
                                    className="mt-0.5"
                                    checked={form.scope === 'zones'}
                                    onChange={() => setField('scope', 'zones')}
                                />
                                <span>{isAdmin ? t('webhooks.scopeZonesAdmin') : t('webhooks.scopeZones')}</span>
                            </label>
                        </div>
                        {form.scope === 'zones' && (
                            <p className="mt-2 text-xs text-amber-300">{t('webhooks.scopeHint')}</p>
                        )}
                    </fieldset>

                    <fieldset>
                        <legend className="text-xs font-medium text-text-secondary mb-0.5">{t('webhooks.fieldEvents')}</legend>
                        <p className="text-[11px] text-text-muted mb-2">{t('webhooks.eventsHint')}</p>
                        <label className="flex items-center gap-2 text-sm font-medium cursor-pointer mb-2">
                            <input
                                type="checkbox"
                                checked={events.all}
                                onChange={() => setEvents((s) => toggleEventToken(s, '*'))}
                            />
                            {t('webhooks.eventsAll')}
                        </label>
                        <div className={`grid gap-3 sm:grid-cols-2 ${events.all ? 'opacity-60' : ''}`}>
                            {groups.map((g) => (
                                <div key={g.category} className="rounded-lg border border-border/50 p-3 space-y-1">
                                    <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={tokenChecked(g.category)}
                                            disabled={events.all}
                                            onChange={() => setEvents((s) => toggleEventToken(s, g.category))}
                                        />
                                        {t('webhooks.categoryAll', { category: categoryLabel(t, g.category) })}
                                    </label>
                                    <div className="pl-5 space-y-1">
                                        {g.events.map((ev) => {
                                            const covered = isCoveredEvent(events, ev)
                                            return (
                                                <label key={ev} className="flex items-baseline gap-2 text-sm cursor-pointer">
                                                    <input
                                                        type="checkbox"
                                                        checked={covered || events.tokens.has(ev)}
                                                        disabled={covered}
                                                        onChange={() => setEvents((s) => toggleEventToken(s, ev))}
                                                    />
                                                    <span className="min-w-0">
                                                        {eventLabel(t, ev)}{' '}
                                                        <span className="font-mono text-[10px] text-text-muted">{ev}</span>
                                                    </span>
                                                </label>
                                            )
                                        })}
                                    </div>
                                </div>
                            ))}
                        </div>
                        {events.legacy.length > 0 && !events.all && (
                            <div className="mt-3">
                                <p className="text-xs font-medium text-text-secondary">{t('webhooks.eventsLegacy')}</p>
                                <p className="text-[11px] text-text-muted mb-1">{t('webhooks.eventsLegacyHint')}</p>
                                <div className="flex flex-wrap gap-1.5">
                                    {events.legacy.map((l) => (
                                        <span key={l} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-border bg-bg-hover text-xs font-mono">
                                            {l}
                                            <button
                                                type="button"
                                                onClick={() => setEvents((s) => removeLegacyFilter(s, l))}
                                                className="text-text-muted hover:text-danger"
                                                aria-label={t('webhooks.removeFilter', { filter: l })}
                                                title={t('webhooks.removeFilter', { filter: l })}
                                            >
                                                <X className="w-3 h-3" aria-hidden="true" />
                                            </button>
                                        </span>
                                    ))}
                                </div>
                            </div>
                        )}
                    </fieldset>

                    <div className="flex flex-wrap justify-end gap-2 pt-2 border-t border-border/50">
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={saving}
                            className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-40"
                        >
                            {t('common.cancel')}
                        </button>
                        <button
                            type="submit"
                            disabled={saving}
                            className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-gradient-to-r from-accent to-purple-600 disabled:opacity-60 inline-flex items-center gap-2"
                        >
                            {saving && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                            {t('common.save')}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    )
}
