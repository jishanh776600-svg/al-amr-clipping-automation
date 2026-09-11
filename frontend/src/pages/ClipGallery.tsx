import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  api,
  formatDuration,
  type Clip,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'

export function ClipGallery() {
  const [clips, setClips] = useState<Clip[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [statusFilter, setStatusFilter] = useState<'all' | 'kept' | 'candidate' | 'discarded'>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [previewClip, setPreviewClip] = useState<Clip | null>(null)

  const loadClips = () => {
    setLoading(true)
    api
      .listAllClips(100)
      .then((data) => {
        setClips(data)
        setError(null)
      })
      .catch((err) => setError(err as Error))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    loadClips()
  }, [])

  const filtered = clips.filter((c) => {
    if (statusFilter !== 'all' && c.status !== statusFilter) return false
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      const titleMatch = (c.title || '').toLowerCase().includes(q)
      const reasonMatch = (c.evaluation?.reasoning || c.reason || '').toLowerCase().includes(q)
      if (!titleMatch && !reasonMatch) return false
    }
    return true
  })

  return (
    <div className="pt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-ink-800 pb-5">
        <div>
          <p className="eyebrow">Library</p>
          <h1 className="mt-1 font-display text-[clamp(1.75rem,3vw,2.5rem)] leading-none text-ink-100">
            Clip Gallery
          </h1>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={loadClips}
            disabled={loading}
            className="btn btn-quiet text-xs"
          >
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <Link to="/new" className="btn btn-primary text-xs">
            + New Ingest
          </Link>
        </div>
      </div>

      {error && (
        <div className="mt-6 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {/* Filters & Search */}
      <div className="mt-6 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-2 text-xs">
          {(['all', 'kept', 'candidate', 'discarded'] as const).map((filter) => (
            <button
              key={filter}
              onClick={() => setStatusFilter(filter)}
              className={[
                'px-3 py-1.5 rounded uppercase tracking-wider font-semibold transition-colors',
                statusFilter === filter
                  ? 'bg-sodium-500 text-ink-950'
                  : 'bg-ink-800 text-ink-400 hover:text-ink-200',
              ].join(' ')}
            >
              {filter} ({filter === 'all' ? clips.length : clips.filter((c) => c.status === filter).length})
            </button>
          ))}
        </div>

        <div className="w-full sm:w-72">
          <input
            type="text"
            placeholder="Filter clips by title or hook…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="field py-1.5 text-xs"
          />
        </div>
      </div>

      {loading && clips.length === 0 ? (
        <div className="py-24 text-center text-ink-500 text-sm">
          Loading library clips…
        </div>
      ) : filtered.length === 0 ? (
        <div className="mt-12 rounded border border-ink-800 bg-ink-850/40 p-12 text-center">
          <p className="font-display text-lg text-ink-300">No clips found</p>
          <p className="mt-2 text-xs text-ink-500">
            {searchQuery || statusFilter !== 'all'
              ? 'Try relaxing your filters or search terms.'
              : 'No clips have been generated yet across your jobs.'}
          </p>
          <Link to="/new" className="btn btn-primary mt-6 inline-block text-xs">
            Create First Job
          </Link>
        </div>
      ) : (
        <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtered.map((clip) => (
            <ClipCard
              key={clip.id}
              clip={clip}
              onPreview={() => setPreviewClip(clip)}
            />
          ))}
        </div>
      )}

      {/* Video Preview Modal */}
      {previewClip && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={() => setPreviewClip(null)}
        >
          <div
            className="relative w-full max-w-lg rounded-lg border border-ink-700 bg-ink-900 p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4 border-b border-ink-800 pb-3">
              <div>
                <h3 className="font-display text-lg text-ink-100">
                  {previewClip.title || 'Untitled Clip'}
                </h3>
                <p className="text-xs text-ink-500">
                  Job: {previewClip.job_id} · Score: {previewClip.score}/100
                </p>
              </div>
              <button
                onClick={() => setPreviewClip(null)}
                className="text-ink-400 hover:text-ink-100 text-lg leading-none"
              >
                ✕
              </button>
            </div>

            <div className="mt-4 flex justify-center bg-ink-950 rounded p-2">
              {previewClip.exports.length > 0 ? (
                <video
                  src={api.streamExportUrl(previewClip.exports[0].id)}
                  controls
                  autoPlay
                  className="max-h-[60vh] rounded object-contain"
                />
              ) : (
                <div className="p-12 text-center text-xs text-ink-500">
                  <p>No rendered video export available for this clip yet.</p>
                  <p className="mt-2 text-ink-400">
                    Export it in the job review console to generate MP4 with active speaker framing & captions.
                  </p>
                </div>
              )}
            </div>

            {/* Campaign Evaluation metadata if present */}
            {previewClip.evaluation && (
              <div className="mt-4 rounded bg-ink-850 p-3 text-xs">
                <div className="flex items-center justify-between text-sodium-400 font-semibold">
                  <span>Campaign Evaluated</span>
                  <span>
                    Score: {previewClip.evaluation.composite_score ?? previewClip.evaluation.final_score}/100
                  </span>
                </div>
                {previewClip.evaluation.matched_hooks && previewClip.evaluation.matched_hooks.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {previewClip.evaluation.matched_hooks.map((h: string, i: number) => (
                      <span
                        key={i}
                        className="rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-emerald-400 border border-emerald-500/20"
                      >
                        ✓ {h}
                      </span>
                    ))}
                  </div>
                )}
                {(previewClip.evaluation.reasoning || previewClip.reason) && (
                  <p className="mt-2 text-ink-400 italic">
                    "{previewClip.evaluation.reasoning || previewClip.reason}"
                  </p>
                )}
              </div>
            )}

            <div className="mt-5 flex items-center justify-between gap-3 border-t border-ink-800 pt-3 text-xs">
              <Link
                to={`/jobs/${previewClip.job_id}/clips`}
                className="text-sodium-500 underline underline-offset-4"
              >
                Open in Job Review →
              </Link>
              {previewClip.exports.length > 0 && (
                <a
                  href={previewClip.exports[0].download_url}
                  download
                  className="btn btn-primary py-1 px-3 text-xs"
                >
                  Download ({previewClip.exports[0].ratio})
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ClipCard({
  clip,
  onPreview,
}: {
  clip: Clip
  onPreview: () => void
}) {
  const hasExport = clip.exports.length > 0
  const exportRec = clip.exports[0]

  return (
    <div className="flex flex-col justify-between rounded-lg border border-ink-800 bg-ink-850/50 p-4 transition hover:border-ink-700 hover:bg-ink-850/80">
      <div>
        <div className="flex items-start justify-between gap-2">
          <span
            className={[
              'numeric font-display text-2xl leading-none',
              clip.score >= 85
                ? 'text-sodium-500'
                : clip.score >= 70
                ? 'text-ink-200'
                : 'text-ink-500',
            ].join(' ')}
          >
            {clip.score}
          </span>
          <span
            className={[
              'rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider',
              clip.status === 'kept'
                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                : clip.status === 'discarded'
                ? 'bg-ink-800 text-ink-500'
                : 'bg-sodium-500/10 text-sodium-400 border border-sodium-500/30',
            ].join(' ')}
          >
            {clip.status}
          </span>
        </div>

        <h3 className="mt-3 line-clamp-2 text-sm font-medium text-ink-100">
          {clip.title || 'Untitled clip'}
        </h3>

        <div className="mt-2 flex items-center gap-3 text-xs text-ink-500">
          <span className="numeric">{formatDuration(clip.duration_s)}</span>
          {hasExport && (
            <span className="text-signal-good font-semibold">
              ● {exportRec.ratio} MP4
            </span>
          )}
        </div>

        {clip.evaluation && (
          <div className="mt-3 border-t border-ink-800/80 pt-2 text-[11px] text-ink-400">
            <span className="text-sodium-400">Campaign Match:</span>{' '}
            {clip.evaluation.composite_score ?? clip.evaluation.final_score}/100
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-ink-800 pt-3 text-xs">
        <button
          type="button"
          onClick={onPreview}
          className="text-sodium-500 hover:underline"
        >
          {hasExport ? '▶ Preview / Stream' : 'Details'}
        </button>

        <Link
          to={`/jobs/${clip.job_id}/clips`}
          className="text-ink-400 hover:text-ink-200"
        >
          Review →
        </Link>
      </div>
    </div>
  )
}
