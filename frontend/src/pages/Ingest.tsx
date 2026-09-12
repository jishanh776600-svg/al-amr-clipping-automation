import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  ApiError,
  api,
  formatDuration,
  type Job,
  type JobSettingsOverrides,
  type ProviderStatus,
  type CampaignPreset,
  type CampaignGuideline,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'

export function Ingest() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  // Input 1: Source
  const [sourceMode, setSourceMode] = useState<'url' | 'file'>('url')
  const [url, setUrl] = useState('')
  const [selectedVideoFile, setSelectedVideoFile] = useState<File | null>(null)
  const videoInputRef = useRef<HTMLInputElement>(null)
  const [videoDragging, setVideoDragging] = useState(false)

  // Input 2: Campaign Guidelines (PDF or DOCX)
  const [uploadedGuideline, setUploadedGuideline] = useState<CampaignGuideline | null>(null)
  const [guidelineUploading, setGuidelineUploading] = useState(false)
  const guidelineInputRef = useRef<HTMLInputElement>(null)
  const [guidelineDragging, setGuidelineDragging] = useState(false)

  // Job Submission & Lifecycle State
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [providers, setProviders] = useState<ProviderStatus[]>([])
  const [campaigns, setCampaigns] = useState<CampaignPreset[]>([])
  const [selectedCampaignId, setSelectedCampaignId] = useState<string>('')
  const [overrides, setOverrides] = useState<JobSettingsOverrides>({})
  const [advancedOpen, setAdvancedOpen] = useState(false)

  useEffect(() => {
    api.listJobs(8).then(setJobs).catch(() => undefined)
    api.providerStatus().then(setProviders).catch(() => undefined)
    api.listCampaigns().then((list) => {
      setCampaigns(list)
      const paramCampaignId = searchParams.get('campaign')
      if (paramCampaignId) {
        const found = list.find((c) => c.id === paramCampaignId)
        if (found) {
          setSelectedCampaignId(found.id)
          applyCampaign(found)
        }
      }
    }).catch(() => undefined)
  }, [searchParams])

  const applyCampaign = (c: CampaignPreset | null) => {
    if (!c) {
      setSelectedCampaignId('')
      setOverrides((prev) => {
        const next = { ...prev }
        delete next.campaign
        return next
      })
      return
    }
    setSelectedCampaignId(c.id)
    setOverrides((prev) => ({
      ...prev,
      campaign: c.brief as any,
      min_duration_s: c.brief?.minimum_duration ?? prev.min_duration_s,
      max_duration_s: c.brief?.maximum_duration ?? prev.max_duration_s,
      max_clips: c.brief?.output_count ?? prev.max_clips,
    }))
  }

  // Handle Guideline Document Selection & Immediate Extraction
  const handleGuidelineFile = async (file: File) => {
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
    if (ext !== '.pdf' && ext !== '.docx') {
      setError(new Error(`Unsupported guideline format "${ext}". Please provide a PDF (.pdf) or Word document (.docx).`))
      return
    }
    setError(null)
    setGuidelineUploading(true)
    try {
      const guideline = await api.uploadGuideline(file)
      setUploadedGuideline(guideline)
      if (guideline.parsed_brief) {
        setOverrides((prev) => ({
          ...prev,
          campaign: guideline.parsed_brief as any,
          min_duration_s: guideline.parsed_brief.minimum_duration ?? prev.min_duration_s,
          max_duration_s: guideline.parsed_brief.maximum_duration ?? prev.max_duration_s,
          max_clips: guideline.parsed_brief.output_count ?? prev.max_clips,
        }))
      }
    } catch (err) {
      setError(err as Error)
    } finally {
      setGuidelineUploading(false)
    }
  }

  const clearGuideline = () => {
    setUploadedGuideline(null)
    setOverrides((prev) => {
      const next = { ...prev }
      delete next.campaign
      return next
    })
    if (guidelineInputRef.current) guidelineInputRef.current.value = ''
  }

  // Handle Complete Autonomous Job Submission
  const handleStartJob = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    setError(null)

    if (sourceMode === 'url' && !url.trim()) {
      setError(new Error('Please paste a YouTube or video URL.'))
      return
    }
    if (sourceMode === 'file' && !selectedVideoFile) {
      setError(new Error('Please select or drop a source video file.'))
      return
    }

    setBusy('launching')
    try {
      let sourceId = ''
      if (sourceMode === 'url') {
        setBusy('Fetching source video...')
        const source = await api.ingestYouTube(url.trim())
        sourceId = source.id
      } else if (selectedVideoFile) {
        setBusy('Uploading source video...')
        const source = await api.uploadSource(selectedVideoFile)
        sourceId = source.id
      }

      setBusy('Creating autonomous job...')
      const guidelineId = uploadedGuideline?.id || null
      const job = await api.createJob(
        sourceId,
        overrides,
        overrides.campaign,
        guidelineId,
      )
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      setError(err as Error)
      setBusy(null)
    }
  }

  const canSubmit =
    (sourceMode === 'url' && url.trim().length > 0) ||
    (sourceMode === 'file' && selectedVideoFile !== null)

  return (
    <div className="pt-10 max-w-5xl">
      {/* Masthead */}
      <div className="rise">
        <div className="flex items-center gap-3">
          <span className="inline-flex items-center rounded bg-sodium-500/10 px-2.5 py-0.5 text-xs font-medium text-sodium-400 border border-sodium-500/20">
            AL AMR Autonomous Production
          </span>
          <span className="text-xs text-ink-500">Zero manual configuration required</span>
        </div>
        <h1 className="mt-4 font-display text-[clamp(2.25rem,5vw,4.5rem)] leading-[1.0] text-ink-100">
          Source In. Guidelines In.
          <br />
          <span className="italic text-sodium-500">Autonomous Shorts</span> Out.
        </h1>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-400">
          Provide your source video (or link) and drop your campaign guidelines document (PDF or DOCX).
          The engine extracts requirements, tracks speakers, reframes to 9:16, generates kinetic captions, and publishes automatically.
        </p>
      </div>

      {error && (
        <div className="mt-8">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {/* Main Dual-Input Section */}
      <div className="mt-10 grid gap-8 md:grid-cols-2">
        {/* INPUT 1: Source Video */}
        <div className="rounded-lg border border-ink-800 bg-ink-900/60 p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400">
                INPUT 1 · Source Video
              </span>
              <div className="flex gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => setSourceMode('url')}
                  className={`px-2.5 py-1 rounded transition-colors ${
                    sourceMode === 'url'
                      ? 'bg-sodium-500/20 text-sodium-300 font-medium border border-sodium-500/30'
                      : 'text-ink-400 hover:text-ink-200'
                  }`}
                >
                  Link
                </button>
                <button
                  type="button"
                  onClick={() => setSourceMode('file')}
                  className={`px-2.5 py-1 rounded transition-colors ${
                    sourceMode === 'file'
                      ? 'bg-sodium-500/20 text-sodium-300 font-medium border border-sodium-500/30'
                      : 'text-ink-400 hover:text-ink-200'
                  }`}
                >
                  File Upload
                </button>
              </div>
            </div>

            {sourceMode === 'url' ? (
              <div className="mt-4">
                <label htmlFor="source-url" className="text-xs text-ink-400 block mb-2">
                  Paste a video link (YouTube, direct MP4, or web stream):
                </label>
                <input
                  id="source-url"
                  className="field w-full text-base font-mono"
                  placeholder="https://youtube.com/watch?v=…"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy !== null}
                />
                <p className="mt-2 text-[11px] text-ink-500">
                  Headless cloud ingestion with automated bypass and streaming fallback.
                </p>
              </div>
            ) : (
              <div className="mt-4">
                <label className="text-xs text-ink-400 block mb-2">
                  Drop source media file:
                </label>
                <div
                  onDragOver={(e) => {
                    e.preventDefault()
                    setVideoDragging(true)
                  }}
                  onDragLeave={() => setVideoDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setVideoDragging(false)
                    const file = e.dataTransfer.files[0]
                    if (file) setSelectedVideoFile(file)
                  }}
                  onClick={() => videoInputRef.current?.click()}
                  className={`flex min-h-32 cursor-pointer flex-col items-center justify-center rounded border border-dashed p-4 text-center transition-colors ${
                    videoDragging
                      ? 'border-sodium-500 bg-sodium-500/10'
                      : selectedVideoFile
                      ? 'border-signal-good/50 bg-signal-good/5'
                      : 'border-ink-700 hover:border-ink-600 bg-ink-850/40'
                  }`}
                >
                  {selectedVideoFile ? (
                    <div>
                      <span className="text-sm font-medium text-signal-good block">
                        ✓ {selectedVideoFile.name}
                      </span>
                      <span className="text-xs text-ink-400 block mt-1">
                        {(selectedVideoFile.size / (1024 * 1024)).toFixed(1)} MB · Click to replace
                      </span>
                    </div>
                  ) : (
                    <div>
                      <span className="text-sm font-medium text-ink-200 block">
                        Drop video file or click to browse
                      </span>
                      <span className="text-xs text-ink-500 block mt-1">
                        MP4, MOV, MKV, WEBM, M4A, WAV
                      </span>
                    </div>
                  )}
                </div>
                <input
                  ref={videoInputRef}
                  type="file"
                  className="hidden"
                  accept="video/*,audio/*"
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) setSelectedVideoFile(file)
                    e.target.value = ''
                  }}
                />
              </div>
            )}
          </div>

          <div className="mt-4 pt-3 border-t border-ink-800/80 flex items-center justify-between text-xs text-ink-500">
            <span>Status:</span>
            <span className={canSubmit ? 'text-signal-good' : 'text-ink-500'}>
              {sourceMode === 'url'
                ? url.trim()
                  ? 'URL Ready'
                  : 'Awaiting URL'
                : selectedVideoFile
                ? `${selectedVideoFile.name} Ready`
                : 'Awaiting File'}
            </span>
          </div>
        </div>

        {/* INPUT 2: Campaign Guidelines (PDF or DOCX) */}
        <div className="rounded-lg border border-ink-800 bg-ink-900/60 p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400">
                INPUT 2 · Campaign Guidelines
              </span>
              <span className="text-[11px] text-ink-400 bg-ink-800/60 px-2 py-0.5 rounded">
                PDF or DOCX
              </span>
            </div>

            <p className="mt-2 text-xs text-ink-400">
              Upload your campaign document. AutoClip automatically parses topics, audience, duration limits, and CTA rules.
            </p>

            <div className="mt-4">
              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setGuidelineDragging(true)
                }}
                onDragLeave={() => setGuidelineDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setGuidelineDragging(false)
                  const file = e.dataTransfer.files[0]
                  if (file) handleGuidelineFile(file)
                }}
                onClick={() => guidelineInputRef.current?.click()}
                className={`flex min-h-32 cursor-pointer flex-col items-center justify-center rounded border border-dashed p-4 text-center transition-colors ${
                  guidelineDragging
                    ? 'border-sodium-500 bg-sodium-500/10'
                    : uploadedGuideline
                    ? 'border-sodium-500/60 bg-sodium-500/5'
                    : 'border-ink-700 hover:border-ink-600 bg-ink-850/40'
                }`}
              >
                {guidelineUploading ? (
                  <span className="text-sm text-sodium-400 animate-pulse">
                    Extracting requirements from document…
                  </span>
                ) : uploadedGuideline ? (
                  <div className="text-left w-full px-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold text-sodium-400 truncate max-w-[200px]">
                        📄 {uploadedGuideline.filename}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation()
                          clearGuideline()
                        }}
                        className="text-xs text-signal-bad hover:underline ml-2"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="mt-2 text-[11px] space-y-1 text-ink-300 bg-ink-950/40 p-2.5 rounded border border-ink-800">
                      <div>
                        <span className="text-ink-500">Campaign: </span>
                        <span className="text-ink-100 font-medium">
                          {uploadedGuideline.parsed_brief?.name || 'Extracted Campaign'}
                        </span>
                      </div>
                      {uploadedGuideline.parsed_brief?.target_audience && (
                        <div>
                          <span className="text-ink-500">Audience: </span>
                          <span>{uploadedGuideline.parsed_brief.target_audience}</span>
                        </div>
                      )}
                      {uploadedGuideline.parsed_brief?.required_topics?.length > 0 && (
                        <div>
                          <span className="text-ink-500">Topics: </span>
                          <span>{uploadedGuideline.parsed_brief.required_topics.slice(0, 3).join(', ')}</span>
                        </div>
                      )}
                      <div className="flex items-center justify-between text-[10px] text-ink-500 pt-1 border-t border-ink-800/60">
                        <span>
                          Duration: {uploadedGuideline.parsed_brief?.minimum_duration}s - {uploadedGuideline.parsed_brief?.maximum_duration}s
                        </span>
                        <span>
                          CTA: {uploadedGuideline.parsed_brief?.cta_required ? 'Required' : 'Optional'}
                        </span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div>
                    <span className="text-sm font-medium text-ink-200 block">
                      Drop guideline document (.pdf or .docx)
                    </span>
                    <span className="text-xs text-ink-500 block mt-1">
                      Or click to browse from device
                    </span>
                  </div>
                )}
              </div>
              <input
                ref={guidelineInputRef}
                type="file"
                className="hidden"
                accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) handleGuidelineFile(file)
                  e.target.value = ''
                }}
              />
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-ink-800/80 flex items-center justify-between text-xs text-ink-500">
            <span>Status:</span>
            <span className={uploadedGuideline ? 'text-sodium-400 font-medium' : 'text-ink-500'}>
              {guidelineUploading
                ? 'Extracting…'
                : uploadedGuideline
                ? '✓ Requirements Extracted'
                : 'Optional (defaults to viral highlighting)'}
            </span>
          </div>
        </div>
      </div>

      {/* Autonomous Launch Action Button */}
      <div className="mt-8 flex flex-col sm:flex-row items-center justify-between gap-4 rounded-lg border border-ink-800 bg-ink-900/40 p-5">
        <div>
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-signal-good animate-pulse"></span>
            <span className="text-sm font-medium text-ink-100">
              Autonomous Production Engine Ready
            </span>
          </div>
          <p className="text-xs text-ink-400 mt-1">
            Cloud on-demand worker handles Whisper audio extraction, MediaPipe face tracking, and FFmpeg 9:16 export.
          </p>
        </div>

        <button
          type="button"
          onClick={() => handleStartJob()}
          disabled={!canSubmit || busy !== null}
          className="btn btn-primary px-8 py-3 text-base shrink-0 w-full sm:w-auto flex items-center justify-center gap-2"
        >
          {busy ? (
            <span>{busy}</span>
          ) : (
            <>
              <span>Launch Autonomous Clipping</span>
              <span>→</span>
            </>
          )}
        </button>
      </div>

      {/* Advanced Optional Overrides Collapsible */}
      <div className="mt-8">
        <AdvancedOptions
          open={advancedOpen}
          onToggle={() => setAdvancedOpen((v) => !v)}
          overrides={overrides}
          onChange={setOverrides}
          providers={providers}
          campaigns={campaigns}
          selectedCampaignId={selectedCampaignId}
          onSelectCampaign={applyCampaign}
        />
      </div>

      {/* Recent Jobs */}
      <RecentJobs jobs={jobs} />
    </div>
  )
}

function AdvancedOptions({
  open,
  onToggle,
  overrides,
  onChange,
  providers,
  campaigns,
  selectedCampaignId,
  onSelectCampaign,
}: {
  open: boolean
  onToggle: () => void
  overrides: JobSettingsOverrides
  onChange: (next: JobSettingsOverrides) => void
  providers: ProviderStatus[]
  campaigns: CampaignPreset[]
  selectedCampaignId: string
  onSelectCampaign: (c: CampaignPreset | null) => void
}) {
  const set = <K extends keyof JobSettingsOverrides>(key: K, value: JobSettingsOverrides[K]) =>
    onChange({ ...overrides, [key]: value })

  return (
    <div className="rounded border border-ink-800/80 bg-ink-950/40 p-4">
      <button
        type="button"
        onClick={onToggle}
        className="flex items-center justify-between w-full text-xs text-ink-400 hover:text-ink-200"
      >
        <span className="font-semibold uppercase tracking-wider">
          Advanced Pipeline Controls (Optional)
        </span>
        <span className="text-xs font-mono">{open ? '▲ Collapse' : '▼ Expand'}</span>
      </button>

      {open && (
        <div className="mt-5 grid gap-x-8 gap-y-5 pt-4 border-t border-ink-800 sm:grid-cols-2">
          {campaigns.length > 0 && (
            <Selector
              label="Saved Campaign Preset"
              value={selectedCampaignId}
              onChange={(v) => {
                const found = campaigns.find((c) => c.id === v) || null
                onSelectCampaign(found)
              }}
              options={[
                { value: '', label: 'None (Or use uploaded guideline document)' },
                ...campaigns.map((c) => ({
                  value: c.id,
                  label: `🎯 ${c.name}`,
                })),
              ]}
            />
          )}
          <Selector
            label="Highlight Provider"
            value={overrides.provider ?? ''}
            onChange={(v) => set('provider', v || undefined)}
            options={[
              { value: '', label: 'Autonomous (Built-in Heuristic & Guideline Engine)' },
              ...providers.map((p) => ({
                value: p.name,
                label: p.available ? `${p.name} (Ready)` : `${p.name} (API key needed)`,
                disabled: false,
              })),
            ]}
          />
          <Selector
            label="Whisper Model"
            value={overrides.whisper_model ?? ''}
            onChange={(v) => set('whisper_model', v || undefined)}
            options={[
              { value: '', label: 'base (Default recommended)' },
              { value: 'tiny', label: 'tiny — fastest' },
              { value: 'base', label: 'base — balanced' },
              { value: 'small', label: 'small — higher accuracy' },
              { value: 'medium', label: 'medium' },
              { value: 'large-v3', label: 'large-v3 — highest accuracy' },
            ]}
          />
          <Selector
            label="Export Aspect Ratio"
            value={overrides.ratio ?? '9:16'}
            onChange={(v) => set('ratio', (v as any) || '9:16')}
            options={[
              { value: '9:16', label: '9:16 Vertical (Shorts, Reels, TikTok)' },
              { value: '1:1', label: '1:1 Square (Feed posts)' },
              { value: '16:9', label: '16:9 Landscape' },
            ]}
          />
          <NumberField
            label="Maximum Clips"
            value={overrides.max_clips}
            placeholder="5"
            min={1}
            max={50}
            onChange={(v) => set('max_clips', v)}
          />
          <div className="grid grid-cols-2 gap-4">
            <NumberField
              label="Min Length (s)"
              value={overrides.min_duration_s}
              placeholder="20"
              min={5}
              max={300}
              onChange={(v) => set('min_duration_s', v)}
            />
            <NumberField
              label="Max Length (s)"
              value={overrides.max_duration_s}
              placeholder="75"
              min={5}
              max={300}
              onChange={(v) => set('max_duration_s', v)}
            />
          </div>
          <label className="flex items-center gap-3 text-sm text-ink-200 sm:col-span-2">
            <input
              type="checkbox"
              checked={overrides.diarization ?? false}
              onChange={(e) => set('diarization', e.target.checked || undefined)}
              className="size-4 accent-sodium-500"
            />
            Multiple speaker diarization — label who is speaking and switch camera focus
          </label>
        </div>
      )}
    </div>
  )
}

function Selector({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string; disabled?: boolean }[]
}) {
  return (
    <label className="block">
      <span className="eyebrow text-xs text-ink-400">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="field mt-1 cursor-pointer text-sm w-full bg-ink-850 text-ink-100"
      >
        {options.map((option) => (
          <option
            key={option.value}
            value={option.value}
            disabled={option.disabled}
            className="bg-ink-850"
          >
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}

function NumberField({
  label,
  value,
  placeholder,
  min,
  max,
  onChange,
}: {
  label: string
  value: number | undefined
  placeholder: string
  min: number
  max: number
  onChange: (value: number | undefined) => void
}) {
  return (
    <label className="block">
      <span className="eyebrow text-xs text-ink-400">{label}</span>
      <input
        type="number"
        className="field numeric mt-1 text-sm w-full"
        placeholder={placeholder}
        value={value ?? ''}
        min={min}
        max={max}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
      />
    </label>
  )
}

function RecentJobs({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) return null

  return (
    <section className="rise mt-20" style={{ animationDelay: '240ms' }}>
      <div className="flex items-baseline justify-between border-b border-ink-800 pb-3">
        <h2 className="eyebrow">Recent Ingests & Jobs</h2>
        <span className="numeric text-xs text-ink-600">{jobs.length} jobs</span>
      </div>

      <ul>
        {jobs.map((job) => (
          <li key={job.id}>
            <a
              href={job.status === 'done' ? `/jobs/${job.id}/clips` : `/jobs/${job.id}`}
              className="group grid grid-cols-[1fr_auto] items-baseline gap-4 border-b border-ink-850 py-4 transition-colors duration-200 hover:bg-ink-850/40 sm:grid-cols-[1fr_7rem_6rem_5rem]"
            >
              <div className="truncate">
                <span className="text-[0.9375rem] text-ink-200 group-hover:text-ink-100">
                  {job.source?.title || 'Untitled Video'}
                </span>
                {job.guideline?.filename && (
                  <span className="ml-2 inline-flex items-center rounded bg-sodium-500/10 px-1.5 py-0.2 text-[10px] text-sodium-400 border border-sodium-500/20">
                    📄 {job.guideline.filename}
                  </span>
                )}
              </div>
              <span className="numeric hidden text-xs text-ink-500 sm:block">
                {job.source ? formatDuration(job.source.duration_s) : '—'}
              </span>
              <span className="hidden text-xs text-ink-500 sm:block">
                {job.provider || 'autonomous'}
              </span>
              <StatusTag job={job} />
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}

function StatusTag({ job }: { job: Job }) {
  const tone: Record<string, string> = {
    done: 'text-signal-good',
    failed: 'text-signal-bad',
    running: 'text-sodium-500',
    queued: 'text-ink-400',
    cancelled: 'text-ink-500',
  }
  const label =
    job.status === 'running' ? `${Math.round(job.progress * 100)}%` : job.status

  return (
    <span className={`numeric justify-self-end text-xs ${tone[job.status] ?? 'text-ink-400'}`}>
      {label}
    </span>
  )
}
