import { Component } from 'react'

/*
 * Banner-Slot im Hauptbereich von Layout.jsx (direkt ueber der Seite, Plan B.14).
 *
 * Vertrag fuer Slot-Dateien `components/banners/NN-<name>.banner.jsx` (Reihenfolge = Dateiname):
 *   export const banner = { id: 'secrets', adminOnly: true }   // optional; adminOnly blendet fuer Nicht-Admins aus
 *   export default function SecretsBanner({ user, isAdmin }) { ... return null wenn nichts anzuzeigen ist }
 * Der Banner laedt seine Daten selbst, behandelt Fehler still (z. B. 403) und merkt sich ein "Schliessen"
 * selbst (sessionStorage in try/catch). Ein Renderfehler eines Banners blendet nur diesen Banner aus.
 * Bekannte Nutzer: F5 `10-secrets.banner.jsx`, F9 `20-dyndns-proxy.banner.jsx`.
 */
const modules = import.meta.glob('./*.banner.jsx', { eager: true })

const BANNERS = Object.keys(modules)
    .sort()
    .map((file) => {
        const mod = modules[file]
        const id = mod.banner?.id || file.replace(/^\.\//, '').replace(/\.banner\.jsx$/, '')
        return { id, adminOnly: !!mod.banner?.adminOnly, Component: mod.default, file }
    })
    .filter((b) => {
        if (typeof b.Component === 'function' || (b.Component && typeof b.Component === 'object')) return true
        console.error(`Banner ${b.file}: kein Default-Export`)
        return false
    })

// Faengt Renderfehler eines einzelnen Slots ab (Seite bleibt bedienbar).
class SlotBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { failed: false }
    }

    static getDerivedStateFromError() {
        return { failed: true }
    }

    componentDidCatch(error) {
        console.error(`Banner ${this.props.id} ausgeblendet:`, error)
    }

    render() {
        return this.state.failed ? null : this.props.children
    }
}

export default function Banners({ user }) {
    const isAdmin = user?.role === 'admin'
    const visible = BANNERS.filter((b) => !b.adminOnly || isAdmin)
    if (visible.length === 0) return null
    return (
        <>
            {visible.map(({ id, Component: Banner }) => (
                <SlotBoundary key={id} id={id}>
                    <Banner user={user} isAdmin={isAdmin} />
                </SlotBoundary>
            ))}
        </>
    )
}
