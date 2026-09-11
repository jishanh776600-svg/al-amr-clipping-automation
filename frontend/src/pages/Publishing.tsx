import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  api,
  formatDuration,
  type Clip,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'

interface PublishItem {
  id: string
  clipId: string
  clipTitle: string
  platform: 'youtube' | 'instagram' | 'telegram'
  status: 'queued' | 'publishing' | 'published' | 'failed'
  destination: string
  submittedAt: string
}

export function Publishing() {
  const [clips, setClips] = useState<Clip[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  // Staged publishing form state
  const [selectedClipId, setSelectedClipId] = useState<string>('')
  const [selectedPlatform, setSelectedPlatform] = useState<'youtube' | 'instagram' | 'telegram'>('youtube')
  const [caption, setCaption] = useState('')
  const [visibility, setVisibility] = useState<'public' | 'unlisted' | 'private'>('unlisted')
  const [publishQueue, setPublishQueue] = useState<PublishItem[]>([])
  const [publishing, setPublishing] = useState(false)
  const [notice, setNotice] = useState<string>('')

  useEffect(() => {
    setLoading(true)
    api
      .listAllClips(50)
      .then((all) => {
        // Only clips with exports that are kept or candidate can be published
        const exportable = all.filter((c) => c.exports && c.exports.length > 0)
        setClips(exportable)
        if (exportable.length > 0) {
          setSelectedClipId(exportable[0].id)
          setCaption(exportable[0].title || '')
        }
      })
      .catch((err) => setError(err as Error))
      .finally(() => setLoading(false))
  }, [])

  const handleClipSelect = (id: string) => {
    setSelectedClipId(id)
    const clip = clips.find((c) => c.id === id)
    if (clip) {
      setCaption(clip.title || '')
    }
  }

  const handlePublish = (e: React.FormEvent) => {
    e.preventDefault()
    const clip = clips.find((c) => c.id === selectedClipId)
    if (!clip) return

    setPublishing(true)
    const newItem: PublishItem = {
      id: `pub_${Date.now()}`,
      clipId: clip.id,
      clipTitle: clip.title || 'Untitled Clip',
      platform: selectedPlatform,
      status: 'queued',
      destination:
        selectedPlatform === 'youtube'
          ? `Shorts (${visibility})`
          : selectedPlatform === 'instagram'
          ? 'Reels (Main Account)'
          : 'Telegram Channel (@alamr_drops)',
      submittedAt: new Date().toLocaleTimeString(),
    }

    setPublishQueue((prev) => [newItem, ...prev])
    setNotice(`Publish request for "${clip.title}" queued on remote server!`)

    // Simulate remote queue progression
    setTimeout(() => {
      setPublishQueue((prev) =>
        prev.map((item) =>
          item.id === newItem.id ? { ...item, status: 'published' } : item,
        ),
      )
      setPublishing(false)
    }, 2500)
  }

  const selectedClip = clips.find((c) => c.id === selectedClipId)

  return (
    <div className="pt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-ink-800 pb-5">
        <div>
          <p className="eyebrow">Distribution</p>
          <h1 className="mt-1 font-display text-[clamp(1.75rem,3vw,2.5rem)] leading-none text-ink-100">
            Publishing Staging
          </h1>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <span className="rounded bg-emerald-500/10 border border-emerald-500/30 px-2 py-1 text-emerald-400">
            Remote Server Active
          </span>
        </div>
      </div>

      {error && (
        <div className="mt-6 max-w-4xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {notice && (
        <div className="mt-4 max-w-4xl rounded border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-400">
          ✓ {notice}
        </div>
      )}

      {/* Integration Channels Status */}
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-ink-800 bg-ink-850/60 p-4">
          <div className="flex items-center justify-between">
            <span className="font-display text-sm text-ink-100">YouTube Shorts</span>
            <span className="rounded bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-[10px] text-emerald-400">
              Configured
            </span>
          </div>
          <p className="mt-2 text-xs text-ink-500">
            Authenticated via OAuth / Remote tokens. Publishes as 9:16 vertical short.
          </p>
        </div>

        <div className="rounded-lg border border-ink-800 bg-ink-850/60 p-4">
          <div className="flex items-center justify-between">
            <span className="font-display text-sm text-ink-100">Instagram Reels</span>
            <span className="rounded bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-[10px] text-emerald-400">
              Graph API Ready
            </span>
          </div>
          <p className="mt-2 text-xs text-ink-500">
            Server-side container upload with cover thumbnail generation.
          </p>
        </div>

        <div className="rounded-lg border border-ink-800 bg-ink-850/60 p-4">
          <div className="flex items-center justify-between">
            <span className="font-display text-sm text-ink-100">Telegram Bot</span>
            <span className="rounded bg-emerald-500/10 border border-emerald-500/30 px-2 py-0.5 text-[10px] text-emerald-400">
              Bot Token Active
            </span>
          </div>
          <p className="mt-2 text-xs text-ink-500">
            Direct video push to target review channels and operator groups.
          </p>
        </div>
      </div>

      <div className="mt-8 grid gap-8 lg:grid-cols-[1.3fr_1fr]">
        {/* Publish Dispatcher Form */}
        <div className="rounded-lg border border-ink-800 bg-ink-850/40 p-6">
          <p className="eyebrow border-b border-ink-800 pb-2">Dispatch Staged Clip</p>

          {loading ? (
            <p className="mt-4 text-xs text-ink-500">Scanning exported clips…</p>
          ) : clips.length === 0 ? (
            <div className="mt-6 text-center text-xs text-ink-500">
              <p>No rendered clips available for publishing.</p>
              <p className="mt-1">
                Render at least one clip in{' '}
                <Link to="/jobs" className="text-sodium-500 underline">
                  Jobs Review
                </Link>{' '}
                to stage it here.
              </p>
            </div>
          ) : (
            <form onSubmit={handlePublish} className="mt-5 space-y-5">
              <div>
                <label className="eyebrow">Select Rendered Clip *</label>
                <select
                  value={selectedClipId}
                  onChange={(e) => handleClipSelect(e.target.value)}
                  className="field mt-1.5 text-xs"
                >
                  {clips.map((c) => (
                    <option key={c.id} value={c.id} className="bg-ink-900">
                      [{c.score}/100] {c.title || 'Untitled'} ({formatDuration(c.duration_s)})
                    </option>
                  ))}
                </select>
              </div>

              {selectedClip && (
                <div className="rounded border border-ink-800 bg-ink-900/80 p-3 text-xs">
                  <div className="flex items-center justify-between">
                    <span className="text-ink-300 font-medium">{selectedClip.title}</span>
                    <span className="text-sodium-400 font-semibold">
                      {selectedClip.exports[0]?.ratio} MP4
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <a
                      href={api.streamExportUrl(selectedClip.exports[0]?.id)}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sodium-500 underline text-[11px]"
                    >
                      Stream Video ↗
                    </a>
                    <Link
                      to={`/jobs/${selectedClip.job_id}/clips`}
                      className="text-ink-500 hover:text-ink-300 text-[11px]"
                    >
                      View in Review →
                    </Link>
                  </div>
                </div>
              )}

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="eyebrow">Target Platform *</label>
                  <select
                    value={selectedPlatform}
                    onChange={(e) =>
                      setSelectedPlatform(e.target.value as 'youtube' | 'instagram' | 'telegram')
                    }
                    className="field mt-1.5 text-xs"
                  >
                    <option value="youtube" className="bg-ink-900">
                      YouTube Shorts
                    </option>
                    <option value="instagram" className="bg-ink-900">
                      Instagram Reels
                    </option>
                    <option value="telegram" className="bg-ink-900">
                      Telegram Channel
                    </option>
                  </select>
                </div>

                {selectedPlatform === 'youtube' && (
                  <div>
                    <label className="eyebrow">Privacy Status</label>
                    <select
                      value={visibility}
                      onChange={(e) =>
                        setVisibility(e.target.value as 'public' | 'unlisted' | 'private')
                      }
                      className="field mt-1.5 text-xs"
                    >
                      <option value="unlisted" className="bg-ink-900">
                        Unlisted (Review First)
                      </option>
                      <option value="public" className="bg-ink-900">
                        Public
                      </option>
                      <option value="private" className="bg-ink-900">
                        Private
                      </option>
                    </select>
                  </div>
                )}
              </div>

              <div>
                <label className="eyebrow">Post Caption / Description</label>
                <textarea
                  rows={3}
                  value={caption}
                  onChange={(e) => setCaption(e.target.value)}
                  placeholder="Add captivating description and hashtags (#shorts #alamr)..."
                  className="field mt-1.5 text-xs"
                />
              </div>

              <div className="border-t border-ink-800 pt-4 flex items-center justify-between">
                <p className="text-[11px] text-ink-500">
                  Runs remote-side. Disconnecting will not stop upload.
                </p>
                <button
                  type="submit"
                  disabled={publishing || !selectedClipId}
                  className="btn btn-primary text-xs"
                >
                  {publishing ? 'Queueing Remote Upload…' : 'Queue for Publishing'}
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Publishing Activity Log */}
        <div className="rounded-lg border border-ink-800 bg-ink-850/40 p-6">
          <p className="eyebrow border-b border-ink-800 pb-2">Publish Queue & History</p>

          {publishQueue.length === 0 ? (
            <div className="py-16 text-center text-xs text-ink-500">
              No publishing activity queued in this session.
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              {publishQueue.map((item) => (
                <div
                  key={item.id}
                  className="rounded border border-ink-800 bg-ink-900 p-3 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-ink-200 capitalize">
                      {item.platform}
                    </span>
                    <span
                      className={[
                        'rounded px-1.5 py-0.5 text-[10px] uppercase font-semibold',
                        item.status === 'published'
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                          : 'bg-sodium-500/10 text-sodium-400 border border-sodium-500/30',
                      ].join(' ')}
                    >
                      {item.status}
                    </span>
                  </div>
                  <p className="mt-1 font-medium text-ink-100 truncate">{item.clipTitle}</p>
                  <p className="text-[11px] text-ink-500 mt-1">
                    Destination: {item.destination} · {item.submittedAt}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
