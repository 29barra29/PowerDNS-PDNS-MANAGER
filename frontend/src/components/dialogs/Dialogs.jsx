import { Component } from 'react'

/*
 * Dialog-Slot (Host in App.jsx, nur fuer angemeldete Bereiche; Plan B.14 [S8]).
 *
 * Vertrag fuer Slot-Dateien `components/dialogs/NN-<name>.dialog.jsx` (Reihenfolge = Dateiname):
 *   export const dialog = { id: 'stepup' }          // optional
 *   export default function StepUpDialog() { ... }   // ohne Props; immer gemountet
 * Ein Dialog ist dauerhaft gemountet, horcht selbst auf seine Ausloeser (z. B. Window-Event
 * `STEP_UP_EVENT` aus api.js) und rendert `null`, solange er geschlossen ist. Eigene Modals mit z-[60]
 * oder hoeher, damit sie ueber Seiten-Modals liegen. Ein Renderfehler blendet nur diesen Dialog aus.
 * Bekannter Nutzer: F10-APP-FE `10-stepup.dialog.jsx`.
 */
const modules = import.meta.glob('./*.dialog.jsx', { eager: true })

const DIALOGS = Object.keys(modules)
    .sort()
    .map((file) => {
        const mod = modules[file]
        const id = mod.dialog?.id || file.replace(/^\.\//, '').replace(/\.dialog\.jsx$/, '')
        return { id, Component: mod.default, file }
    })
    .filter((d) => {
        if (typeof d.Component === 'function' || (d.Component && typeof d.Component === 'object')) return true
        console.error(`Dialog ${d.file}: kein Default-Export`)
        return false
    })

class SlotBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { failed: false }
    }

    static getDerivedStateFromError() {
        return { failed: true }
    }

    componentDidCatch(error) {
        console.error(`Dialog ${this.props.id} ausgeblendet:`, error)
    }

    render() {
        return this.state.failed ? null : this.props.children
    }
}

export default function Dialogs() {
    if (DIALOGS.length === 0) return null
    return (
        <>
            {DIALOGS.map(({ id, Component: Dialog }) => (
                <SlotBoundary key={id} id={id}>
                    <Dialog />
                </SlotBoundary>
            ))}
        </>
    )
}
