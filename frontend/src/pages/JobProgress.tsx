import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api, formatBytes, formatDuration } from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { useJobStream } from '../useJobStream'

import { AuthModal } from '../components/AuthModal'

/** Stage order and labels, mirroring autoclip.pipeline.Stage. */
const STAGES = [
  { key: 'prepare', label: 'Prepare & Ingest', note: 'Source acquisition, audio and thumbnails' },
  { key: 'transcribe', label: 'Transcribe', note: 'Word-level timing' },
  { key: 'highlights', label: 'Highlights', note: 'Choosing the moments worth cutting' },
  { key: 'reframe', label: 'Reframe', note: 'Tracking the speaker into vertical' },
  { key: 'captions', label: 'Captions', note: 'Building subtitle timing' },
  { key: 'export', label: 'Export', note: 'Rendering clips' },
] as const

export function JobProgress() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const { job, progress, acquisition, error: streamError } = useJobStream(jobId)
  const [cancelling, setCancelling] = useState(false)
  const [actionError, setActionError] = useState<Error | null>(null)
  const [showAuthModal, setShowAuthModal] = useState(false)
  const [showAcquisitionDetails, setShowAcquisitionDetails] = useState(false)

  useEffect(() => {
    if (job?.status === 'done') {
      // A short hold so the completed state is legible rather than a flash.
      const timer = setTimeout(() => navigate(`/jobs/${job.id}/clips`), 900)
      return () => clearTimeout(timer)
    }
  }, [job?.status, job?.id, navigate])

  if (!job) {
    if (streamError?.status === 401) {
      return (
        <div className="pt-16 max-w-lg">
          <div className="rounded-xl border border-sodium-500/40 bg-ink-850 p-6 shadow-xl">
            <h2 className="font-display text-lg font-bold text-ink-100">Operator Authentication Required</h2>
            <p className="mt-2 text-xs text-ink-400">Access to job status requires an authorized operator token.</p>
            <button onClick={() => setShowAuthModal(true)} className="btn btn-primary mt-4 text-xs">
              Authenticate
            </button>
          </div>
          <AuthModal isOpen={showAuthModal} onClose={() => setShowAuthModal(false)} onSuccess={() => window.location.reload()} />
        </div>
      )
    }
    if (streamError?.status === 404) {
      return (
        <div className="pt-16 max-w-lg">
          <div className="rounded-xl border border-ink-800 bg-ink-850 p-6">
            <h2 className="font-display text-lg font-bold text-ink-100">Job Not Found</h2>
            <p className="mt-2 text-xs text-ink-400">The requested job does not exist on this server.</p>
            <button onClick={() => navigate('/jobs')} className="btn btn-secondary mt-4 text-xs">
              View All Jobs
            </button>
          </div>
        </div>
      )
    }
    return <p className="pt-24 text-sm text-ink-500">Loading…</p>
  }

  const activeIndex = job.current_stage === 'acquiring' ? 0 : STAGES.findIndex((s) => s.key === job.current_stage)
  const percent = Math.round((progress?.overall ?? job.progress) * 100)

  const cancel = async () => {
    setCancelling(true)
    setActionError(null)
    try {
      await api.cancelJob(job.id)
    } catch (err) {
      setActionError(err as Error)
    } finally {
      setCancelling(false)
    }
  }

  const retry = async () => {
    setActionError(null)
    try {
      await api.retryJob(job.id)
      window.location.reload()
    } catch (err) {
      setActionError(err as Error)
    }
  }

  return (
    <div className="pt-14">
      <div className="rise flex flex-wrap items-baseline justify-between gap-6 border-b border-ink-800 pb-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <p className="eyebrow">{job.status}</p>
            {job.attempt && (
              <span className="rounded bg-ink-800 px-2 py-0.5 text-[10px] font-mono text-ink-300">
                Attempt {job.attempt}/{job.max_attempts ?? 3}
              </span>
            )}
            {job.dispatch_mode === 'github' && job.github_run_id && (
              <a
                href={job.github_run_url || `https://github.com/jishanh776600-svg/al-amr-clipping-automation/actions/runs/${job.github_run_id}`}
                target="_blank"
                rel="noreferrer"
                className="rounded bg-sky-950 px-2 py-0.5 text-[10px] font-mono text-sky-400 hover:underline"
              >
                GitHub Run #{job.github_run_id} ↗
              </a>
            )}
          </div>
          <h1 className="mt-2 max-w-3xl truncate font-display text-[clamp(1.75rem,4vw,3rem)] leading-tight text-ink-100">
            {job.source?.title || 'Untitled'}
          </h1>
          {job.source && (
            <p className="numeric mt-2 text-xs text-ink-500">
              {formatDuration(job.source.duration_s)}
              {job.source.width ? ` · ${job.source.width}×${job.source.height}` : ''} ·{' '}
              {job.provider}
            </p>
          )}
        </div>

        <div className="flex items-baseline gap-8">
          <span className="numeric font-display text-[clamp(2.5rem,6vw,4rem)] leading-none text-sodium-500">
            {percent}
            <span className="text-2xl text-ink-600">%</span>
          </span>
          {(job.status === 'running' || job.status === 'queued' || job.status === 'dispatching') && (
            <button onClick={cancel} disabled={cancelling} className="btn btn-ghost">
              {cancelling ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {job.status === 'cancel_requested' && (
            <span className="rounded bg-rose-950/60 px-3 py-1.5 text-xs font-medium text-rose-400">
              Cancellation in progress…
            </span>
          )}
          {(job.status === 'failed' || job.status === 'cancelled') && (
            <button onClick={retry} className="btn btn-primary">
              Retry
            </button>
          )}
          {job.status === 'done' && (
            <button onClick={() => navigate(`/jobs/${job.id}/clips`)} className="btn btn-primary">
              Review clips
            </button>
          )}
        </div>
      </div>

      {job.stale_at && (
        <div className="mt-6 rounded-lg border border-amber-800/40 bg-amber-950/30 p-4 text-xs text-amber-300">
          ⚠️ <strong>Stale Worker Warning:</strong> No heartbeat received from worker recently. Automatic recovery or retry will occur if attempt limit allows.
        </div>
      )}

      {job.error && (
        <div className="mt-8 max-w-3xl">
          <ErrorNote error={new Error(job.error)} />
          <p className="mt-3 text-xs text-ink-500">
            Retrying resumes from the stage that failed — finished stages are not redone.
          </p>
        </div>
      )}

      {actionError && (
        <div className="mt-8 max-w-3xl">
          <ErrorNote error={actionError} onDismiss={() => setActionError(null)} />
        </div>
      )}

      {/* Live Source Acquisition Status & Fallback Telemetry */}
      {(acquisition || job.settings?.acquisition_telemetry || job.current_stage === 'acquiring') && (
        <div className="mt-8 max-w-3xl rounded-xl border border-sodium-500/30 bg-ink-900/90 p-5 shadow-lg">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-800 pb-3">
            <div className="flex items-center gap-2">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  acquisition?.status === 'completed' || (job.status === 'running' && job.current_stage !== 'acquiring' && job.current_stage !== 'prepare')
                    ? 'bg-emerald-400'
                    : acquisition?.status === 'failed' || job.status === 'failed'
                      ? 'bg-rose-500'
                      : 'bg-sodium-400 animate-pulse'
                }`}
              />
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-300">
                Source Media Acquisition
              </span>
              {(acquisition?.provider || job.settings?.acquisition_telemetry?.provider) && (
                <span className="rounded bg-sodium-500/10 px-2 py-0.5 text-[11px] font-mono text-sodium-300 border border-sodium-500/20">
                  {acquisition?.provider || job.settings?.acquisition_telemetry?.provider}
                </span>
              )}
            </div>
            {acquisition?.attempt && acquisition?.totalAttempts && (
              <span className="text-xs font-mono text-ink-400">
                Provider {acquisition.attempt} of {acquisition.totalAttempts}
              </span>
            )}
          </div>

          <div className="mt-3 text-sm text-ink-200 font-medium">
            {acquisition?.message ||
              (job.settings?.acquisition_telemetry
                ? `Acquired via ${job.settings.acquisition_telemetry.provider} in ${job.settings.acquisition_telemetry.acquisition_duration}s`
                : 'Acquiring source media...')}
          </div>

          {/* Download progress bar */}
          {acquisition?.phase === 'DOWNLOADING' && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-xs text-ink-400 mb-1">
                <span>
                  {acquisition.bytesDownloaded ? formatBytes(acquisition.bytesDownloaded) : ''}
                  {acquisition.totalBytes ? ` / ${formatBytes(acquisition.totalBytes)}` : ''}
                  {acquisition.downloadSpeed ? ` · ${(acquisition.downloadSpeed / (1024 * 1024)).toFixed(1)} MB/s` : ''}
                </span>
                <span className="font-mono text-sodium-400">
                  {acquisition.progressPercent !== undefined && acquisition.progressPercent !== null
                    ? `${Math.round(acquisition.progressPercent)}%`
                    : ''}
                  {acquisition.etaSeconds ? ` (ETA ${Math.round(acquisition.etaSeconds)}s)` : ''}
                </span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-ink-800 overflow-hidden">
                <div
                  className="h-full bg-sodium-500 transition-all duration-300 ease-out"
                  style={{ width: `${Math.min(100, Math.max(0, acquisition.progressPercent ?? 0))}%` }}
                />
              </div>
            </div>
          )}

          {/* Expandable Acquisition Details */}
          <div className="mt-4 pt-3 border-t border-ink-800/60">
            <button
              type="button"
              onClick={() => setShowAcquisitionDetails(!showAcquisitionDetails)}
              className="text-xs text-ink-400 hover:text-ink-200 flex items-center gap-1.5 font-mono"
            >
              <span>{showAcquisitionDetails ? '▼ Hide' : '▶ Show'} Acquisition Details & Fallback Telemetry</span>
            </button>

            {showAcquisitionDetails && (
              <div className="mt-3 rounded bg-ink-950/80 p-3 border border-ink-800 text-xs font-mono space-y-2 text-ink-300">
                <div>
                  <span className="text-ink-500">Source URL:</span>{' '}
                  <span className="text-ink-200 break-all">{job.source?.url || '—'}</span>
                </div>
                {(acquisition?.provider || job.settings?.acquisition_telemetry?.provider) && (
                  <div>
                    <span className="text-ink-500">Active Provider:</span>{' '}
                    <span className="text-sodium-300">
                      {acquisition?.provider || job.settings?.acquisition_telemetry?.provider}
                    </span>
                  </div>
                )}
                {acquisition?.instance && (
                  <div>
                    <span className="text-ink-500">Instance:</span>{' '}
                    <span className="text-ink-200">{acquisition.instance}</span>
                  </div>
                )}

                {/* Fallback history */}
                {(() => {
                  const history =
                    acquisition?.telemetry?.fallback_history ||
                    acquisition?.telemetry?.attempts_history ||
                    job.settings?.acquisition_telemetry?.fallback_history ||
                    job.settings?.acquisition_telemetry?.attempts_history ||
                    []
                  if (!history.length) return null
                  return (
                    <div className="mt-2 pt-2 border-t border-ink-900">
                      <div className="text-ink-400 font-semibold mb-1">Provider Fallback Log:</div>
                      <div className="space-y-1">
                        {history.map((att: any, idx: number) => (
                          <div key={idx} className="text-[11px] flex items-start gap-2">
                            <span className="text-rose-400">✕</span>
                            <span className="text-ink-200 font-bold">{att.provider}:</span>
                            <span className="text-ink-400">
                              [{att.code}] {att.message}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })()}
              </div>
            )}
          </div>
        </div>
      )}

      <ol className="mt-12 max-w-3xl">
        {STAGES.map((stage, index) => {
          // A failed job stopped *at* current_stage, so that stage must read as
          // failed rather than as still running — otherwise the checklist claims
          // work is in progress while the banner says it died.
          const failedHere =
            (job.status === 'failed' || job.status === 'cancelled') && index === activeIndex

          const state =
            job.status === 'done' || (activeIndex >= 0 && index < activeIndex)
              ? 'done'
              : failedHere
                ? 'stopped'
                : index === activeIndex && job.status === 'running'
                  ? 'active'
                  : 'pending'

          const fraction =
            state === 'done' ? 1 : state === 'active' ? (progress?.stageProgress ?? 0) : 0

          return (
            <li
              key={stage.key}
              className="rise grid grid-cols-[2.5rem_1fr] items-start gap-x-5 border-b border-ink-850 py-5"
              style={{ animationDelay: `${index * 55}ms` }}
            >
              <span
                className={[
                  'numeric pt-0.5 text-xs',
                  state === 'done'
                    ? 'text-signal-good'
                    : state === 'stopped'
                      ? 'text-signal-bad'
                      : state === 'active'
                        ? 'text-sodium-500'
                        : 'text-ink-600',
                ].join(' ')}
              >
                {state === 'done' ? '✓' : state === 'stopped' ? '✕' : String(index + 1).padStart(2, '0')}
              </span>

              <div>
                <div className="flex flex-wrap items-baseline justify-between gap-x-4">
                  <span
                    className={[
                      'text-[0.9375rem]',
                      state === 'pending' ? 'text-ink-600' : 'text-ink-100',
                    ].join(' ')}
                  >
                    {stage.label}
                  </span>
                  <span
                    className={`text-xs ${state === 'stopped' ? 'text-signal-bad' : 'text-ink-500'}`}
                  >
                    {state === 'stopped'
                      ? job.status === 'cancelled'
                        ? 'cancelled here'
                        : 'failed here'
                      : state === 'active'
                        ? (progress?.message ?? stage.note)
                        : stage.note}
                  </span>
                </div>

                {/* A hairline that fills. No track chrome — the rule below the
                    row already provides the container. */}
                <div className="mt-3 h-px w-full bg-ink-800">
                  <div
                    className="h-px origin-left bg-sodium-500 transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]"
                    style={{ transform: `scaleX(${fraction})` }}
                  />
                </div>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
