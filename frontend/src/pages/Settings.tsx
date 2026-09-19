import { useEffect, useState } from 'react'

import {
  api,
  ApiError,
  getStoredToken,
  setStoredToken,
  getStoredPat,
  type ProviderStatus,
  type Settings as SettingsData,
  type SystemStatus,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'

const SECRET_LABELS: Record<string, string> = {
  anthropic: 'Anthropic API key',
  openai: 'OpenAI-compatible API key',
  gemini: 'Google Gemini API key',
  huggingface_token: 'HuggingFace token',
  github_pat: 'GitHub Personal Access Token (Worker Dispatch)',
}

export function Settings() {
  const [settings, setSettings] = useState<SettingsData | null>(null)
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)
  const [apiKeyInput, setApiKeyInput] = useState(() => getStoredToken() || '')
  const [tokenStatus, setTokenStatus] = useState<string | null>(null)
  const [verifying, setVerifying] = useState(false)

  const reload = async () => {
    setLoading(true)
    setError(null)
    try {
      const [sData, pData, sysData] = await Promise.all([
        api.getSettings(),
        api.providerStatus().catch(() => []),
        api.system().catch(() => null),
      ])
      if (sData?.credentials_status?.github_pat && !sData.credentials_status.github_pat.configured) {
        const storedPat = getStoredPat()
        if (storedPat) {
          try {
            await api.putSecret('github_pat', storedPat)
            sData.credentials_status.github_pat.configured = true
            sData.credentials_status.github_pat.masked = '•••••••• Configured (Auto-Restored)'
            if (sData.keys_present) {
              sData.keys_present.github_pat = true
            }
          } catch {}
        }
      }
      setSettings(sData)
      setProviders(pData)
      setSystem(sysData)
    } catch (err: any) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }

  const handleSaveToken = async () => {
    const trimmed = apiKeyInput.trim()
    setVerifying(true)
    setTokenStatus(null)
    try {
      if (trimmed) {
        const isValid = await api.testAuth(trimmed)
        if (isValid) {
          setStoredToken(trimmed)
          setTokenStatus('✓ Token verified and connected!')
          await reload()
        } else {
          setTokenStatus('Authentication rejected: HTTP 401 Unauthorized. Check token.')
        }
      } else {
        setStoredToken(null)
        setTokenStatus('Token cleared.')
        await reload()
      }
    } catch (err: any) {
      setTokenStatus(`Verification failed: ${err.message || 'Server error'}`)
    } finally {
      setVerifying(false)
    }
  }

  const [tgTesting, setTgTesting] = useState(false)
  const [tgResult, setTgResult] = useState<{ valid: boolean; account_name?: string | null; details?: string; error?: string | null } | null>(null)

  const [ytConnecting, setYtConnecting] = useState(false)
  const [ytTesting, setYtTesting] = useState(false)
  const [ytResult, setYtResult] = useState<{ valid: boolean; account_name?: string | null; details?: string; error?: string | null } | null>(null)
  const [ytNotice, setYtNotice] = useState<string | null>(null)

  const [igTesting, setIgTesting] = useState(false)
  const [igResult, setIgResult] = useState<{ valid: boolean; account_name?: string | null; details?: string; error?: string | null } | null>(null)

  const handleTestTelegram = async () => {
    setTgTesting(true)
    setTgResult(null)
    try {
      const res = await api.validateTelegram()
      setTgResult(res)
    } catch (err: any) {
      setTgResult({ valid: false, error: err.message || 'Validation request failed' })
    } finally {
      setTgTesting(false)
    }
  }

  const handleConnectYouTube = async () => {
    setYtConnecting(true)
    setYtNotice(null)
    try {
      const redirectUri = window.location.origin + window.location.pathname
      const { auth_url } = await api.getYouTubeAuthUrl(redirectUri)
      window.location.href = auth_url
    } catch (err: any) {
      setError(new Error(`Failed to generate YouTube authorization URL: ${err.message}`))
      setYtConnecting(false)
    }
  }

  const handleTestYouTube = async () => {
    setYtTesting(true)
    setYtResult(null)
    try {
      const res = await api.validateYouTube()
      setYtResult(res)
    } catch (err: any) {
      setYtResult({ valid: false, error: err.message || 'Validation request failed' })
    } finally {
      setYtTesting(false)
    }
  }

  const handleDisconnectYouTube = async () => {
    try {
      await api.disconnectYouTube()
      setYtNotice('YouTube account disconnected.')
      setYtResult(null)
      await reload()
    } catch (err: any) {
      setError(new Error(`Failed to disconnect YouTube: ${err.message}`))
    }
  }

  const handleTestInstagram = async () => {
    setIgTesting(true)
    setIgResult(null)
    try {
      const res = await api.validateInstagram()
      setIgResult(res)
    } catch (err: any) {
      setIgResult({ valid: false, error: err.message || 'Validation request failed' })
    } finally {
      setIgTesting(false)
    }
  }

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search)
      const code = params.get('code')
      if (code) {
        window.history.replaceState({}, document.title, window.location.pathname)
        const redirectUri = window.location.origin + window.location.pathname
        setYtNotice('Exchanging authorization code with Google OAuth...')
        api
          .exchangeYouTubeCode(code, redirectUri)
          .then((res) => {
            setYtNotice(`✓ Connected YouTube Channel: ${res.account_name || 'Account Linked'}`)
            reload()
          })
          .catch((err) => {
            setError(new Error(`YouTube authorization failed: ${err.message}`))
          })
      }
    }
    reload()
  }, [])

  const patch = async (update: Partial<SettingsData>) => {
    setError(null)
    try {
      setSettings(await api.putSettings(update))
      setSaved(true)
      setTimeout(() => setSaved(false), 1600)
    } catch (err) {
      setError(err as Error)
    }
  }

  if (loading && !settings && !error) {
    return (
      <div className="max-w-4xl pt-14">
        <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-100">
          Settings
        </h1>
        <p className="mt-6 text-sm text-ink-500">Loading settings from remote node…</p>
      </div>
    )
  }

  if (!settings && error) {
    const is401 = (error as ApiError).status === 401
    return (
      <div className="max-w-4xl pt-14">
        <div className="rise flex items-baseline justify-between border-b border-ink-800 pb-5">
          <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-100">
            Settings
          </h1>
        </div>

        <div className="mt-8 rounded-xl border border-sodium-500/40 bg-ink-850 p-6 shadow-xl shadow-black/50">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🔒</span>
            <div>
              <h2 className="font-display text-lg font-bold text-ink-100">
                {is401 ? 'Operator Authentication Required' : 'Failed to Load Settings'}
              </h2>
              <p className="mt-1 text-xs text-ink-400">
                {is401
                  ? 'Private endpoints on this remote node require an operator credential (OPERATOR_TOKEN or AL_AMR_MASTER_KEY).'
                  : error.message}
              </p>
            </div>
          </div>

          <div className="mt-6 border-t border-ink-800 pt-5">
            <label className="eyebrow">Enter Operator API Key</label>
            <div className="mt-2 flex gap-3">
              <input
                type="password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder="Paste your remote OPERATOR_TOKEN…"
                className="field text-xs font-mono"
              />
              <button
                type="button"
                disabled={verifying || !apiKeyInput.trim()}
                onClick={handleSaveToken}
                className="btn btn-primary shrink-0 text-xs"
              >
                {verifying ? 'Verifying…' : 'Save & Connect'}
              </button>
            </div>
            {tokenStatus && (
              <p className="mt-2.5 text-xs text-sodium-400 font-medium">{tokenStatus}</p>
            )}
            <p className="mt-2 text-[11px] text-ink-500">
              Stored exclusively in browser <code className="text-ink-400">localStorage</code> and transmitted as <code className="text-ink-400">Authorization: Bearer &lt;token&gt;</code>.
            </p>
          </div>

          {!is401 && (
            <div className="mt-4 pt-4 border-t border-ink-800">
              <button onClick={reload} className="btn btn-quiet text-xs">
                ↻ Retry Loading Settings
              </button>
            </div>
          )}
        </div>
      </div>
    )
  }

  if (!settings) {
    return (
      <div className="max-w-4xl pt-14">
        <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-100">
          Settings
        </h1>
        <p className="mt-6 text-sm text-ink-500">No settings available.</p>
        <button onClick={reload} className="mt-4 btn btn-quiet text-xs">↻ Retry</button>
      </div>
    )
  }

  return (
    <div className="max-w-4xl pt-14">
      <div className="rise flex items-baseline justify-between border-b border-ink-800 pb-5">
        <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-100">
          Settings
        </h1>
        <span
          className="text-xs text-signal-good transition-opacity duration-300"
          style={{ opacity: saved ? 1 : 0 }}
        >
          saved
        </span>
      </div>

      {error && (
        <div className="mt-6">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {settings.insecure_secret_storage && (
        <p className="mt-6 border-l-2 border-sodium-600 pl-4 text-sm leading-relaxed text-ink-300">
          No OS keyring is available on this machine, so API keys are stored in plain text
          in <code className="text-ink-200">config.json</code>. On headless Linux, installing
          a Secret Service provider or <code className="text-ink-200">keyrings.alt</code>{' '}
          fixes this.
        </p>
      )}

      <Section
        title="Operator Access & Authentication"
        note="Token used to authenticate this browser client with the remote AutoClip server."
      >
        <div className="rounded border border-ink-800 bg-ink-850/60 p-4">
          <label className="eyebrow">Operator API Key (AUTOCLIP_API_KEY)</label>
          <div className="mt-2 flex gap-3">
            <input
              type="password"
              value={apiKeyInput}
              onChange={(e) => setApiKeyInput(e.target.value)}
              placeholder="Paste your remote AUTOCLIP_API_KEY…"
              className="field text-xs font-mono"
            />
            <button
              type="button"
              onClick={handleSaveToken}
              className="btn btn-primary shrink-0 text-xs"
            >
              Save & Verify
            </button>
            {apiKeyInput && (
              <button
                type="button"
                onClick={() => {
                  setApiKeyInput('')
                  setStoredToken(null)
                  setTokenStatus('Token cleared')
                }}
                className="btn btn-quiet shrink-0 text-xs"
              >
                Clear
              </button>
            )}
          </div>
          {tokenStatus && (
            <p className="mt-2 text-xs text-sodium-400">
              {tokenStatus}
            </p>
          )}
          <p className="mt-2 text-[11px] text-ink-500">
            Stored in browser <code className="text-ink-400">localStorage</code>. Transmitted securely as <code className="text-ink-400">Authorization: Bearer &lt;token&gt;</code> on all requests.
          </p>
        </div>
      </Section>

      <Section title="AI provider" note="Which model picks the clips.">
        <div className="space-y-1">
          {providers.map((provider) => (
            <button
              key={provider.name}
              onClick={() => patch({ active_provider: provider.name })}
              className={[
                'block w-full border-l-2 py-3 pl-3 text-left transition-colors duration-200',
                provider.name === settings.active_provider
                  ? 'border-sodium-500 bg-ink-850/60'
                  : 'border-transparent hover:border-ink-700 hover:bg-ink-850/30',
              ].join(' ')}
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm text-ink-100">{provider.name}</span>
                <span
                  className={`text-xs ${provider.available ? 'text-signal-good' : 'text-ink-500'}`}
                >
                  {provider.available ? 'reachable' : provider.detail || 'unavailable'}
                </span>
              </div>
              {provider.models.length > 0 && (
                <span className="numeric mt-1 block truncate text-xs text-ink-600">
                  {provider.models.slice(0, 6).join(' · ')}
                </span>
              )}
            </button>
          ))}
        </div>

        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          <Field
            label="Model"
            value={settings.providers[settings.active_provider]?.model ?? ''}
            placeholder="model name"
            onCommit={(value) =>
              patch({
                providers: {
                  [settings.active_provider]: {
                    ...settings.providers[settings.active_provider],
                    model: value,
                  },
                },
              })
            }
          />
          <Field
            label="Base URL"
            hint="Point at OpenRouter, Groq, DeepSeek, or a local server."
            value={settings.providers[settings.active_provider]?.base_url ?? ''}
            placeholder="https://…"
            onCommit={(value) =>
              patch({
                providers: {
                  [settings.active_provider]: {
                    ...settings.providers[settings.active_provider],
                    base_url: value || null,
                  },
                },
              })
            }
          />
        </div>
      </Section>

      <Section title="Keys" note="Stored securely in your encrypted database vault & OS keyring. Never sent anywhere but the provider.">
        <div className="space-y-4">
          {Object.entries(SECRET_LABELS).map(([key, label]) => (
            <SecretField
              key={key}
              secretKey={key}
              label={label}
              present={settings.keys_present[key] ?? false}
              status={settings.credentials_status?.[key]}
              onChanged={reload}
              onError={setError}
            />
          ))}
        </div>
      </Section>

      <Section
        title="Publishing Channels & Social Integrations"
        note="Configure human review on Telegram, and automated Shorts/Reels distribution to YouTube and Instagram."
      >
        <div className="space-y-6">
          {/* Telegram Bot */}
          <div className="rounded-lg border border-ink-800 bg-ink-850/60 p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-sm text-ink-100 font-semibold">1. Telegram Review & Publishing Bot</h3>
                <p className="text-xs text-ink-500 mt-0.5">Sends rendered clips for review with interactive Approve / Request Changes buttons.</p>
              </div>
              <button
                type="button"
                onClick={handleTestTelegram}
                disabled={tgTesting}
                className="btn btn-quiet text-xs shrink-0"
              >
                {tgTesting ? 'Testing...' : 'Test Bot Connection'}
              </button>
            </div>

            {tgResult && (
              <div className={`rounded p-2.5 text-xs ${tgResult.valid ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400' : 'bg-rose-500/10 border border-rose-500/30 text-rose-400'}`}>
                {tgResult.valid ? `✓ Connected to ${tgResult.account_name} (${tgResult.details})` : `✗ ${tgResult.error || 'Connection failed'}`}
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2">
              <SecretField
                secretKey="telegram_bot_token"
                label="Telegram Bot Token"
                present={settings.keys_present?.telegram_bot_token ?? false}
                status={settings.credentials_status?.telegram_bot_token}
                onChanged={reload}
                onError={setError}
              />
              <SecretField
                secretKey="telegram_chat_id"
                label="Telegram Chat / Channel ID"
                present={settings.keys_present?.telegram_chat_id ?? false}
                status={settings.credentials_status?.telegram_chat_id}
                onChanged={reload}
                onError={setError}
              />
            </div>
          </div>

          {/* YouTube Shorts */}
          <div className="rounded-lg border border-ink-800 bg-ink-850/60 p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-sm text-ink-100 font-semibold">2. YouTube Shorts (OAuth2 Integration)</h3>
                <p className="text-xs text-ink-500 mt-0.5">Automatic upload and publishing of approved 9:16 vertical Shorts.</p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleConnectYouTube}
                  disabled={ytConnecting || !settings.keys_present?.youtube_client_id}
                  className="btn btn-primary text-xs shrink-0"
                >
                  {ytConnecting ? 'Opening OAuth...' : settings.keys_present?.youtube_refresh_token ? 'Reconnect YouTube' : 'Connect YouTube Account'}
                </button>
                {settings.keys_present?.youtube_refresh_token && (
                  <>
                    <button
                      type="button"
                      onClick={handleTestYouTube}
                      disabled={ytTesting}
                      className="btn btn-quiet text-xs shrink-0"
                    >
                      {ytTesting ? 'Testing...' : 'Test Connection'}
                    </button>
                    <button
                      type="button"
                      onClick={handleDisconnectYouTube}
                      className="btn btn-quiet text-xs text-rose-400 hover:text-rose-300 shrink-0"
                    >
                      Disconnect
                    </button>
                  </>
                )}
              </div>
            </div>

            {ytNotice && (
              <div className="rounded p-2.5 text-xs bg-emerald-500/10 border border-emerald-500/30 text-emerald-400">
                {ytNotice}
              </div>
            )}

            {ytResult && (
              <div className={`rounded p-2.5 text-xs ${ytResult.valid ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400' : 'bg-rose-500/10 border border-rose-500/30 text-rose-400'}`}>
                {ytResult.valid ? `✓ Connected Channel: ${ytResult.account_name}` : `✗ ${ytResult.error || 'Connection failed'}`}
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2">
              <SecretField
                secretKey="youtube_client_id"
                label="OAuth Client ID"
                present={settings.keys_present?.youtube_client_id ?? false}
                status={settings.credentials_status?.youtube_client_id}
                onChanged={reload}
                onError={setError}
              />
              <SecretField
                secretKey="youtube_client_secret"
                label="OAuth Client Secret"
                present={settings.keys_present?.youtube_client_secret ?? false}
                status={settings.credentials_status?.youtube_client_secret}
                onChanged={reload}
                onError={setError}
              />
            </div>
            {settings.keys_present?.youtube_refresh_token && (
              <div className="rounded border border-ink-800/80 bg-ink-900/60 p-3 flex items-center justify-between text-xs">
                <span className="text-ink-400">OAuth Refresh Token:</span>
                <span className="text-emerald-400 font-mono">
                  {settings.credentials_status?.youtube_refresh_token?.masked || '•••••••• Configured'}
                </span>
              </div>
            )}
          </div>

          {/* Instagram Reels */}
          <div className="rounded-lg border border-ink-800 bg-ink-850/60 p-5 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display text-sm text-ink-100 font-semibold">3. Instagram Reels (Meta Graph API)</h3>
                <p className="text-xs text-ink-500 mt-0.5">Automated upload, container processing, and publishing of Reels.</p>
              </div>
              <button
                type="button"
                onClick={handleTestInstagram}
                disabled={igTesting}
                className="btn btn-quiet text-xs shrink-0"
              >
                {igTesting ? 'Testing...' : 'Test Connection'}
              </button>
            </div>

            {igResult && (
              <div className={`rounded p-2.5 text-xs ${igResult.valid ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400' : 'bg-rose-500/10 border border-rose-500/30 text-rose-400'}`}>
                {igResult.valid ? `✓ Connected to ${igResult.account_name}` : `✗ ${igResult.error || 'Connection failed'}`}
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2">
              <SecretField
                secretKey="instagram_access_token"
                label="Meta Graph API Access Token"
                present={settings.keys_present?.instagram_access_token ?? false}
                status={settings.credentials_status?.instagram_access_token}
                onChanged={reload}
                onError={setError}
              />
              <SecretField
                secretKey="instagram_account_id"
                label="Instagram Professional Account ID"
                present={settings.keys_present?.instagram_account_id ?? false}
                status={settings.credentials_status?.instagram_account_id}
                onChanged={reload}
                onError={setError}
              />
            </div>
          </div>
        </div>
      </Section>

      <Section title="Transcription">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Whisper model"
            value={settings.whisper.model}
            onChange={(value) => patch({ whisper: { ...settings.whisper, model: value } })}
            options={['tiny', 'base', 'small', 'medium', 'large-v3']}
          />
          <Field
            label="Language"
            hint="Leave empty to detect automatically."
            value={settings.whisper.language}
            placeholder="auto"
            onCommit={(value) => patch({ whisper: { ...settings.whisper, language: value } })}
          />
        </div>

        <label className="mt-5 flex items-start gap-3 text-sm text-ink-200">
          <input
            type="checkbox"
            checked={settings.whisper.diarization}
            disabled={!system?.diarization_available}
            onChange={(e) =>
              patch({ whisper: { ...settings.whisper, diarization: e.target.checked } })
            }
            className="mt-0.5 size-4 accent-sodium-500"
          />
          <span>
            Identify speakers
            {!system?.diarization_available && (
              <span className="mt-1 block text-xs text-ink-500">
                Needs the diarization extra:{' '}
                <code className="text-ink-300">uv pip install &apos;autoclip[diarization]&apos;</code>
              </span>
            )}
          </span>
        </label>
      </Section>

      <Section title="Clips">
        <div className="grid gap-5 sm:grid-cols-3">
          <NumberField
            label="Min length (s)"
            value={settings.clips.min_duration_s}
            onCommit={(value) => patch({ clips: { ...settings.clips, min_duration_s: value } })}
          />
          <NumberField
            label="Max length (s)"
            value={settings.clips.max_duration_s}
            onCommit={(value) => patch({ clips: { ...settings.clips, max_duration_s: value } })}
          />
          <NumberField
            label="Max clips"
            value={settings.clips.max_clips}
            onCommit={(value) => patch({ clips: { ...settings.clips, max_clips: value } })}
          />
        </div>
      </Section>

      <Section title="Ingest" note="Media download engines, egress routing, and proxy sidecars.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Field
            label="Egress Proxy URL"
            hint="SOCKS5 or HTTP proxy URL for WARP egress (e.g. socks5://127.0.0.1:1080)."
            value={settings.ingest.proxy ?? ''}
            placeholder="socks5://127.0.0.1:1080"
            onCommit={(value) =>
              patch({ ingest: { ...settings.ingest, proxy: value } })
            }
          />
          <Select
            label="YouTube cookies from (Local only)"
            hint="Cloud workers run browserless and use WARP egress. For local desktop use only."
            value={settings.ingest.cookies_from_browser}
            onChange={(value) =>
              patch({ ingest: { ...settings.ingest, cookies_from_browser: value } })
            }
            options={['', 'chrome', 'firefox', 'edge', 'brave', 'chromium', 'safari']}
            labels={{ '': 'None' }}
          />
        </div>
      </Section>

      <Section title="Export">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Default ratio"
            value={settings.export.ratio}
            onChange={(value) => patch({ export: { ...settings.export, ratio: value } })}
            options={['9:16', '1:1', '16:9']}
          />
          <NumberField
            label="Loudness target (LUFS)"
            value={settings.export.loudness_lufs}
            onCommit={(value) => patch({ export: { ...settings.export, loudness_lufs: value } })}
          />
        </div>

        <label className="mt-5 flex items-start gap-3 text-sm text-ink-200">
          <input
            type="checkbox"
            checked={settings.export.prefer_hardware_encoder}
            onChange={(e) =>
              patch({
                export: { ...settings.export, prefer_hardware_encoder: e.target.checked },
              })
            }
            className="mt-0.5 size-4 accent-sodium-500"
          />
          <span>
            Use GPU encoding when available
            {system && !system.nvenc_works && (
              <span className="mt-1 block text-xs text-ink-500">
                Not usable on this machine — exports will use the CPU encoder. Same quality,
                slower.
              </span>
            )}
          </span>
        </label>

        <label className="mt-4 flex items-center gap-3 text-sm text-ink-200">
          <input
            type="checkbox"
            checked={settings.export.write_srt}
            onChange={(e) => patch({ export: { ...settings.export, write_srt: e.target.checked } })}
            className="size-4 accent-sodium-500"
          />
          Also write an .srt sidecar
        </label>
      </Section>

      {system && (
        <Section title="This machine">
          <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
            <Row label="Platform" value={system.platform} />
            <Row label="Python" value={system.python_version} />
            <Row label="ffmpeg" value={system.ffmpeg_version ?? 'not found'} />
            <Row label="Acceleration" value={system.accel.toUpperCase()} />
            <Row label="Device" value={system.gpu_name ?? '—'} />
            <Row label="Whisper compute" value={system.compute_type} />
            <Row label="GPU encode" value={system.nvenc_works ? 'available' : 'unavailable'} />
            <Row label="Captions" value={system.has_libass ? 'libass present' : 'libass missing'} />
          </dl>
        </Section>
      )}
    </div>
  )
}

function Section({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section className="rise mt-14">
      <div className="border-b border-ink-800 pb-2">
        <h2 className="eyebrow">{title}</h2>
        {note && <p className="mt-1 text-xs text-ink-500">{note}</p>}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-ink-850 pb-2">
      <dt className="text-ink-500">{label}</dt>
      <dd className="numeric truncate text-right text-ink-200">{value}</dd>
    </div>
  )
}

/** Commits on blur rather than per keystroke, so a PUT isn't fired per letter. */
function Field({
  label,
  hint,
  value,
  placeholder,
  onCommit,
}: {
  label: string
  hint?: string
  value: string
  placeholder?: string
  onCommit: (value: string) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])

  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <input
        className="field mt-1 text-sm"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        spellCheck={false}
      />
      {hint && <span className="mt-1.5 block text-xs leading-snug text-ink-500">{hint}</span>}
    </label>
  )
}

function NumberField({
  label,
  value,
  onCommit,
}: {
  label: string
  value: number
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <input
        type="number"
        className="field numeric mt-1 text-sm"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const parsed = Number(draft)
          if (!Number.isNaN(parsed) && parsed !== value) onCommit(parsed)
        }}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />
    </label>
  )
}

function Select({
  label,
  hint,
  value,
  onChange,
  options,
  labels = {},
}: {
  label: string
  hint?: string
  value: string
  onChange: (value: string) => void
  options: string[]
  labels?: Record<string, string>
}) {
  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <select
        className="field mt-1 cursor-pointer text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option} className="bg-ink-850">
            {labels[option] ?? option}
          </option>
        ))}
      </select>
      {hint && <span className="mt-1.5 block text-xs leading-snug text-ink-500">{hint}</span>}
    </label>
  )
}

function SecretField({
  secretKey,
  label,
  present,
  status,
  onChanged,
  onError,
}: {
  secretKey: string
  label: string
  present: boolean
  status?: { configured: boolean; masked?: string; updated_at?: string }
  onChanged: () => Promise<void> | void
  onError: (error: Error) => void
}) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [validating, setValidating] = useState(false)
  const [testResult, setTestResult] = useState<{ valid: boolean; message: string } | null>(null)
  const [savedNotice, setSavedNotice] = useState<string | null>(null)
  const [showReplaceInput, setShowReplaceInput] = useState(false)

  const isConfigured = present || (status?.configured ?? false)

  const save = async () => {
    const trimmed = value.trim()
    if (!trimmed) return
    if (trimmed.includes('••••') || trimmed.includes('Configured')) {
      setValue('')
      return
    }
    setBusy(true)
    setTestResult(null)
    setSavedNotice(null)
    try {
      await api.putSecret(secretKey, trimmed)
      setValue('')
      setShowReplaceInput(false)
      setSavedNotice('✓ Token successfully encrypted and saved to database vault.')
      setTimeout(() => setSavedNotice(null), 4000)
      await onChanged()
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    setTestResult(null)
    setSavedNotice(null)
    try {
      await api.deleteSecret(secretKey)
      setValue('')
      setShowReplaceInput(false)
      setSavedNotice('Token cleared from durable vault.')
      setTimeout(() => setSavedNotice(null), 3000)
      await onChanged()
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const handleValidate = async () => {
    setValidating(true)
    setTestResult(null)
    try {
      const res = await api.validateSecret(secretKey, value.trim() || undefined)
      setTestResult(res)
    } catch (err: any) {
      setTestResult({
        valid: false,
        message: err.message || 'Validation request failed',
      })
    } finally {
      setValidating(false)
    }
  }

  return (
    <div className="rounded-lg border border-ink-800 bg-ink-900/40 p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="eyebrow text-ink-200">{label}</span>
          {isConfigured ? (
            <span className="inline-flex items-center gap-1.5 rounded bg-signal-good/15 px-2.5 py-0.5 text-xs font-mono font-medium text-signal-good">
              <span className="w-1.5 h-1.5 rounded-full bg-signal-good" />
              Connected & Stored
            </span>
          ) : (
            <span className="rounded bg-ink-800 px-2 py-0.5 text-xs font-mono text-ink-400">
              Not configured
            </span>
          )}
        </div>
        {status?.updated_at && (
          <span className="text-[11px] text-ink-500">
            Vault updated: {new Date(status.updated_at).toLocaleDateString()}
          </span>
        )}
      </div>

      {isConfigured && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-ink-800 bg-ink-850/70 px-3.5 py-2.5 text-xs">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-ink-400 font-mono text-[11px]">Encrypted Secret:</span>
            <span className="font-mono text-ink-100 font-medium truncate">
              {status?.masked || '•••••••• Configured'}
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setShowReplaceInput(!showReplaceInput)}
              className="btn btn-quiet text-xs py-1 px-2.5"
            >
              {showReplaceInput ? 'Cancel' : 'Replace'}
            </button>
            <button
              type="button"
              onClick={handleValidate}
              disabled={validating || busy}
              className="btn btn-quiet text-xs py-1 px-2.5"
              title="Test token authentication against provider"
            >
              {validating ? 'Testing…' : 'Test / Validate'}
            </button>
            <button
              type="button"
              onClick={remove}
              disabled={busy || validating}
              className="btn btn-quiet text-xs py-1 px-2.5 text-signal-danger hover:bg-signal-danger/10"
              title="Remove stored token from AutoClip"
            >
              Remove
            </button>
          </div>
        </div>
      )}

      {(!isConfigured || showReplaceInput) && (
        <div className="flex flex-wrap items-center gap-2.5 pt-1">
          <input
            type="password"
            className="field text-sm flex-1 min-w-[240px]"
            value={value}
            placeholder={
              isConfigured
                ? 'Paste new token to replace existing secret…'
                : 'Paste token to configure…'
            }
            onChange={(e) => {
              setValue(e.target.value)
              if (testResult) setTestResult(null)
            }}
            onKeyDown={(e) => e.key === 'Enter' && save()}
            autoComplete="off"
          />
          <button
            type="button"
            onClick={save}
            disabled={!value.trim() || busy}
            className="btn btn-primary text-xs py-1.5 px-3"
          >
            {isConfigured ? 'Save New Token' : 'Save & Encrypt'}
          </button>
          {!isConfigured && value.trim() && (
            <button
              type="button"
              onClick={handleValidate}
              disabled={validating || busy}
              className="btn btn-quiet text-xs py-1.5 px-3"
            >
              {validating ? 'Testing…' : 'Test / Validate'}
            </button>
          )}
        </div>
      )}

      {savedNotice && (
        <div className="text-xs px-3 py-1.5 rounded bg-signal-good/10 text-signal-good border border-signal-good/20 flex items-center gap-2">
          <span>✓</span>
          <span>{savedNotice}</span>
        </div>
      )}

      {testResult && (
        <div
          className={`text-xs px-3 py-1.5 rounded flex items-center gap-2 ${
            testResult.valid
              ? 'bg-signal-good/10 text-signal-good border border-signal-good/20'
              : 'bg-signal-danger/10 text-signal-danger border border-signal-danger/20'
          }`}
        >
          <span>{testResult.valid ? '✓' : '✕'}</span>
          <span>{testResult.message}</span>
        </div>
      )}
    </div>
  )
}
