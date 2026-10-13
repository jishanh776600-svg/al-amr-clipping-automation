import React, { useEffect, useState } from 'react'
import { api, type CreatorProfile } from '../api'

export function CreatorPortal() {
  const [passcode, setPasscode] = useState(() => localStorage.getItem('alamr_creator_passcode') || '')
  const [profile, setProfile] = useState<CreatorProfile | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)

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

  // Fetch profile if passcode is stored
  const loadProfile = async (code: string) => {
    if (!code) return
    setLoading(true)
    setError(null)
    try {
      const p = await api.creatorProfile(code)
      setProfile(p)
      localStorage.setItem('alamr_creator_passcode', code)
    } catch (err: any) {
      setError(err?.message || 'Invalid or expired passcode.')
      setProfile(null)
    } finally {
      setLoading(false)
    }
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
  }, [])

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
    try {
      setError(null)
      const redirectUri = window.location.origin + window.location.pathname
      const res = await api.creatorYouTubeAuthUrl(redirectUri, passcode)
      if (res?.auth_url) {
        window.location.href = res.auth_url
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to initialize YouTube OAuth.')
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
      }
      form.append('overrides', JSON.stringify(overrides))

      const job = await api.createAutonomousJob(form)
      setSuccessMsg(`Autonomous Clipping Job #${job.id.slice(0, 8)} started! AI will analyze highlights, render 9:16 vertical edits, and notify you via Telegram.`)
      setVideoUrl('')
      setBriefText('')
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
    </div>
  )
}
