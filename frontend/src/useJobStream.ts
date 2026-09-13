import { useEffect, useRef, useState } from 'react'
import { api, getStoredToken, API_KEY, resolveUrl, type Job } from './api'

export interface StageProgress {
  stage: string
  stageProgress: number
  overall: number
  message: string
}

export interface AcquisitionProgress {
  phase: string
  status: 'active' | 'completed' | 'failed'
  provider?: string | null
  instance?: string | null
  message: string
  progressPercent?: number | null
  bytesDownloaded?: number | null
  totalBytes?: number | null
  downloadSpeed?: number | null
  etaSeconds?: number | null
  attempt?: number | null
  totalAttempts?: number | null
  telemetry?: Record<string, any>
}

/**
 * Subscribe to a job's Server-Sent Event stream.
 *
 * EventSource reconnects on its own, and the server replays a snapshot on every
 * connection, so a dropped connection self-heals without any retry logic here.
 * The one thing worth guarding is a terminal state: once a job is done there is
 * nothing left to stream, and an open connection would just churn heartbeats.
 */
export function useJobStream(jobId: string | undefined) {
  const [job, setJob] = useState<Job | null>(null)
  const [progress, setProgress] = useState<StageProgress | null>(null)
  const [acquisition, setAcquisition] = useState<AcquisitionProgress | null>(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<any>(null)
  const sourceRef = useRef<EventSource | null>(null)

  useEffect(() => {
    if (!jobId) return

    let cancelled = false
    setError(null)

    // Fetch once up front so the page renders immediately rather than waiting
    // for the stream's first message.
    api
      .getJob(jobId)
      .then((initial) => {
        if (!cancelled) {
          setJob(initial)
          setError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err)
      })

    const token = getStoredToken() || API_KEY
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : ''
    const source = new EventSource(resolveUrl(`/api/jobs/${jobId}/events${tokenParam}`))
    sourceRef.current = source

    const close = () => {
      source.close()
      setConnected(false)
    }

    source.onopen = () => setConnected(true)

    source.addEventListener('snapshot', (event) => {
      const data = JSON.parse((event as MessageEvent).data) as Job
      setJob(data)
      if (['done', 'failed', 'cancelled'].includes(data.status)) close()
    })

    source.addEventListener('progress', (event) => {
      const data = JSON.parse((event as MessageEvent).data)
      setProgress({
        stage: data.stage,
        stageProgress: data.stage_progress,
        overall: data.overall,
        message: data.message,
      })
      setJob((current) =>
        current
          ? { ...current, status: 'running', current_stage: data.stage, progress: data.overall }
          : current,
      )
    })

    source.addEventListener('acquisition', (event) => {
      try {
        const raw = JSON.parse((event as MessageEvent).data)
        const data: AcquisitionProgress = {
          phase: raw.phase,
          status: raw.status || 'active',
          provider: raw.provider,
          instance: raw.instance,
          message: raw.message || '',
          progressPercent: raw.progress_percent,
          bytesDownloaded: raw.bytes_downloaded,
          totalBytes: raw.total_bytes,
          downloadSpeed: raw.download_speed,
          etaSeconds: raw.eta_seconds,
          attempt: raw.attempt,
          totalAttempts: raw.total_attempts,
          telemetry: raw.telemetry || {},
        }
        setAcquisition(data)
      } catch {
        /* Ignore malformed event */
      }
    })

    const terminal = (status: Job['status']) => (event: Event) => {
      const data = JSON.parse((event as MessageEvent).data ?? '{}')
      setJob((current) =>
        current
          ? { ...current, status, error: data.error ?? null, progress: status === 'done' ? 1 : current.progress }
          : current,
      )
      close()
    }

    source.addEventListener('done', terminal('done'))
    source.addEventListener('failed', terminal('failed'))
    source.addEventListener('cancelled', terminal('cancelled'))

    source.onerror = () => setConnected(false)

    // Polling fallback every 3s ensures state updates even if SSE disconnects or proxy drops stream
    const pollInterval = setInterval(() => {
      if (cancelled) return
      api
        .getJob(jobId)
        .then((latest) => {
          if (!cancelled && latest) {
            setJob(latest)
            if (['done', 'failed', 'cancelled'].includes(latest.status)) {
              close()
              clearInterval(pollInterval)
            }
          }
        })
        .catch(() => {})
    }, 3000)

    return () => {
      cancelled = true
      clearInterval(pollInterval)
      source.close()
      sourceRef.current = null
    }
  }, [jobId])

  return { job, progress, acquisition, connected, setJob, error }
}
