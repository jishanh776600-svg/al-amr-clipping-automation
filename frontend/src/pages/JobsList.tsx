import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Job, onAuthChange } from '../api'
import { AuthModal } from '../components/AuthModal'
import { ErrorNote } from '../components/ErrorNote'

export function JobsList() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [isUnauthorized, setIsUnauthorized] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [showAuthModal, setShowAuthModal] = useState(false)

  const fetchJobs = () => {
    api
      .listJobs(50)
      .then((data) => {
        setJobs(data)
        setIsUnauthorized(false)
        setError(null)
      })
      .catch((err) => {
        if (err?.status === 401) {
          setIsUnauthorized(true)
        } else {
          setError(err)
        }
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    fetchJobs()
    const timer = setInterval(fetchJobs, 5000)
    const unsub = onAuthChange(() => fetchJobs())
    return () => {
      clearInterval(timer)
      unsub()
    }
  }, [])

  const handleCancel = async (jobId: string) => {
    setActionLoading(jobId)
    try {
      await api.cancelJob(jobId)
      fetchJobs()
    } catch (err: any) {
      setError(err)
    } finally {
      setActionLoading(null)
    }
  }

  const handleRetry = async (jobId: string) => {
    setActionLoading(jobId)
    try {
      await api.retryJob(jobId)
      fetchJobs()
    } catch (err: any) {
      setError(err)
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div className="space-y-6 pt-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display text-3xl font-bold text-ink-100">Production Jobs</h1>
          <p className="mt-1 text-sm text-ink-400">
            Authoritative remote queue status and historical job records.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link
            to="/new"
            className="rounded-lg bg-sodium-500 px-4 py-2 text-sm font-semibold text-ink-950 hover:bg-sodium-400"
          >
            + New Job
          </Link>
          <button
            onClick={fetchJobs}
            className="rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-sm text-ink-300 hover:bg-ink-700"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {isUnauthorized && (
        <div className="rounded-xl border border-sodium-500/40 bg-ink-850 p-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xl">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🔒</span>
            <div>
              <h2 className="font-display text-base font-bold text-ink-100">Operator Authentication Required</h2>
              <p className="mt-0.5 text-xs text-ink-400">Viewing production jobs requires an operator token (OPERATOR_TOKEN / AL_AMR_MASTER_KEY).</p>
            </div>
          </div>
          <button
            onClick={() => setShowAuthModal(true)}
            className="btn btn-primary shrink-0 text-xs"
          >
            Authenticate Now
          </button>
        </div>
      )}

      {error && !isUnauthorized && (
        <div className="mt-4">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-ink-800 bg-ink-850 shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-ink-800 bg-ink-900/60 text-xs uppercase text-ink-400">
              <tr>
                <th className="px-6 py-3.5">Source / Title</th>
                <th className="px-6 py-3.5">Status</th>
                <th className="px-6 py-3.5">Stage & Progress</th>
                <th className="px-6 py-3.5">Provider</th>
                <th className="px-6 py-3.5">Created</th>
                <th className="px-6 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-800/60">
              {loading && jobs.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-ink-500">
                    Loading jobs from remote server...
                  </td>
                </tr>
              )}
              {!loading && jobs.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-12 text-center text-ink-500">
                    No jobs found. Create one with the New Job button.
                  </td>
                </tr>
              )}
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-ink-800/40 transition">
                  <td className="px-6 py-4">
                    <div className="font-medium text-ink-200 truncate max-w-xs">
                      {job.source?.title || 'Direct Ingestion'}
                    </div>
                    <div className="text-xs font-mono text-ink-500 mt-0.5">{job.id}</div>
                  </td>
                  <td className="px-6 py-4">
                    <StatusBadge status={job.status} />
                    {job.error && (
                      <div className="mt-1 text-xs text-rose-400 truncate max-w-xs" title={job.error}>
                        {job.error}
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-ink-300">
                        {job.current_stage || (job.status === 'queued' ? 'waiting' : '-')}
                      </span>
                      <span className="text-xs text-ink-500">({Math.round(job.progress * 100)}%)</span>
                    </div>
                    {job.status === 'running' && (
                      <div className="mt-1.5 h-1.5 w-36 overflow-hidden rounded-full bg-ink-700">
                        <div
                          className="h-full bg-sodium-500"
                          style={{ width: `${Math.max(5, job.progress * 100)}%` }}
                        />
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-4 text-xs font-mono text-ink-400">{job.provider}</td>
                  <td className="px-6 py-4 text-xs text-ink-400">
                    {new Date(job.created_at).toLocaleString()}
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-2">
                      <Link
                        to={`/jobs/${job.id}`}
                        className="rounded bg-ink-750 px-2.5 py-1 text-xs font-medium text-ink-200 hover:bg-ink-700"
                      >
                        Progress
                      </Link>
                      {job.status === 'done' && (
                        <Link
                          to={`/jobs/${job.id}/clips`}
                          className="rounded bg-sodium-500/10 px-2.5 py-1 text-xs font-medium text-sodium-400 hover:bg-sodium-500/20"
                        >
                          Clips
                        </Link>
                      )}
                      {(job.status === 'queued' || job.status === 'running') && (
                        <button
                          onClick={() => handleCancel(job.id)}
                          disabled={actionLoading === job.id}
                          className="rounded bg-rose-500/10 px-2.5 py-1 text-xs text-rose-400 hover:bg-rose-500/20"
                        >
                          Cancel
                        </button>
                      )}
                      {(job.status === 'failed' || job.status === 'cancelled') && (
                        <button
                          onClick={() => handleRetry(job.id)}
                          disabled={actionLoading === job.id}
                          className="rounded bg-amber-500/10 px-2.5 py-1 text-xs text-amber-400 hover:bg-amber-500/20"
                        >
                          Retry
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <AuthModal
        isOpen={showAuthModal}
        onClose={() => setShowAuthModal(false)}
        onSuccess={() => fetchJobs()}
      />
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
