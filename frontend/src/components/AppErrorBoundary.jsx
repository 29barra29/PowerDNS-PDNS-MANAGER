import { Component } from 'react'
import { AlertCircle } from 'lucide-react'
import i18n from '../i18n'

// Klassenkomponente (Error Boundary) -> kein useTranslation; Texte direkt ueber i18n.t (F8-A11).

export default class AppErrorBoundary extends Component {
    constructor(props) {
        super(props)
        this.state = { error: null }
    }

    static getDerivedStateFromError(error) {
        return { error }
    }

    componentDidCatch(error, info) {
        console.error('Frontend runtime error:', error, info)
    }

    render() {
        if (!this.state.error) return this.props.children

        return (
            <div className="min-h-screen bg-bg-primary text-text-primary flex items-center justify-center p-6">
                <div className="glass-card max-w-xl w-full p-6 space-y-4">
                    <div className="flex items-start gap-3">
                        <AlertCircle className="w-6 h-6 text-danger shrink-0 mt-0.5" />
                        <div>
                            <h1 className="text-xl font-bold">{i18n.t('errorBoundary.title')}</h1>
                            <p className="text-sm text-text-muted mt-2">{i18n.t('errorBoundary.body')}</p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => window.location.reload()}
                        className="px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium"
                    >
                        {i18n.t('common.reloadPage')}
                    </button>
                </div>
            </div>
        )
    }
}
