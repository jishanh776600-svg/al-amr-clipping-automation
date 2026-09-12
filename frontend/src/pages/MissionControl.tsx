import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Job, type Clip, formatDuration, onAuthChange } from '../api'
import { AuthModal } from '../components/AuthModal'

export function MissionControl() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [clips, setClips] = useState<Clip[]>([])
  const [readiness, setReadiness] = useState<{ status: string; ready: boolean; checks: Record<string, any> } | null>(null)
  const [loading, setLoading] = useState(true)
  const [isUnauthorized, setIsUnauthorized] = useState(false)
  const [showAuthModal, setShowAuthModal] = useState(false)

  const refresh = () => {
    Promise.all([
      api.listJobs(20).catch((err) => {
        if (err?.status === 401) setIsUnauthorized(true)
        return []
      }),
      api.listAllClips(8).catch(() => []),
      api.ready().catch(() => null),
    ]).then(([jList, cList, rStatus]) => {
      setJobs(jList)
      setClips(cList)
      setReadiness(rStatus)
      if (jList.length > 0) setIsUnauthorized(false)
      setLoading(false)
    })
  }

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, 5000)
    const unsub = onAuthChange(() => refresh())
    return () => {
      clearInterval(interval)
      unsub()
    }
  }, [])

  const runningJob = jobs.find((j) => j.status === 'running')
  const queuedCount = jobs.filter((j) => j.status === 'queued').length
  const completedCount = jobs.filter((j) => j.status === 'done').length
  const failedCount = jobs.filter((j) => j.status === 'failed').length

  return (
    <div className="space-y-8 pt-8">
      {/* Top Header */}
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="font-display text-3xl font-bold tracking-tight text-ink-100">
              Mission Control
            </h1>
            <span className="inline-flex items-center rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-400 border border-emerald-500/20">
              LIVE CONSOLE
            </span>
          </div>
          <p className="mt-1 text-sm text-ink-400">
            Autonomous server-side clipping engine and real-time operations overview.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Link
            to="/new"
            className="inline-flex items-center gap-2 rounded-lg bg-sodium-500 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-sodium-400"
          >
            <span>+ Create New Job</span>
          </Link>
          <button
            onClick={refresh}
            className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-300 hover:bg-ink-700"
            title="Refresh status"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {isUnauthorized && (
        <div className="rounded-xl border border-sodium-500/40 bg-ink-850 p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 shadow-lg">
          <div className="flex items-center gap-3">
            <span className="text-xl">🔒</span>
            <div>
              <p className="text-sm font-semibold text-ink-100">Operator Authentication Required</p>
              <p className="mt-0.5 text-xs text-ink-400">Remote control plane endpoints are locked. Enter your token to view active jobs and telemetry.</p>
            </div>
          </div>
          <button
            onClick={() => setShowAuthModal(true)}
            className="btn btn-primary shrink-0 text-xs"
          >
            Authenticate
          </button>
        </div>
      )}

      {/* Metrics Row */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 lg:gap-6">
        <MetricCard
          label="Active Job"
          value={runningJob ? '1 Processing' : 'Idle'}
          subtext={runningJob ? `Stage: ${runningJob.current_stage}` : 'Ready for jobs'}
          status={runningJob ? 'active' : 'idle'}
        />
        <MetricCard
          label="Queued"
          value={queuedCount.toString()}
          subtext="Pending FIFO execution"
          status={queuedCount > 0 ? 'warning' : 'idle'}
        />
        <MetricCard
          label="Completed"
          value={completedCount.toString()}
          subtext={failedCount > 0 ? `${failedCount} failed` : 'Rendered & validated'}
          status="good"
        />
        <MetricCard
          label="System Health"
          value={readiness?.ready ? 'OPERATIONAL' : 'DEGRADED'}
          subtext={readiness ? `DB, Storage & FFmpeg: OK` : 'Checking...'}
          status={readiness?.ready ? 'good' : 'bad'}
        />
      </div>

      {/* Active Processing Card (if any) */}
      {runningJob && (
        <div className="rounded-xl border border-sodium-500/30 bg-ink-850 p-6 shadow-lg shadow-black/40">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-500">
                In-Progress Remote Processing
              </span>
              <h2 className="text-lg font-bold text-ink-100">
                {runningJob.source?.title || `Job ${runningJob.id.slice(0, 8)}`}
              </h2>
              <p className="text-xs text-ink-400">
                Stage: <span className="font-mono text-ink-200">{runningJob.current_stage}</span> ·
                Provider: <span className="text-ink-200">{runningJob.provider}</span> ·
                ID: <span className="font-mono text-ink-300">{runningJob.id}</span>
              </p>
            </div>
            <Link
              to={`/jobs/${runningJob.id}`}
              className="inline-flex items-center justify-center rounded-lg bg-ink-750 px-4 py-2 text-xs font-semibold text-ink-100 hover:bg-ink-700"
            >
              Monitor Stream →
            </Link>
          </div>

          <div className="mt-4">
            <div className="flex justify-between text-xs text-ink-400 mb-1">
              <span>Overall Progress</span>
              <span className="font-mono font-medium text-ink-200">
                {Math.round(runningJob.progress * 100)}%
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-ink-700">
              <div
                className="h-full bg-sodium-500 transition-all duration-300"
                style={{ width: `${Math.max(5, runningJob.progress * 100)}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {/* Two Column Layout: Recent Jobs & Recent Clips */}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
        {/* Recent Jobs Table */}
        <div className="rounded-xl border border-ink-800 bg-ink-850 p-6">
          <div className="flex items-center justify-between pb-4 border-b border-ink-800">
            <h3 className="font-display text-lg font-bold text-ink-100">Recent Production Jobs</h3>
            <Link to="/jobs" className="text-xs text-sodium-500 hover:underline">
              View all ({jobs.length}) →
            </Link>
          </div>

          <div className="divide-y divide-ink-800/60">
            {jobs.length === 0 && !loading && (
              <p className="py-8 text-center text-sm text-ink-500">No jobs submitted yet.</p>
            )}
            {jobs.slice(0, 5).map((job) => (
              <div key={job.id} className="flex items-center justify-between py-3.5">
                <div className="min-w-0 pr-4">
                  <Link
                    to={`/jobs/${job.id}`}
                    className="truncate block text-sm font-medium text-ink-200 hover:text-sodium-500"
                  >
                    {job.source?.title || `Job ${job.id.slice(0, 8)}`}
                  </Link>
                  <div className="flex items-center gap-2 text-xs text-ink-500 mt-0.5">
                    <span className="font-mono">{job.id.slice(0, 8)}</span>
                    <span>·</span>
                    <span>{new Date(job.created_at).toLocaleTimeString()}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <StatusBadge status={job.status} />
                  {job.status === 'done' && (
                    <Link
                      to={`/jobs/${job.id}/clips`}
                      className="text-xs text-ink-400 hover:text-ink-200"
                    >
                      Clips →
                    </Link>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Recent Generated Clips */}
        <div className="rounded-xl border border-ink-800 bg-ink-850 p-6">
          <div className="flex items-center justify-between pb-4 border-b border-ink-800">
            <h3 className="font-display text-lg font-bold text-ink-100">Recently Generated Clips</h3>
            <Link to="/clips" className="text-xs text-sodium-500 hover:underline">
              Clip Gallery →
            </Link>
          </div>

          <div className="divide-y divide-ink-800/60">
            {clips.length === 0 && !loading && (
              <p className="py-8 text-center text-sm text-ink-500">No clips exported yet.</p>
            )}
            {clips.slice(0, 5).map((clip) => {
              const exportRec = clip.exports?.[0]
              return (
                <div key={clip.id} className="flex items-center justify-between py-3.5">
                  <div className="min-w-0 pr-4">
                    <span className="truncate block text-sm font-medium text-ink-200">
                      {clip.title || 'Untitled Clip'}
                    </span>
                    <div className="flex items-center gap-2 text-xs text-ink-500 mt-0.5">
                      <span>{formatDuration(clip.duration_s)}</span>
                      <span>·</span>
                      <span>Ratio: {clip.ratio}</span>
                      {clip.evaluation && (
                        <>
                          <span>·</span>
                          <span className="text-emerald-400 font-semibold">
                            Score: {clip.evaluation.final_score.toFixed(1)}/10
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {exportRec && (
                      <a
                        href={api.streamExportUrl(exportRec.id)}
                        target="_blank"
                        rel="noreferrer"
                        className="rounded bg-ink-750 px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-700"
                      >
                        Preview
                      </a>
                    )}
                    <Link
                      to={`/jobs/${clip.job_id}/clips`}
                      className="rounded bg-ink-750 px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-700"
                    >
                      Review
                    </Link>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <AuthModal
        isOpen={showAuthModal}
        onClose={() => setShowAuthModal(false)}
        onSuccess={() => refresh()}
      />
    </div>
  )
}

function MetricCard({
  label,
  value,
  subtext,
  status,
}: {
  label: string
  value: string
  subtext: string
  status: 'good' | 'bad' | 'warning' | 'active' | 'idle'
}) {
  const statusColors = {
    good: 'border-emerald-500/30 text-emerald-400 bg-emerald-500/5',
    active: 'border-sodium-500/40 text-sodium-400 bg-sodium-500/5 animate-pulse',
    warning: 'border-amber-500/30 text-amber-400 bg-amber-500/5',
    bad: 'border-rose-500/30 text-rose-400 bg-rose-500/5',
    idle: 'border-ink-800 text-ink-300 bg-ink-850',
  }

  return (
    <div className={`rounded-xl border p-5 ${statusColors[status]}`}>
      <span className="text-xs font-medium uppercase tracking-wider text-ink-400">{label}</span>
      <div className="mt-1 text-2xl font-bold font-display tracking-tight text-ink-100">{value}</div>
      <p className="mt-1 text-xs text-ink-500">{subtext}</p>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; className: string }> = {
    queued: { label: 'Queued', className: 'bg-amber-500/10 text-amber-400 border-amber-500/20' },
    running: { label: 'Running', className: 'bg-sodium-500/10 text-sodium-400 border-sodium-500/20 animate-pulse' },
    done: { label: 'Done', className: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' },
    failed: { label: 'Failed', className: 'bg-rose-500/10 text-rose-400 border-rose-500/20' },
    cancelled: { label: 'Cancelled', className: 'bg-ink-700 text-ink-400 border-ink-600' },
  }
  const curr = map[status] || { label: status, className: 'bg-ink-800 text-ink-400' }
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold border ${curr.className}`}
    >
      {curr.label}
    </span>
  )
}
