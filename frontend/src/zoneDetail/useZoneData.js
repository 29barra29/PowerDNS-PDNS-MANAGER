import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import api from '../api'
import { fanoutSummary, formatFanoutErrors, formatFanoutWarnings } from '../lib/fanout.js'

// Daten und Handler der Zonenansicht (Plan B.14). Verhalten wie 2.4.1 (ZoneDetailPage), mit drei Aenderungen:
//  - loadZone laedt Records und Zonen-Metadaten parallel (F8-F08); DNSSEC-Daten laedt der DNSSEC-Abschnitt selbst
//    ueber subscribeZoneLoaded (F4-B ersetzt ihn, ohne Shell oder Hook anzufassen).
//  - handleDelete(record, extra?) wertet das Fan-out-Ergebnis aus (lib/fanout.js), wie Anlegen/Aendern.
//  - nicht geladene Server ('skipped (not loaded: ...)') erscheinen als gelbe Warnung [D4].
// Die Schnittstelle ist in zoneDetailContext.js beschrieben.
export default function useZoneData(server, zoneId) {
    const { t } = useTranslation()

    const [records, setRecords] = useState([])
    /** Metadaten von GET /zones/.../detail (u. a. dnssec: boolean, kind, serial) */
    const [zoneMeta, setZoneMeta] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [warning, setWarning] = useState('')
    /** Offener Record-Dialog: { seq, mode: 'add'|'edit'|'clone', record } oder null */
    const [recordForm, setRecordForm] = useState(null)
    const [slotState, setSlotStateMap] = useState({})
    /** letztes Lade-Ereignis; ein Effekt verteilt es an die Abonnenten */
    const [loadEvent, setLoadEvent] = useState(null)
    /** seq des letzten erfolgreichen Ladevorgangs (0 = noch keiner) */
    const [zoneLoadSeq, setZoneLoadSeq] = useState(0)

    const [me, setMe] = useState(() => api.getUser())
    const [allServers, setAllServers] = useState([])

    const loadSeqRef = useRef(0)
    const formSeqRef = useRef(0)
    const listenersRef = useRef(new Set())

    useEffect(() => {
        api.getMe().then((u) => { api.setUser(u); setMe(u) }).catch(() => {})
    }, [])
    useEffect(() => {
        api.getServers().then((d) => setAllServers(d.servers || [])).catch(() => {})
    }, [])

    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    const zoneName = (zoneId || '').replace(/\.$/, '')
    const zoneKey = useMemo(() => {
        const z = (zoneId || '').trim().toLowerCase()
        if (!z) return ''
        return z.endsWith('.') ? z : `${z}.`
    }, [zoneId])

    const currentServerInfo = useMemo(
        () => allServers.find((s) => s.name === server) || null,
        [allServers, server]
    )
    const otherWritableServers = useMemo(
        () => allServers.filter((s) => s.name !== server && s.allow_writes !== false && s.is_reachable),
        [allServers, server]
    )
    const serverCanWrite = currentServerInfo ? currentServerInfo.allow_writes !== false : true
    const userCanEdit = !me || me.role === 'admin' || me.zone_permissions?.[zoneKey] !== 'read'
    const canEdit = userCanEdit && serverCanWrite
    const isAdmin = me?.role === 'admin'

    /** Laedt Records + Metadaten parallel; setzt Zustand erst nach dem Laden (keine synchronen setState-Aufrufe). */
    const fetchZone = useCallback(async (silent) => {
        const seq = ++loadSeqRef.current
        const [recRes, metaRes] = await Promise.allSettled([
            api.listRecords(server, zoneId),
            api.getZone(server, zoneId),
        ])
        // Ein neuerer Ladevorgang hat uebernommen - dessen Ergebnis gilt.
        if (seq !== loadSeqRef.current) return { seq, ok: false, stale: true, silent }
        let event
        if (recRes.status === 'fulfilled') {
            const list = recRes.value?.records || []
            const meta = metaRes.status === 'fulfilled' ? metaRes.value : null
            setRecords(list)
            setZoneMeta(meta)
            setZoneLoadSeq(seq)
            event = { seq, ok: true, silent, records: list, zoneMeta: meta, error: null }
        } else {
            const err = recRes.reason
            setError(err?.message || String(err))
            event = { seq, ok: false, silent, records: null, zoneMeta: null, error: err }
        }
        setLoading(false)
        setLoadEvent(event)
        return event
    }, [server, zoneId])

    /** Zone neu laden. opts.silent: ohne Ganzseiten-Spinner (Panels bleiben gemountet, F7 §6.4). */
    const loadZone = useCallback((opts = {}) => {
        const silent = !!opts.silent
        if (!silent) setLoading(true)
        return fetchZone(silent)
    }, [fetchZone])

    // Erstes Laden (loading ist bereits true); per Microtask, damit der Effekt selbst keinen Zustand setzt.
    useEffect(() => { queueMicrotask(() => { fetchZone(false) }) }, [fetchZone])

    /** Abonnenten werden nach dem Rendern des neuen Stands benachrichtigt (Kind-Effekte laufen vorher). */
    useEffect(() => {
        if (!loadEvent) return
        for (const cb of Array.from(listenersRef.current)) {
            try {
                cb(loadEvent)
            } catch (err) {
                console.error('subscribeZoneLoaded: Abonnent hat geworfen', err)
            }
        }
    }, [loadEvent])

    const subscribeZoneLoaded = useCallback((cb) => {
        if (typeof cb !== 'function') return () => {}
        listenersRef.current.add(cb)
        return () => { listenersRef.current.delete(cb) }
    }, [])

    const setSlotState = useCallback((key, value) => {
        setSlotStateMap((prev) => {
            const next = typeof value === 'function' ? value(prev[key]) : value
            if (Object.is(prev[key], next)) return prev
            return { ...prev, [key]: next }
        })
    }, [])

    /** Fan-out-Ergebnis (details bzw. details.fanout) auswerten: Fehler rot, nicht geladene Server gelb. */
    const reportFanout = useCallback((details) => {
        const summary = fanoutSummary(details)
        // Wie 2.4.1: Teilfehler auf Peer-Servern nicht verschlucken (Primary war ok, Peer nicht).
        if (summary.hasErrors) setError(formatFanoutErrors(summary.errors))
        setWarning(summary.hasWarnings
            ? t('zoneDetail.fanoutNotLoaded', { servers: formatFanoutWarnings(summary.warnings) })
            : '')
        return summary
    }, [t])

    const resolveName = useCallback((name) => {
        let fqdn
        if (name === '@') fqdn = zoneName
        else if (!name.includes(zoneName)) fqdn = `${name}.${zoneName}`
        else fqdn = name
        if (!fqdn.endsWith('.')) fqdn = fqdn + '.'
        return fqdn
    }, [zoneName])

    const relativeName = useCallback((fqdn) => {
        let name = String(fqdn || '').replace(/\.$/, '')
        if (name === zoneName) name = '@'
        else if (name.endsWith(`.${zoneName}`)) name = name.substring(0, name.length - zoneName.length - 1)
        return name
    }, [zoneName])

    const openAdd = useCallback(() => {
        setRecordForm({ seq: ++formSeqRef.current, mode: 'add', record: null })
    }, [])
    /** Bestehenden Record klonen -> Add-Dialog mit denselben Werten, aber ohne oldContent. */
    const openClone = useCallback((record) => {
        setRecordForm({ seq: ++formSeqRef.current, mode: 'clone', record })
    }, [])
    const openEdit = useCallback((record) => {
        setRecordForm({ seq: ++formSeqRef.current, mode: 'edit', record })
    }, [])
    const closeRecordForm = useCallback(() => setRecordForm(null), [])

    /**
     * Einen Wert loeschen (Papierkorb). extra: zusaetzliche Body-Felder (z. B. { manage_ptr }); name/type/content
     * gewinnen immer. Liefert die Antwort oder null (abgebrochen/Fehler).
     */
    const handleDelete = useCallback(async (record, extra = null) => {
        const { name, type, content } = record
        if (!window.confirm(t('zoneDetail.deleteRecordConfirm', { name, type }))) return null
        try {
            const res = await api.deleteRecord(server, zoneId, { ...(extra || {}), name, type, content })
            reportFanout(res?.details)
            setSuccess(t('zoneDetail.recordDeleted', { type, name: name.replace(/\.$/, '') }))
            loadZone()
            return res
        } catch (err) {
            setError(err.message)
            return null
        }
    }, [t, server, zoneId, reportFanout, loadZone])

    return {
        server, zoneId, zoneName, zoneKey,
        records, zoneMeta, loading, zoneLoadSeq,
        me, isAdmin, allServers, currentServerInfo, otherWritableServers,
        userCanEdit, serverCanWrite, canEdit,
        error, setError, success, setSuccess, warning, setWarning,
        loadZone, subscribeZoneLoaded,
        recordForm, openAdd, openEdit, openClone, closeRecordForm,
        handleDelete, reportFanout, resolveName, relativeName,
        slotState, setSlotState,
    }
}
