import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  api,
  formatDuration,
  type Clip,
  type PublishingPlatformInfo,
  type PublishingRecord,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'

export function Publishing() {
  const [clips, setClips] = useState<Clip[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)

  // Remote platforms and records
  const [platforms, setPlatforms] = useState<PublishingPlatformInfo[]>([])
  const [publishingRecords, setPublishingRecords] = useState<PublishingRecord[]>([])

  // Staged publishing form state
  const [selectedClipId, setSelectedClipId] = useState<string>('')
  const [selectedPlatform, setSelectedPlatform] = useState<'youtube' | 'instagram' | 'telegram'>('youtube')
  const [caption, setCaption] = useState('')
  const [dryRun, setDryRun] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [retryingId, setRetryingId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string>('')

  const loadData = async () => {
    try {
      const [allClips, platInfo, recs] = await Promise.all([
        api.listAllClips(50),
        api.getPublishingPlatforms().catch(() => []),
        api.listPublishing({ limit: 50 }).catch(() => []),
      ])

      const exportable = allClips.filter((c) => c.exports && c.exports.length > 0)
      setClips(exportable)
      if (exportable.length > 0 && !selectedClipId) {
        setSelectedClipId(exportable[0].id)
        setCaption(exportable[0].title || '')
      }
      setPlatforms(platInfo)
      setPublishingRecords(recs)
    } catch (err) {
      setError(err as Error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    loadData()
  }, [])

  // Auto-refresh publishing records if any are publishing/pending
  useEffect(() => {
    const hasActive = publishingRecords.some((r) => r.status === 'publishing' || r.status === 'pending')
    if (!hasActive) return

    const interval = setInterval(() => {
      api.listPublishing({ limit: 50 }).then(setPublishingRecords).catch(() => {})
    }, 3000)

    return () => clearInterval(interval)
  }, [publishingRecords])

  const handleClipSelect = (id: string) => {
    setSelectedClipId(id)
    const clip = clips.find((c) => c.id === id)
    if (clip) {
      setCaption(clip.title || '')
    }
  }

  const handlePublish = async (e: React.FormEvent) => {
    e.preventDefault()
    const clip = clips.find((c) => c.id === selectedClipId)
    if (!clip || !clip.exports || clip.exports.length === 0) {
      setError(new Error('Selected clip does not have an exported video.'))
      return
    }

    const exportRec = clip.exports[0]
    setPublishing(true)
    setNotice('')
    setError(null)

    try {
      await api.publishExport(exportRec.id, {
        platforms: [selectedPlatform],
        title: clip.title,
        description: caption,
        dry_run: dryRun,
      })
      setNotice(`Publish dispatched for "${clip.title}" to ${selectedPlatform.toUpperCase()}!`)
      // Refresh records
      const recs = await api.listPublishing({ limit: 50 })
      setPublishingRecords(recs)
    } catch (err) {
      setError(err as Error)
    } finally {
      setPublishing(false)
    }
  }

  const handleRetry = async (recordId: string) => {
    setRetryingId(recordId)
    setError(null)
    try {
      await api.retryPublishing(recordId)
      const recs = await api.listPublishing({ limit: 50 })
      setPublishingRecords(recs)
      setNotice('Retry published successfully!')
    } catch (err) {
      setError(err as Error)
    } finally {
      setRetryingId(null)
    }
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
        {platforms.length > 0 ? (
          platforms.map((p) => (
            <div key={p.platform} className="rounded-lg border border-ink-800 bg-ink-850/60 p-4">
              <div className="flex items-center justify-between">
                <span className="font-display text-sm text-ink-100 capitalize">
                  {p.platform === 'youtube'
                    ? 'YouTube Shorts'
                    : p.platform === 'instagram'
                    ? 'Instagram Reels'
                    : 'Telegram Bot'}
                </span>
                <span
                  className={`rounded border px-2 py-0.5 text-[10px] ${
                    p.configured
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                      : 'bg-amber-500/10 border-amber-500/30 text-amber-400'
                  }`}
                >
                  {p.configured ? 'Configured' : 'Needs Config'}
                </span>
              </div>
              <p className="mt-2 text-xs text-ink-500">{p.details}</p>
            </div>
          ))
        ) : (
          <div className="col-span-3 text-xs text-ink-500">Loading channel configs…</div>
        )}
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
                    Telegram Channel / Group
                  </option>
                </select>
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

              <div>
                <label className="flex items-center gap-2 cursor-pointer text-xs text-ink-400">
                  <input
                    type="checkbox"
                    checked={dryRun}
                    onChange={(e) => setDryRun(e.target.checked)}
                    className="rounded border-ink-700 bg-ink-900 text-sodium-500 focus:ring-0"
                  />
                  <span>Dry Run (Validate API/OAuth tokens without publishing public video)</span>
                </label>
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
                  {publishing ? 'Publishing…' : 'Publish Clip'}
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Publishing Activity Log */}
        <div className="rounded-lg border border-ink-800 bg-ink-850/40 p-6">
          <div className="flex items-center justify-between border-b border-ink-800 pb-2">
            <p className="eyebrow">Publish History & Status</p>
            <button
              type="button"
              onClick={() => api.listPublishing({ limit: 50 }).then(setPublishingRecords)}
              className="text-[11px] text-sodium-500 hover:underline"
            >
              Refresh ↻
            </button>
          </div>

          {publishingRecords.length === 0 ? (
            <div className="py-16 text-center text-xs text-ink-500">
              No publishing activity recorded yet.
            </div>
          ) : (
            <div className="mt-4 space-y-3 max-h-[600px] overflow-y-auto pr-1">
              {publishingRecords.map((item) => (
                <div
                  key={item.id}
                  className="rounded border border-ink-800 bg-ink-900 p-3 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-ink-200 capitalize">
                      {item.platform}
                    </span>
                    <div className="flex items-center gap-2">
                      <span
                        className={[
                          'rounded px-1.5 py-0.5 text-[10px] uppercase font-semibold',
                          item.status === 'published'
                            ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                            : item.status === 'failed'
                            ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                            : 'bg-sodium-500/10 text-sodium-400 border border-sodium-500/30',
                        ].join(' ')}
                      >
                        {item.status}
                      </span>
                      {item.status === 'failed' && (
                        <button
                          type="button"
                          onClick={() => handleRetry(item.id)}
                          disabled={retryingId === item.id}
                          className="text-[10px] text-sodium-400 hover:underline border border-sodium-500/30 rounded px-1"
                        >
                          {retryingId === item.id ? 'Retrying…' : 'Retry'}
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="mt-1 font-medium text-ink-100 truncate">
                    {item.metadata?.title || 'Clip Export'}
                  </p>
                  {item.destination && (
                    <p className="text-[11px] text-ink-500 mt-0.5">Dest: {item.destination}</p>
                  )}
                  {item.external_id && (
                    <p className="text-[11px] text-emerald-400 mt-0.5 truncate">
                      Ext ID / Link:{' '}
                      {item.metadata?.url ? (
                        <a
                          href={item.metadata.url}
                          target="_blank"
                          rel="noreferrer"
                          className="underline"
                        >
                          {item.external_id} ↗
                        </a>
                      ) : (
                        item.external_id
                      )}
                    </p>
                  )}
                  {item.error && (
                    <p className="text-[11px] text-rose-400 mt-1 break-words">
                      Error: {item.error}
                    </p>
                  )}
                  <p className="text-[10px] text-ink-600 mt-1">
                    {new Date(item.updated_at).toLocaleString()}
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
