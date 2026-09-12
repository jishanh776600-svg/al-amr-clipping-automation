import { useState, useEffect } from 'react'
import { api, getStoredToken, setStoredToken } from '../api'

interface AuthModalProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
}

export function AuthModal({ isOpen, onClose, onSuccess }: AuthModalProps) {
  const [tokenInput, setTokenInput] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [testing, setTesting] = useState(false)
  const [statusMessage, setStatusMessage] = useState<{ text: string; isError: boolean } | null>(null)

  useEffect(() => {
    if (isOpen) {
      setTokenInput(getStoredToken() || '')
      setStatusMessage(null)
      setTesting(false)
    }
  }, [isOpen])

  if (!isOpen) return null

  const handleVerifyAndSave = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = tokenInput.trim()
    if (!trimmed) {
      setStatusMessage({ text: 'Please enter a valid token.', isError: true })
      return
    }

    setTesting(true)
    setStatusMessage(null)

    try {
      const isValid = await api.testAuth(trimmed)
      if (isValid) {
        setStoredToken(trimmed)
        setStatusMessage({ text: '✓ Connected and verified with AL AMR Control Plane!', isError: false })
        setTimeout(() => {
          onSuccess?.()
          onClose()
        }, 800)
      } else {
        setStatusMessage({
          text: 'Authentication rejected: HTTP 401 Unauthorized. Check your server secret.',
          isError: true,
        })
      }
    } catch (err: any) {
      setStatusMessage({
        text: `Connection failed: ${err.message || 'Server unreachable'}`,
        isError: true,
      })
    } finally {
      setTesting(false)
    }
  }

  const handleClear = () => {
    setStoredToken(null)
    setTokenInput('')
    setStatusMessage({ text: 'Token removed. You are now unauthenticated.', isError: false })
    onSuccess?.()
  }

  const hasExistingToken = Boolean(getStoredToken())

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-xl border border-ink-700 bg-ink-900 p-6 shadow-2xl shadow-black/80">
        <div className="flex items-center justify-between border-b border-ink-800 pb-4">
          <div>
            <p className="eyebrow text-sodium-500">Remote Security</p>
            <h2 className="font-display text-xl font-bold text-ink-100">
              Operator Authentication
            </h2>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-ink-400 hover:bg-ink-800 hover:text-ink-200"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <p className="mt-3 text-xs leading-relaxed text-ink-400">
          Private endpoints on this remote node require an operator credential (e.g.{' '}
          <code className="text-ink-200">OPERATOR_TOKEN</code> or{' '}
          <code className="text-ink-200">AL_AMR_MASTER_KEY</code>). Credentials are stored exclusively in your browser session.
        </p>

        <form onSubmit={handleVerifyAndSave} className="mt-5 space-y-4">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-ink-300">
              Operator Secret Token
            </label>
            <div className="mt-1.5 flex gap-2">
              <input
                type={showToken ? 'text' : 'password'}
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder="Enter secret token…"
                className="field flex-1 text-xs font-mono"
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShowToken(!showToken)}
                className="btn btn-quiet shrink-0 text-xs px-2.5"
                title={showToken ? 'Hide token' : 'Show token'}
              >
                {showToken ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          {statusMessage && (
            <div
              className={`rounded border p-3 text-xs ${
                statusMessage.isError
                  ? 'border-rose-500/40 bg-rose-500/10 text-rose-300'
                  : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
              }`}
            >
              {statusMessage.text}
            </div>
          )}

          <div className="flex items-center justify-between pt-2">
            {hasExistingToken ? (
              <button
                type="button"
                onClick={handleClear}
                className="text-xs text-ink-400 underline hover:text-rose-400"
              >
                Clear Token
              </button>
            ) : (
              <div />
            )}

            <div className="flex gap-2">
              <button
                type="button"
                onClick={onClose}
                className="btn btn-quiet text-xs"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={testing || !tokenInput.trim()}
                className="btn btn-primary text-xs"
              >
                {testing ? 'Verifying…' : 'Save & Connect'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
