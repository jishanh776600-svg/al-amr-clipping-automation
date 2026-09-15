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
  { key: 'retention', label: 'Retention & Pacing', note: 'Optimizing pacing, dead air & final quality' },
  { key: 'captions', label: 'Captions', note: 'Building subtitle timing' },
  { key: 'audio_mix', label: 'Audio & BGM Mix', note: 'Ducking, looping & loudness normalization' },
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
  const [elapsed, setElapsed] = useState<string>('')

  useEffect(() => {
    if (!job?.started_at) return
    const updateElapsed = () => {
      const start = new Date(job.started_at!).getTime()
      const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now()
      const diffSec = Math.max(0, Math.floor((end - start) / 1000))
      const mins = Math.floor(diffSec / 60)
      const secs = diffSec % 60
      setElapsed(`${mins}m ${secs.toString().padStart(2, '0')}s`)
    }
    updateElapsed()
    if (['done', 'failed', 'cancelled'].includes(job.status)) return
    const interval = setInterval(updateElapsed, 1000)
    return () => clearInterval(interval)
  }, [job?.started_at, job?.finished_at, job?.status])

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
            {elapsed && (
              <span className="rounded bg-ink-800 px-2 py-0.5 text-[10px] font-mono text-ink-300">
                ⏱ {elapsed}
              </span>
            )}
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

          {/* Structured Acquisition Progress Checklist */}
          <AcquisitionChecklist acquisition={acquisition} job={job} />

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

      {job.settings?.candidate_telemetry && (
        <div className="mt-6 rounded-xl border border-sodium-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-sodium-400">
              Autonomous Candidate Discovery (Step 15)
            </h3>
            <span className="text-[11px] font-mono text-ink-400">
              Elapsed: {job.settings.candidate_telemetry.elapsed_s}s
            </span>
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-ink-100">
                {job.settings.candidate_telemetry.discovered_count}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Discovered</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-sky-400">
                {job.settings.candidate_telemetry.scored_count}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Scored</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-emerald-500/30">
              <div className="text-lg font-bold font-mono text-emerald-400">
                {job.settings.candidate_telemetry.selected_count}
              </div>
              <div className="text-[11px] text-emerald-500/80 uppercase tracking-wider font-semibold">Selected Top-N</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-rose-500/30">
              <div className="text-lg font-bold font-mono text-rose-400">
                {job.settings.candidate_telemetry.rejected_count}
              </div>
              <div className="text-[11px] text-rose-400/80 uppercase tracking-wider">Rejected</div>
            </div>
          </div>
        </div>
      )}

      {job.settings?.assembly_telemetry && (
        <div className="mt-6 rounded-xl border border-sky-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-sky-400">
              Smart Clip Assembly & Quality Gate (Step 16)
            </h3>
            <span className="text-[11px] font-mono text-ink-400">
              Elapsed: {job.settings.assembly_telemetry.elapsed_s}s
            </span>
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-5 gap-3 text-center">
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-ink-100">
                {job.settings.assembly_telemetry.candidates_received}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Received</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-sky-400">
                {job.settings.assembly_telemetry.clips_optimized}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Optimized</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-emerald-500/30">
              <div className="text-lg font-bold font-mono text-emerald-400">
                {job.settings.assembly_telemetry.clips_passed}
              </div>
              <div className="text-[11px] text-emerald-500/80 uppercase tracking-wider font-semibold">Passed</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-amber-500/30">
              <div className="text-lg font-bold font-mono text-amber-400">
                {job.settings.assembly_telemetry.clips_warned}
              </div>
              <div className="text-[11px] text-amber-400/80 uppercase tracking-wider font-semibold">Warned</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-rose-500/30">
              <div className="text-lg font-bold font-mono text-rose-400">
                {job.settings.assembly_telemetry.clips_rejected}
              </div>
              <div className="text-[11px] text-rose-400/80 uppercase tracking-wider">Rejected</div>
            </div>
          </div>
        </div>
      )}

      {job.settings?.visual_composition_telemetry && (
        <div className="mt-6 rounded-xl border border-purple-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-purple-400">
              9:16 Smart Reframing & Visual Composition (Step 17)
            </h3>
            <span className="text-[11px] font-mono text-ink-400">
              Quality Gate: {job.settings.visual_composition_telemetry.approved}/{job.settings.visual_composition_telemetry.total_evaluated} Approved
            </span>
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-ink-100">
                {job.settings.visual_composition_telemetry.total_evaluated}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Evaluated</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-emerald-500/30">
              <div className="text-lg font-bold font-mono text-emerald-400">
                {job.settings.visual_composition_telemetry.approved}
              </div>
              <div className="text-[11px] text-emerald-500/80 uppercase tracking-wider font-semibold">Approved</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-rose-500/30">
              <div className="text-lg font-bold font-mono text-rose-400">
                {job.settings.visual_composition_telemetry.rejected}
              </div>
              <div className="text-[11px] text-rose-400/80 uppercase tracking-wider">Rejected</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-purple-500/30">
              <div className="text-lg font-bold font-mono text-purple-400">
                {job.settings.visual_composition_telemetry.compositions?.[0]?.output_width || 1080}×{job.settings.visual_composition_telemetry.compositions?.[0]?.output_height || 1920}
              </div>
              <div className="text-[11px] text-purple-400/80 uppercase tracking-wider">Target 9:16</div>
            </div>
          </div>
        </div>
      )}

      {job.settings?.retention_telemetry && (
        <div className="mt-6 rounded-xl border border-amber-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-amber-400">
              AI-Assisted Retention Editing & Quality Gate (Step 18)
            </h3>
            <span className="text-[11px] font-mono text-ink-400">
              Avg Retention Score: {Math.round(job.settings.retention_telemetry.avg_retention_score ?? 0)}/100
            </span>
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-5 gap-3 text-center">
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-ink-100">
                {job.settings.retention_telemetry.clips_analyzed ?? 0}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Analyzed</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-amber-400">
                {job.settings.retention_telemetry.clips_optimized ?? 0}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Optimized</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-emerald-500/30">
              <div className="text-lg font-bold font-mono text-emerald-400">
                {job.settings.retention_telemetry.clips_passed ?? 0}
              </div>
              <div className="text-[11px] text-emerald-500/80 uppercase tracking-wider font-semibold">Passed</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-amber-500/30">
              <div className="text-lg font-bold font-mono text-amber-400">
                {job.settings.retention_telemetry.clips_warned ?? 0}
              </div>
              <div className="text-[11px] text-amber-400/80 uppercase tracking-wider font-semibold">Warned</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-rose-500/30">
              <div className="text-lg font-bold font-mono text-rose-400">
                {job.settings.retention_telemetry.clips_rejected ?? 0}
              </div>
              <div className="text-[11px] text-rose-400/80 uppercase tracking-wider">Rejected</div>
            </div>
          </div>
          {job.settings.retention_telemetry.optimizations && job.settings.retention_telemetry.optimizations.length > 0 && (
            <div className="mt-4 pt-3 border-t border-ink-800/60">
              <div className="text-xs font-mono text-ink-400 mb-2 font-semibold">Retention & Pacing Breakdown:</div>
              <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                {job.settings.retention_telemetry.optimizations.map((opt: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between text-[11px] font-mono bg-ink-950/60 p-2 rounded border border-ink-800">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${opt.quality_status === 'FINAL_PASS' ? 'bg-emerald-400' : opt.quality_status === 'FINAL_WARN' ? 'bg-amber-400' : 'bg-rose-400'}`} />
                      <span className="text-ink-200">Clip #{idx + 1}</span>
                      <span className="text-ink-500">({opt.quality_status})</span>
                    </div>
                    <div className="flex items-center gap-3 text-ink-400">
                      <span>Pacing: <strong className="text-ink-200">{Math.round(opt.pacing_score ?? 0)}</strong></span>
                      <span>Density: <strong className="text-ink-200">{(opt.speech_density_wps ?? 0).toFixed(1)} wps</strong></span>
                      <span>Retention: <strong className="text-amber-400">{Math.round(opt.retention_score ?? 0)}</strong></span>
                      <span>Final: <strong className="text-emerald-400">{Math.round(opt.final_score ?? 0)}</strong></span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {job.settings?.caption_telemetry && (
        <div className="mt-6 rounded-xl border border-teal-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-teal-400">
                Operator Caption Styling & Quality Gate (Step 19)
              </h3>
              <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-teal-500/20 text-teal-300 border border-teal-500/30 font-semibold">
                {job.settings.caption_telemetry.style_label || job.settings.caption_telemetry.selected_style}
              </span>
            </div>
            <span className="text-[11px] font-mono text-ink-400">
              Quality Gate: {job.settings.caption_telemetry.approved}/{job.settings.caption_telemetry.total_evaluated} Approved
            </span>
          </div>
          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
            <div className="rounded-lg bg-ink-850 p-3 border border-ink-800">
              <div className="text-lg font-bold font-mono text-ink-100">
                {job.settings.caption_telemetry.total_evaluated ?? 0}
              </div>
              <div className="text-[11px] text-ink-400 uppercase tracking-wider">Clips Processed</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-teal-500/30">
              <div className="text-lg font-bold font-mono text-teal-400">
                {job.settings.caption_telemetry.selected_style === 'rich_dynamic' ? 'Rich Dynamic' : 'Classic Pro'}
              </div>
              <div className="text-[11px] text-teal-400/80 uppercase tracking-wider font-semibold">Style Selected</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-emerald-500/30">
              <div className="text-lg font-bold font-mono text-emerald-400">
                {job.settings.caption_telemetry.approved ?? 0}
              </div>
              <div className="text-[11px] text-emerald-500/80 uppercase tracking-wider font-semibold">Passed</div>
            </div>
            <div className="rounded-lg bg-ink-850 p-3 border border-rose-500/30">
              <div className="text-lg font-bold font-mono text-rose-400">
                {job.settings.caption_telemetry.rejected ?? 0}
              </div>
              <div className="text-[11px] text-rose-400/80 uppercase tracking-wider">Rejected</div>
            </div>
          </div>
          {job.settings.caption_telemetry.records && job.settings.caption_telemetry.records.length > 0 && (
            <div className="mt-4 pt-3 border-t border-ink-800/60">
              <div className="text-xs font-mono text-ink-400 mb-2 font-semibold">Caption & Visual Safety Breakdown:</div>
              <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
                {job.settings.caption_telemetry.records.map((rec: any, idx: number) => (
                  <div key={idx} className="flex items-center justify-between text-[11px] font-mono bg-ink-950/60 p-2 rounded border border-ink-800">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${rec.quality_status === 'CAPTION_PASS' ? 'bg-emerald-400' : rec.quality_status === 'CAPTION_WARN' ? 'bg-amber-400' : 'bg-rose-400'}`} />
                      <span className="text-ink-200">Clip #{idx + 1}</span>
                      <span className="text-ink-500">({rec.quality_status})</span>
                    </div>
                    <div className="flex items-center gap-3 text-ink-400">
                      <span>Style: <strong className="text-ink-200">{rec.style_label}</strong></span>
                      <span>Score: <strong className="text-teal-400">{Math.round(rec.quality_score ?? 0)}/100</strong></span>
                      <span>Segments: <strong className="text-ink-200">{rec.caption_segments?.length || 0}</strong></span>
                      {rec.fallback_used && (
                        <span className="text-amber-400">[Fallback Used]</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {job.settings?.bgm_telemetry && (
        <div className="mt-6 rounded-xl border border-blue-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-blue-400">
                Campaign Background Music (Step 20)
              </h3>
              <span
                className={`text-[10px] uppercase font-mono px-2 py-0.5 rounded border font-semibold ${
                  job.settings.bgm_telemetry.enabled
                    ? 'bg-blue-500/20 text-blue-300 border-blue-500/30'
                    : 'bg-ink-800 text-ink-400 border-ink-700'
                }`}
              >
                {job.settings.bgm_telemetry.enabled ? '✓ BGM Selected' : 'No BGM'}
              </span>
            </div>
            <span className="text-[11px] font-mono text-ink-400">
              {job.settings.bgm_telemetry.enabled
                ? job.settings.bgm_telemetry.asset_name
                : 'Original Voice Only'}
            </span>
          </div>
          {job.settings.bgm_telemetry.enabled && (
            <div className="mt-3 text-xs text-ink-400 flex flex-wrap items-center gap-4 bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div>
                <span className="text-ink-500">Track:</span>{' '}
                <strong className="text-ink-200">{job.settings.bgm_telemetry.asset_name}</strong>
              </div>
              <div>
                <span className="text-ink-500">Asset ID:</span>{' '}
                <span className="font-mono text-ink-300">{job.settings.bgm_telemetry.asset_id}</span>
              </div>
              <div className="text-[11px] text-blue-400/90 ml-auto">
                Step 20: Persistent selection active · Mixing ready for Step 21
              </div>
            </div>
          )}
        </div>
      )}

      {job.settings?.bgm_mix_telemetry && (
        <div className="mt-6 rounded-xl border border-teal-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-teal-400">
                Audio Mixing, Ducking & Normalization (Step 21)
              </h3>
              <span
                className={`text-[10px] uppercase font-mono px-2 py-0.5 rounded border font-semibold ${
                  job.settings.bgm_mix_telemetry.bgm_enabled
                    ? 'bg-teal-500/20 text-teal-300 border-teal-500/30'
                    : 'bg-ink-800 text-ink-400 border-ink-700'
                }`}
              >
                {job.settings.bgm_mix_telemetry.bgm_enabled ? '✓ Mixed & Ducked' : 'Original Audio'}
              </span>
            </div>
            <span className="text-[11px] font-mono text-ink-400">
              Approved: {job.settings.bgm_mix_telemetry.approved_clips ?? 0}/{job.settings.bgm_mix_telemetry.total_clips ?? 0}
            </span>
          </div>

          <div className="mt-4 grid grid-cols-4 gap-2 text-center">
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Clips Mixed</div>
              <div className="text-lg font-bold text-ink-100 mt-0.5">
                {job.settings.bgm_mix_telemetry.total_clips ?? 0}
              </div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Sidechain Ducking</div>
              <div className="text-lg font-bold text-teal-300 mt-0.5">
                {job.settings.bgm_mix_telemetry.bgm_enabled ? '16 dB' : 'None'}
              </div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Target Loudness</div>
              <div className="text-lg font-bold text-ink-100 mt-0.5">-14.0 LUFS</div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">True Peak Limit</div>
              <div className="text-lg font-bold text-ink-100 mt-0.5">-1.5 dBTP</div>
            </div>
          </div>

          {job.settings.bgm_mix_telemetry.records && job.settings.bgm_mix_telemetry.records.length > 0 && (
            <div className="mt-4 border-t border-ink-800 pt-3">
              <div className="text-xs font-mono text-ink-400 mb-2 font-semibold">Clip Audio Mix Metrics:</div>
              <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                {job.settings.bgm_mix_telemetry.records.map((rec: any, idx: number) => (
                  <div key={rec.id || idx} className="text-xs font-mono bg-ink-950/40 p-2 rounded flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${rec.quality_status === 'MIX_PASS' ? 'bg-emerald-400' : rec.quality_status === 'MIX_WARN' ? 'bg-amber-400' : 'bg-rose-400'}`} />
                      <span className="text-ink-200">Clip #{idx + 1}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-ink-800 text-ink-400 uppercase">{rec.loop_trim_decision}</span>
                    </div>
                    <div className="flex items-center gap-3 text-ink-400 text-[11px]">
                      <span>LUFS: <strong className="text-ink-200">{rec.integrated_lufs}</strong></span>
                      <span>Peak: <strong className="text-ink-200">{rec.true_peak_db} dB</strong></span>
                      <span>Score: <strong className="text-teal-400">{Math.round(rec.quality_score ?? 0)}</strong></span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${rec.quality_status === 'MIX_PASS' ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : rec.quality_status === 'MIX_WARN' ? 'bg-amber-950 text-amber-300 border border-amber-800' : 'bg-rose-950 text-rose-300 border border-rose-800'}`}>
                        {rec.quality_status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {job.settings?.render_telemetry && (
        <div className="mt-6 rounded-xl border border-emerald-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-emerald-400">
                Final Render & Quality Gate (Step 22)
              </h3>
              <span
                className={`text-[10px] uppercase font-mono px-2 py-0.5 rounded border font-semibold ${
                  job.settings.render_telemetry.approved > 0
                    ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                    : 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                }`}
              >
                {job.settings.render_telemetry.approved}/{job.settings.render_telemetry.total_clips} Approved
              </span>
            </div>
            <span className="text-[11px] font-mono text-ink-400">
              Avg Quality: <strong className="text-emerald-300">{job.settings.render_telemetry.avg_quality_score ?? 0}</strong>/100
            </span>
          </div>

          <div className="mt-4 grid grid-cols-4 gap-2 text-center">
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Clips Rendered</div>
              <div className="text-lg font-bold text-ink-100 mt-0.5">
                {job.settings.render_telemetry.rendered ?? 0}
              </div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Resolution</div>
              <div className="text-lg font-bold text-emerald-300 mt-0.5">1080x1920</div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Container / Codec</div>
              <div className="text-lg font-bold text-ink-100 mt-0.5">MP4 · H.264</div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Publishing Gate</div>
              <div className="text-lg font-bold text-emerald-400 mt-0.5">
                {job.settings.render_telemetry.approved} Passed
              </div>
            </div>
          </div>

          {job.settings.render_telemetry.records && job.settings.render_telemetry.records.length > 0 && (
            <div className="mt-4 border-t border-ink-800 pt-3">
              <div className="text-xs font-mono text-ink-400 mb-2 font-semibold">Clip Render Packages & Quality Checks:</div>
              <div className="space-y-1.5 max-h-36 overflow-y-auto pr-1">
                {job.settings.render_telemetry.records.map((rec: any, idx: number) => (
                  <div key={rec.id || idx} className="text-xs font-mono bg-ink-950/40 p-2 rounded flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full ${rec.quality_status === 'RENDER_PASS' ? 'bg-emerald-400' : rec.quality_status === 'RENDER_WARN' ? 'bg-amber-400' : 'bg-rose-400'}`} />
                      <span className="text-ink-200">Clip #{idx + 1}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-ink-800 text-ink-400 uppercase">{rec.caption_style}</span>
                    </div>
                    <div className="flex items-center gap-3 text-ink-400 text-[11px]">
                      <span>Dur: <strong className="text-ink-200">{rec.duration}s</strong></span>
                      <span>Score: <strong className="text-emerald-400">{Math.round(rec.quality_score ?? 0)}</strong></span>
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${rec.quality_status === 'RENDER_PASS' ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : rec.quality_status === 'RENDER_WARN' ? 'bg-amber-950 text-amber-300 border border-amber-800' : 'bg-rose-950 text-rose-300 border border-rose-800'}`}>
                        {rec.quality_status}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {job.settings?.seo_telemetry && (
        <div className="mt-6 rounded-xl border border-teal-500/30 bg-ink-900/90 p-5 shadow-lg max-w-3xl">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-mono font-bold tracking-wider uppercase text-teal-400">
                SEO & Metadata Engine (Step 23)
              </h3>
              <span
                className={`text-[10px] uppercase font-mono px-2 py-0.5 rounded border font-semibold ${
                  job.settings.seo_telemetry.reject_count === 0
                    ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30'
                    : 'bg-rose-500/20 text-rose-300 border-rose-500/30'
                }`}
              >
                {job.settings.seo_telemetry.pass_count + job.settings.seo_telemetry.warn_count}/{job.settings.seo_telemetry.total_metadata} Publish Ready
              </span>
            </div>
            <span className="text-[11px] font-mono text-ink-400">
              Avg Compliance: <strong className="text-teal-300">{job.settings.seo_telemetry.avg_compliance_score ?? 0}</strong>/100
            </span>
          </div>

          <div className="mt-4 grid grid-cols-4 gap-2 text-center">
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Clips With Metadata</div>
              <div className="text-lg font-bold text-ink-100 mt-0.5">
                {job.settings.seo_telemetry.total_metadata ?? 0}
              </div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">SEO Pass</div>
              <div className="text-lg font-bold text-emerald-400 mt-0.5">
                {job.settings.seo_telemetry.pass_count ?? 0}
              </div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">SEO Warn</div>
              <div className="text-lg font-bold text-amber-400 mt-0.5">
                {job.settings.seo_telemetry.warn_count ?? 0}
              </div>
            </div>
            <div className="bg-ink-950/60 p-2.5 rounded border border-ink-800">
              <div className="text-[10px] font-mono text-ink-500 uppercase">Publish Blocked</div>
              <div className={`text-lg font-bold mt-0.5 ${job.settings.seo_telemetry.reject_count > 0 ? 'text-rose-400' : 'text-ink-400'}`}>
                {job.settings.seo_telemetry.reject_count ?? 0}
              </div>
            </div>
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
                    className={`text-xs ${
                      state === 'stopped'
                        ? 'text-signal-bad'
                        : state === 'active'
                          ? 'text-sodium-400 font-medium'
                          : 'text-ink-500'
                    }`}
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

function AcquisitionChecklist({
  acquisition,
  job,
}: {
  acquisition: any
  job: any
}) {
  const isDone =
    acquisition?.phase === 'SOURCE_ACQUIRED' ||
    acquisition?.status === 'completed' ||
    (job.status === 'running' && job.current_stage !== 'acquiring' && job.current_stage !== 'prepare') ||
    job.status === 'done'
  const isFailed =
    acquisition?.status === 'failed' ||
    (job.status === 'failed' && (job.current_stage === 'acquiring' || !job.current_stage))
  const phase = acquisition?.phase || ''

  // Step 1: Initializing WARP
  let warpStep: 'done' | 'active' | 'failed' | 'pending' = 'pending'
  if (isDone) warpStep = 'done'
  else if (phase === 'WARP_INITIALIZING') warpStep = 'active'
  else if (
    ['TESTING_PROXY', 'PROXY_TESTED', 'YTDLP_STARTING', 'DOWNLOADING', 'VALIDATING_MEDIA', 'MEDIA_VALIDATED'].includes(phase)
  )
    warpStep = 'done'
  else if (phase === 'PROXY_FAILED' || isFailed) warpStep = 'failed'

  // Step 2: Testing proxy
  let proxyStep: 'done' | 'active' | 'failed' | 'pending' = 'pending'
  if (isDone) proxyStep = 'done'
  else if (phase === 'TESTING_PROXY') proxyStep = 'active'
  else if (
    ['PROXY_TESTED', 'YTDLP_STARTING', 'DOWNLOADING', 'VALIDATING_MEDIA', 'MEDIA_VALIDATED'].includes(phase)
  )
    proxyStep = 'done'
  else if (phase === 'PROXY_FAILED') proxyStep = 'failed'
  else if (isFailed && warpStep !== 'failed') proxyStep = 'failed'

  // Step 3: yt-dlp via WARP
  let ytdlpStep: 'done' | 'active' | 'failed' | 'pending' = 'pending'
  if (isDone) ytdlpStep = 'done'
  else if (['PROXY_TESTED', 'YTDLP_STARTING', 'TRYING_PROVIDER'].includes(phase)) ytdlpStep = 'active'
  else if (['DOWNLOADING', 'VALIDATING_MEDIA', 'MEDIA_VALIDATED'].includes(phase)) ytdlpStep = 'done'
  else if (isFailed && proxyStep === 'done') ytdlpStep = 'failed'

  // Step 4: Download
  let dlStep: 'done' | 'active' | 'failed' | 'pending' = 'pending'
  let dlLabel = 'Download'
  if (isDone) {
    dlStep = 'done'
  } else if (phase === 'DOWNLOADING') {
    dlStep = 'active'
    if (acquisition?.progressPercent !== undefined && acquisition?.progressPercent !== null) {
      dlLabel = `Download (${Math.round(acquisition.progressPercent)}%)`
    }
  } else if (['VALIDATING_MEDIA', 'MEDIA_VALIDATED'].includes(phase)) {
    dlStep = 'done'
  } else if (isFailed && ytdlpStep === 'done') {
    dlStep = 'failed'
  }

  // Step 5: Media validation
  let valStep: 'done' | 'active' | 'failed' | 'pending' = 'pending'
  if (isDone) valStep = 'done'
  else if (phase === 'VALIDATING_MEDIA') valStep = 'active'
  else if (phase === 'MEDIA_VALIDATED') valStep = 'done'
  else if (isFailed && dlStep === 'done') valStep = 'failed'

  // Step 6: Source acquired
  let acqStep: 'done' | 'active' | 'failed' | 'pending' = 'pending'
  if (isDone) acqStep = 'done'
  else if (phase === 'MEDIA_VALIDATED') acqStep = 'active'
  else if (isFailed && valStep === 'done') acqStep = 'failed'

  const items = [
    { name: 'Initializing WARP', status: warpStep, branch: '├─' },
    { name: 'Testing proxy', status: proxyStep, branch: '├─' },
    { name: 'yt-dlp via WARP', status: ytdlpStep, branch: '├─' },
    {
      name: dlLabel,
      status: dlStep,
      branch: '├─',
      badge:
        dlStep === 'active' && acquisition?.progressPercent != null
          ? `${Math.round(acquisition.progressPercent)}%`
          : undefined,
    },
    { name: 'Media validation', status: valStep, branch: '├─' },
    { name: 'Source acquired', status: acqStep, branch: '└─' },
  ]

  return (
    <div className="mt-4 font-mono text-xs border border-ink-800/80 rounded-lg p-3.5 bg-ink-950/60">
      <div className="text-sodium-400 font-bold tracking-wider mb-2.5 flex items-center justify-between">
        <span>ACQUIRE</span>
        {acquisition?.telemetry?.warp_status && (
          <span className="text-[10px] text-emerald-400 font-normal">
            WARP={acquisition.telemetry.warp_status} ({acquisition.telemetry.location || 'cloudflare'})
          </span>
        )}
      </div>
      <div className="space-y-1.5">
        {items.map((item, i) => (
          <div key={i} className="flex items-center justify-between text-ink-300">
            <div className="flex items-center gap-2">
              <span className="text-ink-600 select-none">{item.branch}</span>
              <span
                className={
                  item.status === 'active'
                    ? 'text-sodium-300 font-semibold'
                    : item.status === 'done'
                      ? 'text-ink-200'
                      : item.status === 'failed'
                        ? 'text-rose-400 font-semibold'
                        : 'text-ink-500'
                }
              >
                {item.name}
              </span>
            </div>
            <div>
              {item.status === 'done' && (
                <span className="text-emerald-400 font-semibold px-2 py-0.5 rounded bg-emerald-950/40 border border-emerald-800/30 text-[10px]">
                  DONE
                </span>
              )}
              {item.status === 'active' && (
                <span className="text-sodium-400 font-semibold px-2 py-0.5 rounded bg-sodium-950/40 border border-sodium-800/40 animate-pulse text-[10px]">
                  {item.badge ? item.badge : 'RUNNING'}
                </span>
              )}
              {item.status === 'failed' && (
                <span className="text-rose-400 font-semibold px-2 py-0.5 rounded bg-rose-950/40 border border-rose-800/30 text-[10px]">
                  FAILED
                </span>
              )}
              {item.status === 'pending' && (
                <span className="text-ink-600 font-normal px-2 py-0.5 rounded text-[10px]">
                  PENDING
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

