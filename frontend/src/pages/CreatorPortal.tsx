import React, { useEffect, useRef, useState } from 'react'
import { api, type CreatorProfile } from '../api'

export function CreatorPortal() {
  const [passcode, setPasscode] = useState(() => localStorage.getItem('alamr_creator_passcode') || '')
  const [profile, setProfile] = useState<CreatorProfile | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

  // YouTube Device & Direct Token State
  const [showYtDeviceModal, setShowYtDeviceModal] = useState(false)
  const [ytTab, setYtTab] = useState<'device' | 'direct'>('device')
  const [ytUserCode, setYtUserCode] = useState('')
  const [ytVerificationUrl, setYtVerificationUrl] = useState('https://www.google.com/device')
  const [ytCopied, setYtCopied] = useState(false)
  const [directRefreshToken, setDirectRefreshToken] = useState('')
  const [savingDirectToken, setSavingDirectToken] = useState(false)
  const ytPollTimerRef = useRef<any>(null)

  // Modals / forms
  const [showTelegramModal, setShowTelegramModal] = useState(false)
  const [tgBotToken, setTgBotToken] = useState('')
  const [tgChatId, setTgChatId] = useState('')

  const [showInstaModal, setShowInstaModal] = useState(false)
  const [igToken, setIgToken] = useState('')
  const [igAccountId, setIgAccountId] = useState('')

  // Quick Ingest Form
  const [videoUrl, setVideoUrl] = useState('')
  const [briefText, setBriefText] = useState('')
  const [isSubmittingJob, setIsSubmittingJob] = useState(false)

  // Real-time Pipeline & Clips
  const [creatorJobsList, setCreatorJobsList] = useState<any[]>([])
  const [previewExportUrl, setPreviewExportUrl] = useState<string | null>(null)
  const [previewClipTitle, setPreviewClipTitle] = useState<string>('')

  const loadJobs = async (code: string) => {
    if (!code) return
    try {
      const list = await api.creatorJobs(code)
      setCreatorJobsList(list)
    } catch {
      // Quiet fail on background jobs refresh
    }
  }

  // Fetch profile if passcode is stored
  const loadProfile = async (code: string) => {
    if (!code) return
    setLoading(true)
    setError(null)
    try {
      const p = await api.creatorProfile(code)
      setProfile(p)
      localStorage.setItem('alamr_creator_passcode', code)
      loadJobs(code)
    } catch (err: any) {
      setError(err?.message || 'Invalid or expired passcode.')
      setProfile(null)
    } finally {
      setLoading(false)
    }
  }

  const stopPollingYouTube = () => {
    if (ytPollTimerRef.current) {
      clearInterval(ytPollTimerRef.current)
      ytPollTimerRef.current = null
    }
  }

  const startPollingYouTube = (deviceCode: string) => {
    stopPollingYouTube()
    ytPollTimerRef.current = setInterval(async () => {
      try {
        const currentPasscode = localStorage.getItem('alamr_creator_passcode') || passcode
        const res = await api.creatorYouTubeDevicePoll(deviceCode, currentPasscode)
        if (res?.status === 'authorized') {
          stopPollingYouTube()
          setShowYtDeviceModal(false)
          setSuccessMsg(res.message || `Connected channel: ${res.channel_title}!`)
          loadProfile(currentPasscode)
        } else if (res?.status === 'failed') {
          stopPollingYouTube()
          setError(res.error || 'YouTube authorization failed or expired.')
          setShowYtDeviceModal(false)
        }
      } catch {
        // Continue polling until expiration
      }
    }, 5000)
  }

  useEffect(() => {
    if (passcode) {
      loadProfile(passcode)
    }

    // Check for Google OAuth callback parameters in URL (?code=...&state=...)
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    if (code) {
      handleOAuthCallback(code)
    }

    return () => {
      stopPollingYouTube()
    }
  }, [])

  useEffect(() => {
    if (!profile || !passcode) return

    const interval = setInterval(() => {
      loadJobs(passcode)
    }, 4000)

    return () => clearInterval(interval)
  }, [profile?.id, passcode])

  const handleOAuthCallback = async (authCode: string) => {
    const currentPasscode = localStorage.getItem('alamr_creator_passcode') || passcode
    if (!currentPasscode) {
      setError('Please enter your Creator Passcode before linking YouTube.')
      return
    }
    setLoading(true)
    try {
      const redirectUri = window.location.origin + window.location.pathname
      const res = await api.creatorYouTubeCallback(authCode, redirectUri, currentPasscode)
      setSuccessMsg(res.message || 'YouTube Channel Connected Successfully!')
      // Clean query params from URL
      window.history.replaceState({}, document.title, window.location.pathname)
      loadProfile(currentPasscode)
    } catch (err: any) {
      setError(err?.message || 'Failed to exchange YouTube authorization code.')
    } finally {
      setLoading(false)
    }
  }

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault()
    if (!passcode.trim()) return
    loadProfile(passcode.trim())
  }

  const handleConnectYouTube = async () => {
    setShowYtDeviceModal(true)
    if (!ytUserCode) {
      try {
        setError(null)
        setLoading(true)
        const res = await api.creatorYouTubeDeviceCode(passcode)
        if (res?.user_code && res?.device_code) {
          setYtUserCode(res.user_code)
          setYtVerificationUrl(res.verification_url || 'https://www.google.com/device')
          startPollingYouTube(res.device_code)
        } else {
          setError('Could not generate YouTube device code.')
        }
      } catch (err: any) {
        setError(err?.message || 'Failed to start YouTube authentication.')
      } finally {
        setLoading(false)
      }
    }
  }

  const handleSaveDirectYouTube = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!directRefreshToken.trim()) return
    setSavingDirectToken(true)
    setError(null)
    try {
      const res = await api.creatorConnectYouTubeDirect(directRefreshToken.trim(), passcode)
      setSuccessMsg(res.message || `Connected channel: ${res.channel_title}!`)
      setShowYtDeviceModal(false)
      setDirectRefreshToken('')
      loadProfile(passcode)
    } catch (err: any) {
      setError(err?.message || 'Failed to connect YouTube with refresh token.')
    } finally {
      setSavingDirectToken(false)
    }
  }

  const handleDisconnectYouTube = async () => {
    if (!confirm('Are you sure you want to disconnect this YouTube Channel?')) return
    try {
      await api.creatorDisconnectYouTube(passcode)
      setSuccessMsg('YouTube channel disconnected.')
      loadProfile(passcode)
    } catch (err: any) {
      setError(err?.message || 'Failed to disconnect YouTube.')
    }
  }

  const handleSaveTelegram = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      setError(null)
      const res = await api.creatorConnectTelegram(tgBotToken.trim(), tgChatId.trim(), passcode)
      setSuccessMsg(res.message)
      setShowTelegramModal(false)
      setTgBotToken('')
      setTgChatId('')
      loadProfile(passcode)
    } catch (err: any) {
      setError(err?.message || 'Failed to connect Telegram Bot.')
    }
  }

  const handleDisconnectTelegram = async () => {
    if (!confirm('Disconnect personal Telegram bot notifications?')) return
    try {
      await api.creatorDisconnectTelegram(passcode)
      setSuccessMsg('Telegram disconnected.')
      loadProfile(passcode)
    } catch (err: any) {
      setError(err?.message || 'Failed to disconnect Telegram.')
    }
  }

  const handleSaveInstagram = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      setError(null)
      const res = await api.creatorConnectInstagram(igToken.trim(), igAccountId.trim(), passcode)
      setSuccessMsg(res.message)
      setShowInstaModal(false)
      setIgToken('')
      setIgAccountId('')
      loadProfile(passcode)
    } catch (err: any) {
      setError(err?.message || 'Failed to connect Instagram account.')
    }
  }

  const handleDisconnectInstagram = async () => {
    if (!confirm('Disconnect Instagram account?')) return
    try {
      await api.creatorDisconnectInstagram(passcode)
      setSuccessMsg('Instagram disconnected.')
      loadProfile(passcode)
    } catch (err: any) {
      setError(err?.message || 'Failed to disconnect Instagram.')
    }
  }

  const handleLaunchClipping = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!videoUrl.trim()) return
    setIsSubmittingJob(true)
    setError(null)
    setSuccessMsg(null)
    try {
      const form = new FormData()
      form.append('url', videoUrl.trim())
      if (briefText.trim()) {
        form.append('campaign_url', '')
      }
      const overrides: any = {
        editing_preset: 'all_in_one_viral',
        creator_id: profile?.id,
        dispatch_mode: 'local',
      }
      form.append('overrides', JSON.stringify(overrides))

      const job = await api.createAutonomousJob(form)
      setSuccessMsg(`Autonomous Clipping Job #${job.id.slice(0, 8)} started! AI will analyze highlights, render 9:16 vertical edits, and notify you via Telegram.`)
      setVideoUrl('')
      setBriefText('')
      loadJobs(passcode)
    } catch (err: any) {
      setError(err?.message || 'Failed to launch clipping job.')
    } finally {
      setIsSubmittingJob(false)
    }
  }

  // ---------------------------------------------------------------------------
  // View 1: Passcode Login View
  // ---------------------------------------------------------------------------
  if (!profile) {
    return (
      <div className="mx-auto max-w-lg px-6 py-20">
        <div className="rounded-2xl border border-ink-800 bg-ink-950 p-8 shadow-2xl">
          <div className="mb-6 text-center">
            <span className="text-4xl">🚀</span>
            <h1 className="mt-3 text-2xl font-bold tracking-tight text-ink-100">
              Creator Studio Access
            </h1>
            <p className="mt-2 text-sm text-ink-400">
              Enter your personal access passcode to manage your channels and automated clips.
            </p>
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-ink-400">
                Access Passcode
              </label>
              <input
                type="password"
                value={passcode}
                onChange={(e) => setPasscode(e.target.value)}
                placeholder="e.g. clp_9a4f21bc or admin key"
                className="mt-1 w-full rounded-lg border border-ink-700 bg-ink-900 px-4 py-3 text-ink-100 placeholder-ink-500 focus:border-sodium-500 focus:outline-none"
                required
              />
            </div>

            {error && (
              <div className="rounded-lg bg-red-500/10 p-3 text-xs text-red-400 border border-red-500/20">
                ⚠️ {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-lg bg-sodium-500 py-3 text-sm font-semibold text-ink-950 transition hover:bg-sodium-400 disabled:opacity-50"
            >
              {loading ? 'Authenticating...' : 'Enter Creator Portal →'}
            </button>
          </form>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // View 2: Creator Dashboard with 1-Click Platform Linking
  // ---------------------------------------------------------------------------
  return (
    <div className="mx-auto max-w-5xl px-6 py-10">
      {/* Top Banner */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-ink-800 pb-6">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-ink-100">{profile.display_name}</h1>
            <span className="rounded-full bg-sodium-500/10 px-3 py-0.5 text-xs font-semibold text-sodium-400 border border-sodium-500/20">
              {profile.role.toUpperCase()}
            </span>
          </div>
          <p className="mt-1 text-xs text-ink-400">
            Dedicated workspace running 24/7 on high-speed Oracle VPS.
          </p>
        </div>

        <button
          onClick={() => {
            localStorage.removeItem('alamr_creator_passcode')
            setProfile(null)
          }}
          className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800"
        >
          Logout / Switch Account
        </button>
      </div>

      {/* Notifications */}
      {successMsg && (
        <div className="mt-6 rounded-lg bg-emerald-500/10 p-4 text-sm text-emerald-300 border border-emerald-500/20 flex justify-between items-center">
          <span>✅ {successMsg}</span>
          <button onClick={() => setSuccessMsg(null)} className="text-xs text-emerald-400 underline">Dismiss</button>
        </div>
      )}
      {error && (
        <div className="mt-6 rounded-lg bg-red-500/10 p-4 text-sm text-red-300 border border-red-500/20 flex justify-between items-center">
          <span>⚠️ {error}</span>
          <button onClick={() => setError(null)} className="text-xs text-red-400 underline">Dismiss</button>
        </div>
      )}

      {/* Connected Channels Grid */}
      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-ink-400">
          Connected Accounts & Destinations
        </h2>

        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-3">
          {/* 1. YouTube Card */}
          <div className="flex flex-col justify-between rounded-xl border border-ink-800 bg-ink-950 p-5 shadow-lg">
            <div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">🔴</span>
                  <span className="font-semibold text-ink-100">YouTube Shorts</span>
                </div>
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    profile.youtube.connected ? 'bg-emerald-400' : 'bg-ink-600'
                  }`}
                />
              </div>

              <div className="mt-4">
                {profile.youtube.connected ? (
                  <div>
                    <div className="text-xs text-ink-400">Connected Channel:</div>
                    <div className="mt-0.5 font-medium text-ink-100">
                      {profile.youtube.channel_title || 'Verified Creator Channel'}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-ink-400">
                    Connect your channel in 1 click via official Google Sign-In.
                  </p>
                )}
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-ink-900">
              {profile.youtube.connected ? (
                <button
                  onClick={handleDisconnectYouTube}
                  className="w-full rounded bg-red-500/10 py-2 text-xs font-medium text-red-400 hover:bg-red-500/20"
                >
                  Disconnect Channel
                </button>
              ) : (
                <button
                  onClick={handleConnectYouTube}
                  className="w-full rounded bg-red-600 py-2 text-xs font-semibold text-white shadow hover:bg-red-500"
                >
                  Connect YouTube ↗
                </button>
              )}
            </div>
          </div>

          {/* 2. Instagram Card */}
          <div className="flex flex-col justify-between rounded-xl border border-ink-800 bg-ink-950 p-5 shadow-lg">
            <div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">🟣</span>
                  <span className="font-semibold text-ink-100">Instagram Reels</span>
                </div>
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    profile.instagram.connected ? 'bg-emerald-400' : 'bg-ink-600'
                  }`}
                />
              </div>

              <div className="mt-4">
                {profile.instagram.connected ? (
                  <div>
                    <div className="text-xs text-ink-400">Connected Account:</div>
                    <div className="mt-0.5 font-medium text-ink-100">
                      @{profile.instagram.account_name || 'Instagram Business'}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-ink-400">
                    Publish viral vertical Reels directly to your Instagram profile.
                  </p>
                )}
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-ink-900">
              {profile.instagram.connected ? (
                <button
                  onClick={handleDisconnectInstagram}
                  className="w-full rounded bg-red-500/10 py-2 text-xs font-medium text-red-400 hover:bg-red-500/20"
                >
                  Disconnect Account
                </button>
              ) : (
                <button
                  onClick={() => setShowInstaModal(true)}
                  className="w-full rounded bg-gradient-to-r from-pink-600 to-purple-600 py-2 text-xs font-semibold text-white shadow hover:opacity-90"
                >
                  Connect Instagram ↗
                </button>
              )}
            </div>
          </div>

          {/* 3. Telegram Dedicated Bot Card */}
          <div className="flex flex-col justify-between rounded-xl border border-ink-800 bg-ink-950 p-5 shadow-lg">
            <div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">✈️</span>
                  <span className="font-semibold text-ink-100">Telegram Bot</span>
                </div>
                <span
                  className={`h-2.5 w-2.5 rounded-full ${
                    profile.telegram.connected ? 'bg-emerald-400' : 'bg-ink-600'
                  }`}
                />
              </div>

              <div className="mt-4">
                {profile.telegram.connected ? (
                  <div>
                    <div className="text-xs text-ink-400">Review Destination:</div>
                    <div className="mt-0.5 font-medium text-ink-100">
                      Chat ID #{profile.telegram.chat_id}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-ink-400">
                    Receive 1-click Approve/Reject preview video messages to your private Telegram.
                  </p>
                )}
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-ink-900">
              {profile.telegram.connected ? (
                <button
                  onClick={handleDisconnectTelegram}
                  className="w-full rounded bg-red-500/10 py-2 text-xs font-medium text-red-400 hover:bg-red-500/20"
                >
                  Disconnect Bot
                </button>
              ) : (
                <button
                  onClick={() => setShowTelegramModal(true)}
                  className="w-full rounded bg-sky-600 py-2 text-xs font-semibold text-white shadow hover:bg-sky-500"
                >
                  Connect Telegram ↗
                </button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Main Action: Launch Autonomous Clipping Job */}
      <div className="mt-12 rounded-2xl border border-ink-800 bg-ink-950 p-8 shadow-2xl">
        <div className="flex items-center justify-between border-b border-ink-800 pb-5">
          <div>
            <h2 className="text-lg font-bold text-ink-100">Launch New Clipping Job</h2>
            <p className="mt-1 text-xs text-ink-400">
              AI analyzes the podcast, detects viral hooks, generates kinetic captions, and prepares uploads for your channels.
            </p>
          </div>
          <span className="text-2xl">⚡</span>
        </div>

        <form onSubmit={handleLaunchClipping} className="mt-6 space-y-6">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-ink-300">
              YouTube Source Video URL <span className="text-sodium-400">*</span>
            </label>
            <input
              type="url"
              value={videoUrl}
              onChange={(e) => setVideoUrl(e.target.value)}
              placeholder="https://www.youtube.com/watch?v=..."
              className="mt-2 w-full rounded-lg border border-ink-700 bg-ink-900 px-4 py-3 text-sm text-ink-100 placeholder-ink-500 focus:border-sodium-500 focus:outline-none"
              required
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-ink-300">
              Whop Campaign Brief / Client Guidelines (Optional)
            </label>
            <textarea
              rows={4}
              value={briefText}
              onChange={(e) => setBriefText(e.target.value)}
              placeholder="Paste mandatory tags, prohibited words, minimum duration (e.g. Must tag @future_founders11, min 30s)..."
              className="mt-2 w-full rounded-lg border border-ink-700 bg-ink-900 p-4 text-sm text-ink-100 placeholder-ink-500 focus:border-sodium-500 focus:outline-none"
            />
            <p className="mt-1 text-xs text-ink-500">
              Powered by Google Gemini Flash API with local DeepSeek-R1 fallback for 100% compliance.
            </p>
          </div>

          <button
            type="submit"
            disabled={isSubmittingJob || !videoUrl.trim()}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-sodium-500 to-amber-500 py-3.5 text-base font-bold text-ink-950 shadow-lg transition hover:brightness-110 disabled:opacity-50"
          >
            {isSubmittingJob ? (
              <span>Processing Job...</span>
            ) : (
              <span>🚀 Start Autonomous Video Clipping</span>
            )}
          </button>
        </form>
      </div>

      {/* Live Video Pipeline & Generated Clips Section */}
      <div className="mt-12">
        <div className="flex items-center justify-between border-b border-ink-800 pb-4">
          <div>
            <h2 className="text-lg font-bold text-ink-100">Your Video Pipeline & Clips</h2>
            <p className="mt-0.5 text-xs text-ink-400">
              Real-time VPS processing status, 9:16 vertical renders, and automated social delivery.
            </p>
          </div>
          <button
            onClick={() => loadJobs(passcode)}
            className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800"
          >
            ↻ Refresh Status
          </button>
        </div>

        {creatorJobsList.length === 0 ? (
          <div className="mt-6 rounded-xl border border-ink-800 bg-ink-950 p-8 text-center text-xs text-ink-400">
            No clipping jobs launched yet. Paste any YouTube link above to start!
          </div>
        ) : (
          <div className="mt-6 space-y-6">
            {creatorJobsList.map((job) => {
              const isRunning = !['done', 'failed', 'cancelled'].includes(job.status)
              const progressPct = Math.min(100, Math.max(0, Math.round((job.progress || 0) * 100)))

              return (
                <div key={job.id} className="rounded-2xl border border-ink-800 bg-ink-950 p-6 shadow-xl">
                  {/* Header */}
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-900 pb-4">
                    <div>
                      <div className="flex items-center gap-2.5">
                        <span className="font-semibold text-ink-100 text-sm">
                          {job.source_title || 'Autonomous Video Job'}
                        </span>
                        <span
                          className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold uppercase tracking-wider ${
                            job.status === 'done'
                              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                              : job.status === 'failed'
                              ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                              : 'bg-sodium-500/15 text-sodium-300 border border-sodium-500/30 animate-pulse'
                          }`}
                        >
                          {job.status === 'done' ? '✓ Completed' : job.status === 'failed' ? 'Failed' : `${job.status} (${progressPct}%)`}
                        </span>
                      </div>
                      {job.source_url && (
                        <a
                          href={job.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-1 block text-xs text-ink-500 hover:text-ink-300 truncate max-w-md"
                        >
                          {job.source_url}
                        </a>
                      )}
                    </div>
                    <div className="text-right text-[11px] text-ink-500 font-mono">
                      Job #{job.id.slice(0, 8)}
                    </div>
                  </div>

                  {/* Stage Progress Bar */}
                  {isRunning && (
                    <div className="mt-5 rounded-xl bg-ink-900/80 p-4 border border-ink-800">
                      <div className="flex items-center justify-between text-xs font-medium text-ink-300">
                        <span className="flex items-center gap-2">
                          <span className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-2 border-sodium-400 border-t-transparent" />
                          <span>Stage: <strong className="text-sodium-400 capitalize">{job.current_stage || 'Processing'}</strong></span>
                        </span>
                        <span className="font-bold text-sodium-400">{progressPct}%</span>
                      </div>

                      {/* Visual Progress Bar */}
                      <div className="mt-3 h-2.5 w-full overflow-hidden rounded-full bg-ink-950">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-sodium-500 to-amber-400 transition-all duration-500"
                          style={{ width: `${Math.max(5, progressPct)}%` }}
                        />
                      </div>

                      {/* Pipeline Milestones */}
                      <div className="mt-3 grid grid-cols-5 gap-1 text-[10px] text-center text-ink-400">
                        <div className={progressPct >= 10 ? 'text-sodium-400 font-semibold' : ''}>1. Ingest</div>
                        <div className={progressPct >= 30 ? 'text-sodium-400 font-semibold' : ''}>2. Whisper AI</div>
                        <div className={progressPct >= 60 ? 'text-sodium-400 font-semibold' : ''}>3. Viral Hooks</div>
                        <div className={progressPct >= 80 ? 'text-sodium-400 font-semibold' : ''}>4. 9:16 Reframe</div>
                        <div className={progressPct >= 95 ? 'text-sodium-400 font-semibold' : ''}>5. Export & Send</div>
                      </div>
                    </div>
                  )}

                  {job.error && (
                    <div className="mt-4 rounded-lg bg-red-500/10 p-3 text-xs text-red-400 border border-red-500/20">
                      ⚠️ {job.error}
                    </div>
                  )}

                  {/* Rendered Clips Gallery for this job */}
                  {job.clips && job.clips.length > 0 && (
                    <div className="mt-6">
                      <h4 className="text-xs font-semibold uppercase tracking-wider text-ink-400">
                        Generated Vertical Shorts ({job.clips.length})
                      </h4>
                      <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                        {job.clips.map((clip: any) => {
                          const firstExport = clip.exports && clip.exports.length > 0 ? clip.exports[0] : null
                          return (
                            <div
                              key={clip.id}
                              className="flex flex-col justify-between rounded-xl border border-ink-800 bg-ink-900 p-4 transition hover:border-ink-700"
                            >
                              <div>
                                <div className="flex items-center justify-between text-xs">
                                  <span className="rounded bg-sodium-500/10 px-2 py-0.5 font-bold text-sodium-400 border border-sodium-500/20">
                                    Virality: {clip.score}/100
                                  </span>
                                  <span className="text-ink-400">{clip.duration_s}s</span>
                                </div>
                                <h5 className="mt-2.5 line-clamp-2 text-sm font-semibold text-ink-100">
                                  {clip.title || 'Viral Highlight Clip'}
                                </h5>
                              </div>

                              <div className="mt-4 flex gap-2 pt-3 border-t border-ink-800">
                                {firstExport ? (
                                  <>
                                    <button
                                      onClick={() => {
                                        setPreviewClipTitle(clip.title || 'Clip Preview')
                                        setPreviewExportUrl(firstExport.stream_url)
                                      }}
                                      className="flex-1 rounded-lg bg-sodium-500 py-2 text-center text-xs font-bold text-ink-950 transition hover:bg-sodium-400"
                                    >
                                      ▶ Preview
                                    </button>
                                    <a
                                      href={firstExport.stream_url}
                                      download={`${(clip.title || 'clip').replace(/[^a-zA-Z0-9_-]/g, '_')}.mp4`}
                                      className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-center text-xs font-semibold text-ink-200 transition hover:bg-ink-700"
                                      title="Download MP4"
                                    >
                                      ⬇ MP4
                                    </a>
                                  </>
                                ) : (
                                  <span className="w-full text-center text-[11px] text-ink-500 py-1">
                                    Rendering vertical export...
                                  </span>
                                )}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Video Preview Modal */}
      {previewExportUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 p-4 backdrop-blur-md"
          onClick={() => setPreviewExportUrl(null)}
        >
          <div
            className="relative w-full max-w-sm rounded-2xl border border-ink-700 bg-ink-900 p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-ink-800 pb-3">
              <h4 className="truncate text-sm font-bold text-ink-100 pr-2">
                {previewClipTitle}
              </h4>
              <button
                onClick={() => setPreviewExportUrl(null)}
                className="text-ink-400 hover:text-white text-base leading-none"
              >
                ✕
              </button>
            </div>
            <div className="mt-4 flex justify-center bg-black rounded-xl overflow-hidden shadow-inner">
              <video
                src={previewExportUrl}
                controls
                autoPlay
                className="max-h-[70vh] w-auto rounded-xl object-contain"
              />
            </div>
            <div className="mt-4 flex justify-end">
              <a
                href={previewExportUrl}
                download="viral_clip.mp4"
                className="rounded-lg bg-sodium-500 px-4 py-2 text-xs font-bold text-ink-950 hover:bg-sodium-400"
              >
                ⬇ Download Clip
              </a>
            </div>
          </div>
        </div>
      )}

      {/* Telegram Modal */}
      {showTelegramModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/80 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-ink-800 bg-ink-900 p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-ink-100">Connect Telegram Bot</h3>
            <p className="mt-1 text-xs text-ink-400">
              Create a bot via @BotFather and enter its token and your Chat ID.
            </p>

            <form onSubmit={handleSaveTelegram} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-ink-300">Bot Token</label>
                <input
                  type="text"
                  value={tgBotToken}
                  onChange={(e) => setTgBotToken(e.target.value)}
                  placeholder="e.g. 123456789:ABCdefGHIjkl..."
                  className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-xs text-ink-100 focus:border-sodium-500 focus:outline-none"
                  required
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-ink-300">Chat ID</label>
                <input
                  type="text"
                  value={tgChatId}
                  onChange={(e) => setTgChatId(e.target.value)}
                  placeholder="e.g. 987654321"
                  className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-xs text-ink-100 focus:border-sodium-500 focus:outline-none"
                  required
                />
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowTelegramModal(false)}
                  className="rounded px-4 py-2 text-xs text-ink-400 hover:text-ink-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="rounded bg-sky-500 px-4 py-2 text-xs font-semibold text-white hover:bg-sky-400"
                >
                  Save & Connect
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Instagram Modal */}
      {showInstaModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/80 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-ink-800 bg-ink-900 p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-ink-100">Connect Instagram Account</h3>
            <p className="mt-1 text-xs text-ink-400">
              Provide your Meta Graph API User Access Token and Instagram Business Account ID.
            </p>

            <form onSubmit={handleSaveInstagram} className="mt-4 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-ink-300">Access Token</label>
                <input
                  type="text"
                  value={igToken}
                  onChange={(e) => setIgToken(e.target.value)}
                  placeholder="EAABwz..."
                  className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-xs text-ink-100 focus:border-sodium-500 focus:outline-none"
                  required
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-ink-300">Instagram Account ID</label>
                <input
                  type="text"
                  value={igAccountId}
                  onChange={(e) => setIgAccountId(e.target.value)}
                  placeholder="e.g. 17841400..."
                  className="mt-1 w-full rounded border border-ink-700 bg-ink-950 px-3 py-2 text-xs text-ink-100 focus:border-sodium-500 focus:outline-none"
                  required
                />
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowInstaModal(false)}
                  className="rounded px-4 py-2 text-xs text-ink-400 hover:text-ink-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="rounded bg-pink-600 px-4 py-2 text-xs font-semibold text-white hover:bg-pink-500"
                >
                  Save & Connect
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* YouTube Channel Linking Modal (Google Device Code / Direct Refresh Token) */}
      {showYtDeviceModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/85 p-4 backdrop-blur-md">
          <div className="w-full max-w-lg rounded-2xl border border-red-500/30 bg-ink-900 p-7 shadow-2xl">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-red-600/20 text-xl text-red-500">
                  ▶
                </span>
                <div>
                  <h3 className="text-lg font-bold text-ink-100">Connect YouTube Channel</h3>
                  <p className="text-xs text-ink-400">Select your preferred authorization method</p>
                </div>
              </div>
              <button
                onClick={() => {
                  stopPollingYouTube()
                  setShowYtDeviceModal(false)
                }}
                className="text-ink-400 hover:text-ink-100 text-lg leading-none"
              >
                ✕
              </button>
            </div>

            {/* Modal Tabs */}
            <div className="mt-5 flex rounded-xl border border-ink-800 bg-ink-950 p-1 text-xs">
              <button
                type="button"
                onClick={() => setYtTab('device')}
                className={`flex-1 rounded-lg py-2 font-semibold transition ${
                  ytTab === 'device'
                    ? 'bg-red-600 text-white shadow'
                    : 'text-ink-400 hover:text-ink-200'
                }`}
              >
                Google TV Code (Zero Config)
              </button>
              <button
                type="button"
                onClick={() => setYtTab('direct')}
                className={`flex-1 rounded-lg py-2 font-semibold transition ${
                  ytTab === 'direct'
                    ? 'bg-red-600 text-white shadow'
                    : 'text-ink-400 hover:text-ink-200'
                }`}
              >
                Direct Refresh Token
              </button>
            </div>

            {ytTab === 'device' ? (
              <div className="mt-5 space-y-4">
                <div className="rounded-xl border border-ink-800 bg-ink-950/70 p-4">
                  <div className="text-xs font-semibold uppercase tracking-wider text-ink-400">Step 1: Open Google Device Page</div>
                  <div className="mt-2 flex items-center justify-between">
                    <a
                      href={ytVerificationUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3.5 py-2 text-xs font-semibold text-white shadow transition hover:bg-red-500"
                    >
                      Open {ytVerificationUrl} ↗
                    </a>
                    <span className="text-xs text-ink-400">Opens in new tab</span>
                  </div>
                </div>

                <div className="rounded-xl border border-ink-800 bg-ink-950/70 p-4">
                  <div className="text-xs font-semibold uppercase tracking-wider text-ink-400">Step 2: Enter This Code</div>
                  <div className="mt-2 flex items-center justify-between gap-3">
                    <div className="rounded-lg border border-red-500/40 bg-red-950/30 px-4 py-2 font-mono text-2xl font-black tracking-widest text-red-400">
                      {ytUserCode || '...'}
                    </div>
                    <button
                      onClick={() => {
                        if (ytUserCode) {
                          navigator.clipboard.writeText(ytUserCode)
                          setYtCopied(true)
                          setTimeout(() => setYtCopied(false), 2000)
                        }
                      }}
                      className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-xs font-semibold text-ink-200 hover:bg-ink-700"
                    >
                      {ytCopied ? '✓ Copied!' : '📋 Copy Code'}
                    </button>
                  </div>
                </div>

                <div className="flex items-center gap-3 rounded-xl border border-cyan-500/20 bg-cyan-950/20 px-4 py-3 text-xs text-cyan-300">
                  <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-cyan-400 border-t-transparent" />
                  <span>Waiting for Google confirmation... This screen will automatically update once you click "Allow" on Google.</span>
                </div>
              </div>
            ) : (
              <form onSubmit={handleSaveDirectYouTube} className="mt-5 space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-ink-300">
                    YouTube OAuth Refresh Token
                  </label>
                  <p className="mt-0.5 text-[11px] text-ink-500">
                    Paste your <code>refresh_token</code> from your Google Cloud Console OAuth 2.0 app.
                  </p>
                  <textarea
                    rows={4}
                    value={directRefreshToken}
                    onChange={(e) => setDirectRefreshToken(e.target.value)}
                    placeholder="1//04..."
                    className="mt-2 w-full rounded-lg border border-ink-700 bg-ink-950 p-3 font-mono text-xs text-ink-100 focus:border-red-500 focus:outline-none"
                    required
                  />
                </div>

                <button
                  type="submit"
                  disabled={savingDirectToken || !directRefreshToken.trim()}
                  className="w-full rounded-lg bg-red-600 py-2.5 text-xs font-bold text-white shadow hover:bg-red-500 disabled:opacity-50"
                >
                  {savingDirectToken ? 'Linking Channel...' : 'Save & Link Channel ↗'}
                </button>
              </form>
            )}

            <div className="mt-6 flex justify-end">
              <button
                type="button"
                onClick={() => {
                  stopPollingYouTube()
                  setShowYtDeviceModal(false)
                }}
                className="rounded-lg px-4 py-2 text-xs text-ink-400 hover:text-ink-200"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
