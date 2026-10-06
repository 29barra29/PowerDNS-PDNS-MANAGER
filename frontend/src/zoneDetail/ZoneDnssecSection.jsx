import { useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import api from '../api'
import ZoneDnssecCard from '../components/dnssec/ZoneDnssecCard'
import DnssecAddKeyModal from '../components/dnssec/DnssecAddKeyModal'
import DnssecDisableModal from '../components/dnssec/DnssecDisableModal'
import DnssecDsModal from '../components/dnssec/DnssecDsModal'
import DnssecEnableModal from '../components/dnssec/DnssecEnableModal'
import DnssecNsecModal from '../components/dnssec/DnssecNsecModal'
import DnssecRolloverModal from '../components/dnssec/DnssecRolloverModal'
import { canManageDnssec, followUpMessages, keyTypeLabel, withForce } from './dnssecModel.js'
import useZoneDnssec, { DNSSEC_MODAL_KEY, useZoneDnssecAutoload } from './useZoneDnssec'
import { useZoneDetail } from './zoneDetailContext'

// DNSSEC-Bereich der Zonenansicht (F4 §6.4, Plan WS-F4-B). Vertrag mit der Shell (unveraendert seit Welle 0b):
//   - default export  ZoneDnssecSection -> Karte im Records-Tab (tabs/10-records.tab.jsx)
//   - named export    ZoneDnssecHost    -> von der Shell IMMER gemountet (auch waehrend des Ladens); laedt den
//                                          DNSSEC-Status nach jedem loadZone() (subscribeZoneLoaded) und rendert die
//                                          Dialoge (Slot-State 'dnssec.modal' = 'enable'|'disable'|'rollover'|'nsec'|
//                                          'addKey'|'ds'|null; die Kopf-Aktion 10-dnssec-ds setzt 'ds').
// Banner der Seite: Erfolg (+ Serial/NOTIFY-Ergebnis), Warnung bei notify_error/serial_error (bleibt stehen).

const CONFIRM_KEYS = {
    activate: 'dnssec.confirmActivate', deactivate: 'dnssec.confirmDeactivate', publish: 'dnssec.confirmPublish',
    unpublish: 'dnssec.confirmUnpublish', delete: 'dnssec.confirmDelete',
}
const OK_KEYS = {
    activate: 'dnssec.keyActivatedOk', deactivate: 'dnssec.keyDeactivatedOk', publish: 'dnssec.keyPublishedOk',
    unpublish: 'dnssec.keyUnpublishedOk', delete: 'dnssec.keyDeletedOk',
}
const ACTION_BODY = {
    activate: { active: true }, deactivate: { active: false }, publish: { published: true }, unpublish: { published: false },
}
const CREATE_WARNING_KEYS = { algorithm_prepublish_unsigned: 'dnssec.warnAlgorithmPrepublishUnsigned' }

/** Gemeinsame Helfer fuer Karte und Host: Banner + Nachladen nach einer Aenderung. */
function useDnssecFeedback() {
    const { t } = useTranslation()
    const { setSuccess, setWarning, loadZone } = useZoneDetail()
    const dnssec = useZoneDnssec()
    const { reload } = dnssec

    const report = useCallback((message, details, extraWarnings = []) => {
        const f = followUpMessages(details, t)
        setSuccess([message, ...f.info].filter(Boolean).join(' '))
        const warnings = [...f.warnings, ...extraWarnings]
        if (warnings.length) setWarning(warnings.join('\n'))
    }, [t, setSuccess, setWarning])

    /** Nach einer Mutation: Status ohne Peer-Vergleich neu laden; bei Serial-Erhoehung auch die Records (SOA). */
    const afterMutation = useCallback(async (details) => {
        if (details?.serial_bumped) loadZone({ silent: true })
        await reload({ peers: false })
    }, [loadZone, reload])

    return { t, dnssec, report, afterMutation }
}

/** Karte "DNSSEC" im Records-Tab. */
export default function ZoneDnssecSection() {
    const { server, zoneId, canEdit, setError, setSlotState } = useZoneDetail()
    const { t, dnssec, report, afterMutation } = useDnssecFeedback()
    const [busyKeyId, setBusyKeyId] = useState(null)
    const canManage = canManageDnssec(canEdit, dnssec.status)

    async function handleKeyAction(action, key) {
        const vars = { id: key.id, type: keyTypeLabel(key), tag: key.key_tag ?? '—' }
        if (!window.confirm(t(CONFIRM_KEYS[action], vars))) return
        setBusyKeyId(key.id)
        try {
            const res = await withForce((force) => (action === 'delete'
                ? api.deleteDnssecKey(server, zoneId, key.id, { force })
                : api.updateDnssecKey(server, zoneId, key.id, { ...ACTION_BODY[action], ...(force ? { force: true } : {}) })), t)
            if (res === null) return // Sicherheitsrueckfrage abgebrochen
            report(t(res.details?.unchanged ? 'dnssec.unchanged' : OK_KEYS[action], vars), res.details)
            await afterMutation(res.details)
        } catch (err) {
            setError(err.message || String(err))
        } finally {
            setBusyKeyId(null)
        }
    }

    return (
        <ZoneDnssecCard
            status={dnssec.status}
            loading={dnssec.loading}
            error={dnssec.error}
            canManage={canManage}
            busyKeyId={busyKeyId}
            onReload={() => dnssec.reload({ peers: true })}
            onOpen={(modal) => setSlotState(DNSSEC_MODAL_KEY, modal)}
            onKeyAction={handleKeyAction}
        />
    )
}

/** Immer gemountet: laedt den Status nach jedem Laden der Zone und zeigt die DNSSEC-Dialoge. */
export function ZoneDnssecHost() {
    useZoneDnssecAutoload()
    const ctx = useZoneDetail()
    const { server, zoneId, zoneName, records, loading, canEdit, slotState, setSlotState } = ctx
    const { t, dnssec, report, afterMutation } = useDnssecFeedback()
    const modal = slotState[DNSSEC_MODAL_KEY] || null
    const setModal = useCallback((value) => setSlotState(DNSSEC_MODAL_KEY, value), [setSlotState])

    // Wie 2.4.1: waehrend des Ganzseiten-Ladens ist kein Dialog sichtbar.
    if (!modal || loading) return null

    const status = dnssec.status
    const close = () => setModal(null)
    const common = { server, zoneId, zoneName, status, onClose: close }

    async function done(message, res, { extraWarnings = [], next = null } = {}) {
        setModal(next)
        report(message, res?.details, extraWarnings)
        await afterMutation(res?.details)
    }

    switch (modal) {
    case 'ds':
        return (
            <DnssecDsModal
                zoneName={zoneName}
                status={status}
                loading={dnssec.loading}
                error={dnssec.error}
                canManage={canManageDnssec(canEdit, status)}
                onEnable={() => setModal('enable')}
                onClose={close}
                onReload={() => dnssec.reload({ peers: true })}
            />
        )
    case 'enable':
        return (
            <DnssecEnableModal
                {...common}
                onDone={(res) => {
                    const already = !!res?.details?.already_enabled
                    // nach dem ersten Aktivieren direkt zum DS-Assistenten (F4 §2.2 Nr. 5)
                    done(t(already ? 'dnssec.alreadyEnabled' : 'zoneDetail.dnssecEnabledOk'), res, { next: already ? null : 'ds' })
                }}
            />
        )
    case 'disable':
        return (
            <DnssecDisableModal
                {...common}
                onReload={() => dnssec.reload({ peers: false })}
                onDone={(res) => {
                    const d = res?.details || {}
                    const extra = d.rectify_error ? [t('dnssec.disableRectifyWarn', { error: d.rectify_error })] : []
                    done(t(d.already_disabled ? 'dnssec.alreadyDisabled' : 'dnssec.disabledOk'), res, { extraWarnings: extra })
                }}
            />
        )
    case 'nsec':
        return (
            <DnssecNsecModal
                {...common}
                onDone={(res) => done(t(res?.details?.unchanged ? 'dnssec.unchanged' : 'dnssec.nsecSavedOk'), res)}
            />
        )
    case 'addKey':
        return (
            <DnssecAddKeyModal
                {...common}
                onDone={(res) => {
                    const key = res?.details?.key || {}
                    const extra = (res?.details?.warnings || [])
                        .map((code) => (CREATE_WARNING_KEYS[code] ? t(CREATE_WARNING_KEYS[code]) : null))
                        .filter(Boolean)
                    done(t('dnssec.keyCreatedOk', { id: key.id ?? '?', tag: key.key_tag ?? '—' }), res, { extraWarnings: extra })
                }}
            />
        )
    case 'rollover':
        return (
            <DnssecRolloverModal
                {...common}
                records={records}
                loading={dnssec.loading}
                onChanged={afterMutation}
                onFinished={(message) => {
                    close()
                    report(message, null)
                }}
            />
        )
    default:
        return null // unbekannter Wert im Slot-State: nichts anzeigen
    }
}
