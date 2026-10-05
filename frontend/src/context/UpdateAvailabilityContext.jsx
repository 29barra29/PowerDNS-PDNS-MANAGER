import { useCallback, useEffect, useMemo, useState } from 'react'
import api from '../api'
import { compareSemver } from '../utils/semverCompare'
import { fetchLatestRemoteVersion } from '../lib/githubLatestVersion'
import { VERSION_CACHE_KEY, makeVersionCacheEntry, readVersionCache } from '../lib/settingsForms.js'
import { UpdateAvailabilityContext } from './updateAvailabilityContext'

const STORAGE_DISMISSED = 'dns_manager_dismissed_release_version'
const POLL_MS = 30 * 60 * 1000 // 30 Minuten (nutzt den 6-h-Cache, also hoechstens alle 6 h ein GitHub-Request)

// localStorage kann blockiert sein (Privatmodus, Richtlinien) - dann eben ohne Cache/Merker
function storageGet(key) {
    try { return localStorage.getItem(key) } catch { return null }
}
function storageSet(key, value) {
    try { localStorage.setItem(key, value) } catch { /* blockiert */ }
}

// Versionspruefung gegen GitHub (F8-B07, f153/f93):
// - nur fuer Admins; Nicht-Admins kontaktieren api.github.com gar nicht
// - Ergebnis 6 h im localStorage gecacht (recheck() umgeht den Cache)
// - GitHub nicht erreichbar -> checkError 'unreachable' (Updates-Tab zeigt "Versionspruefung fehlgeschlagen")
// Context-Wert: { updateAvailable, latestVersion, currentVersion, dismissUpdate, recheck, checkError, checked }
export function UpdateAvailabilityProvider({ children }) {
    const [currentVersion, setCurrentVersion] = useState(null)
    const [latestVersion, setLatestVersion] = useState(null)
    const [updateAvailable, setUpdateAvailable] = useState(false)
    const [checkError, setCheckError] = useState(null)
    const [checked, setChecked] = useState(false)

    const runCheck = useCallback(async ({ force = false } = {}) => {
        if (api.getUser()?.role !== 'admin') {
            setChecked(true)
            return
        }
        setCheckError(null)
        try {
            const app = await api.getAppInfo()
            const current = app?.app_version ? String(app.app_version).replace(/^v/i, '') : '0'
            setCurrentVersion(current)

            let remote = force ? null : readVersionCache(storageGet(VERSION_CACHE_KEY))
            if (!remote) {
                remote = await fetchLatestRemoteVersion()
                if (remote) storageSet(VERSION_CACHE_KEY, makeVersionCacheEntry(remote))
            }
            setLatestVersion(remote)

            if (!remote) {
                setCheckError('unreachable')
                setUpdateAvailable(false)
                return
            }

            const dismissed = storageGet(STORAGE_DISMISSED) || ''
            const isNewer = compareSemver(remote, current) > 0
            setUpdateAvailable(isNewer && dismissed !== remote)
        } catch (e) {
            setCheckError(e?.message || 'check failed')
            setUpdateAvailable(false)
        } finally {
            setChecked(true)
        }
    }, [])

    const recheck = useCallback(() => runCheck({ force: true }), [runCheck])

    const dismissUpdate = useCallback(() => {
        if (latestVersion) storageSet(STORAGE_DISMISSED, latestVersion)
        setUpdateAvailable(false)
    }, [latestVersion])

    useEffect(() => {
        const id = setInterval(() => { runCheck() }, POLL_MS)
        return () => clearInterval(id)
    }, [runCheck])

    /* Erster Check nach Mount (außerhalb synchroner setState-in-effect-Regel) */
    useEffect(() => {
        const tid = setTimeout(() => {
            runCheck()
        }, 0)
        return () => clearTimeout(tid)
    }, [runCheck])

    const value = useMemo(
        () => ({
            updateAvailable,
            latestVersion,
            currentVersion,
            dismissUpdate,
            recheck,
            checkError,
            checked,
        }),
        [updateAvailable, latestVersion, currentVersion, dismissUpdate, recheck, checkError, checked],
    )

    return <UpdateAvailabilityContext.Provider value={value}>{children}</UpdateAvailabilityContext.Provider>
}
