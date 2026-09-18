import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import {
  ApiError,
  api,
  formatBytes,
  formatDuration,
  type BGMAsset,
  type Job,
  type JobSettingsOverrides,
  type ProviderStatus,
  type CampaignPreset,
  type CampaignSpecification,
  type CaptionStyleItem,
  type VisualFilter,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'

export function Ingest() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  // Input 1: Source Video (Direct upload is the canonical Step 14 flow)
  const [sourceMode, setSourceMode] = useState<'file' | 'url'>('file')
  const [url, setUrl] = useState('')
  const [selectedVideoFile, setSelectedVideoFile] = useState<File | null>(null)
  const videoInputRef = useRef<HTMLInputElement>(null)
  const [videoDragging, setVideoDragging] = useState(false)

  // Input 2: Campaign Materials & Intelligence (Step 14)
  const [campaignUrl, setCampaignUrl] = useState('')
  const [guidelineFiles, setGuidelineFiles] = useState<File[]>([])
  const [driveUrl, setDriveUrl] = useState('')
  const [campaignSpec, setCampaignSpec] = useState<CampaignSpecification | null>(null)
  const [specExtracting, setSpecExtracting] = useState(false)
  const guidelineInputRef = useRef<HTMLInputElement>(null)
  const [guidelineDragging, setGuidelineDragging] = useState(false)

  // Destinations & Archival
  const [publishDestinations, setPublishDestinations] = useState<string[]>(['telegram', 'drive'])

  // Caption / Subtitle Style Selection (25 Options)
  const [captionStylesList, setCaptionStylesList] = useState<CaptionStyleItem[]>([])
  const [captionStyle, setCaptionStyle] = useState<string>(() => {
    try {
      return localStorage.getItem('alamr_caption_style') || 'classic_professional'
    } catch {
      return 'classic_professional'
    }
  })

  // Visual Filter Selection (20+ Options)
  const [visualFiltersList, setVisualFiltersList] = useState<VisualFilter[]>([])
  const [visualFilter, setVisualFilter] = useState<string>(() => {
    try {
      return localStorage.getItem('alamr_visual_filter') || 'original'
    } catch {
      return 'original'
    }
  })

  // Step 20: BGM Vault & Campaign Background Music (Step 20 Operator Choice)
  const [bgmAssets, setBgmAssets] = useState<BGMAsset[]>([])
  const [selectedBgmId, setSelectedBgmId] = useState<string>(() => {
    try {
      return localStorage.getItem('alamr_selected_bgm_id') || ''
    } catch {
      return ''
    }
  })

  const updateCaptionStyle = (newStyle: string) => {
    setCaptionStyle(newStyle)
    try {
      localStorage.setItem('alamr_caption_style', newStyle)
    } catch {}
    api.putSettings({ export: { caption_style: newStyle } as any }).catch(() => undefined)
  }

  const updateVisualFilter = (newFilter: string) => {
    setVisualFilter(newFilter)
    try {
      localStorage.setItem('alamr_visual_filter', newFilter)
    } catch {}
    api.putSettings({ export: { visual_filter: newFilter } as any }).catch(() => undefined)
  }

  const updateSelectedBgmId = (newBgmId: string) => {
    setSelectedBgmId(newBgmId)
    try {
      localStorage.setItem('alamr_selected_bgm_id', newBgmId)
    } catch {}
    api.putSettings({ export: { bgm_asset_id: newBgmId } as any }).catch(() => undefined)
  }
  const [bgmVaultOpen, setBgmVaultOpen] = useState(false)
  const [bgmUploading, setBgmUploading] = useState(false)
  const [bgmUploadFile, setBgmUploadFile] = useState<File | null>(null)
  const [bgmUploadName, setBgmUploadName] = useState('')
  const [bgmUploadGenre, setBgmUploadGenre] = useState('')
  const [bgmUploadMood, setBgmUploadMood] = useState('')
  const [bgmUploadTags, setBgmUploadTags] = useState('')
  const [playingAudioId, setPlayingAudioId] = useState<string | null>(null)
  const bgmFileInputRef = useRef<HTMLInputElement>(null)
  const audioPreviewRef = useRef<HTMLAudioElement | null>(null)

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
    api.getSettings().then((s) => {
      if (s?.export) {
        if (s.export.caption_style) {
          const cached = localStorage.getItem('alamr_caption_style')
          if (!cached) setCaptionStyle(s.export.caption_style)
        }
        if (s.export.visual_filter) {
          const cached = localStorage.getItem('alamr_visual_filter')
          if (!cached) setVisualFilter(s.export.visual_filter)
        }
        if (s.export.bgm_asset_id !== undefined) {
          const cached = localStorage.getItem('alamr_selected_bgm_id')
          if (cached === null) setSelectedBgmId(s.export.bgm_asset_id)
        }
      }
    }).catch(() => undefined)
    api.listCaptionStyles().then((list) => {
      if (list && list.length > 0) setCaptionStylesList(list)
    }).catch(() => undefined)
    api.listVisualFilters().then((list) => {
      if (list && list.length > 0) setVisualFiltersList(list)
    }).catch(() => undefined)
    api.listBGMAssets().then((list) => {
      setBgmAssets(list)
      setSelectedBgmId((current) => {
        if (!current || current === 'none') return current
        const exists = list.some((a) => a.id === current && a.enabled)
        return exists ? current : ''
      })
    }).catch(() => undefined)
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

  // Handle Multi-Document Guideline Selection & Analysis
  const handleAddGuidelineFiles = (files: FileList | File[]) => {
    const validFiles: File[] = []
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      const ext = f.name.substring(f.name.lastIndexOf('.')).toLowerCase()
      if (ext === '.pdf' || ext === '.docx') {
        validFiles.push(f)
      }
    }
    if (validFiles.length === 0) {
      setError(new Error('Please provide PDF (.pdf) or Word (.docx) guideline documents.'))
      return
    }
    setError(null)
    const nextFiles = [...guidelineFiles, ...validFiles]
    setGuidelineFiles(nextFiles)
    triggerIntelligenceAnalysis(nextFiles, campaignUrl, driveUrl)
  }

  const handleRemoveGuidelineFile = (index: number) => {
    const nextFiles = guidelineFiles.filter((_, i) => i !== index)
    setGuidelineFiles(nextFiles)
    if (nextFiles.length === 0 && !campaignUrl.trim() && !driveUrl.trim()) {
      setCampaignSpec(null)
    } else {
      triggerIntelligenceAnalysis(nextFiles, campaignUrl, driveUrl)
    }
  }

  const triggerIntelligenceAnalysis = async (files: File[], cUrl: string, dUrl: string) => {
    if (files.length === 0 && !cUrl.trim() && !dUrl.trim()) {
      setCampaignSpec(null)
      return
    }
    setSpecExtracting(true)
    setError(null)
    try {
      const form = new FormData()
      files.forEach((f) => form.append('files', f))
      if (cUrl.trim()) form.append('campaign_url', cUrl.trim())
      if (dUrl.trim()) form.append('drive_urls', dUrl.trim())

      const spec = await api.extractCampaignIntelligence(form)
      setCampaignSpec(spec)

      // Layer duration / clips overrides if parsed
      setOverrides((prev) => ({
        ...prev,
        min_duration_s: spec.duration_min_s?.value ?? prev.min_duration_s,
        max_duration_s: spec.duration_max_s?.value ?? prev.max_duration_s,
        max_clips: spec.output_count?.value ?? prev.max_clips,
      }))
    } catch (err) {
      // Non-blocking warning for preview
      console.warn('Intelligence preview extraction error:', err)
    } finally {
      setSpecExtracting(false)
    }
  }

  const clearAllGuidelines = () => {
    setGuidelineFiles([])
    setCampaignUrl('')
    setDriveUrl('')
    setCampaignSpec(null)
    if (guidelineInputRef.current) guidelineInputRef.current.value = ''
  }

  // Step 20 BGM Vault Actions
  const refreshBgmAssets = async () => {
    try {
      const list = await api.listBGMAssets()
      setBgmAssets(list)
      setSelectedBgmId((prev) => {
        if (!prev || prev === 'none') return prev
        const exists = list.some((a) => a.id === prev && a.enabled)
        return exists ? prev : ''
      })
    } catch {
      // ignore
    }
  }

  const handleUploadBgm = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!bgmUploadFile) return
    setBgmUploading(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', bgmUploadFile)
      if (bgmUploadName.trim()) form.append('name', bgmUploadName.trim())
      if (bgmUploadGenre.trim()) form.append('genre', bgmUploadGenre.trim())
      if (bgmUploadMood.trim()) form.append('mood', bgmUploadMood.trim())
      if (bgmUploadTags.trim()) form.append('tags', bgmUploadTags.trim())

      const created = await api.uploadBGMAsset(form)
      setBgmUploadFile(null)
      setBgmUploadName('')
      setBgmUploadGenre('')
      setBgmUploadMood('')
      setBgmUploadTags('')
      if (bgmFileInputRef.current) bgmFileInputRef.current.value = ''
      await refreshBgmAssets()
      updateSelectedBgmId(created.id)
    } catch (err) {
      setError(err as Error)
    } finally {
      setBgmUploading(false)
    }
  }

  const handleToggleBgmEnabled = async (asset: BGMAsset) => {
    try {
      await api.updateBGMAsset(asset.id, { enabled: !asset.enabled })
      await refreshBgmAssets()
      if (asset.enabled && selectedBgmId === asset.id) {
        updateSelectedBgmId('')
      }
    } catch (err: any) {
      if (err?.status === 404 || err?.message?.includes('not found')) {
        await refreshBgmAssets()
        if (selectedBgmId === asset.id) updateSelectedBgmId('')
        setError(null)
      } else {
        setError(err as Error)
      }
    }
  }

  const handleDeleteBgm = async (id: string) => {
    if (!window.confirm('Delete this BGM track from vault?')) return
    try {
      await api.deleteBGMAsset(id)
      if (selectedBgmId === id) updateSelectedBgmId('')
      await refreshBgmAssets()
    } catch (err: any) {
      if (err?.status === 404 || err?.message?.includes('not found')) {
        await refreshBgmAssets()
        if (selectedBgmId === id) updateSelectedBgmId('')
        setError(null)
      } else {
        setError(err as Error)
      }
    }
  }

  const toggleAudioPreview = (assetId: string) => {
    if (playingAudioId === assetId) {
      if (audioPreviewRef.current) {
        audioPreviewRef.current.pause()
      }
      setPlayingAudioId(null)
    } else {
      if (audioPreviewRef.current) {
        audioPreviewRef.current.src = `/api/bgm/${assetId}/stream`
        audioPreviewRef.current.play().catch(() => undefined)
      }
      setPlayingAudioId(assetId)
    }
  }

  // Handle Complete Autonomous Job Submission
  const handleStartJob = async (e?: React.FormEvent) => {
    if (e) e.preventDefault()
    setError(null)

    if (sourceMode === 'file' && !selectedVideoFile) {
      setError(new Error('Please select or drop a source video file.'))
      return
    }
    if (sourceMode === 'url' && !url.trim()) {
      setError(new Error('Please provide a source video URL.'))
      return
    }

    setBusy('Launching autonomous pipeline...')
    try {
      const jobOverrides: any = {
        ...overrides,
        destinations: publishDestinations,
      }

      const form = new FormData()
      if (sourceMode === 'file' && selectedVideoFile) {
        form.append('video_file', selectedVideoFile)
      } else if (sourceMode === 'url' && url.trim()) {
        form.append('url', url.trim())
      }

      // Campaign materials: multiple files, campaign URL, drive URLs
      guidelineFiles.forEach((f) => form.append('guideline_files', f))
      if (campaignUrl.trim()) form.append('campaign_url', campaignUrl.trim())
      if (driveUrl.trim()) form.append('drive_guideline_urls', driveUrl.trim())

      form.append('destinations', JSON.stringify(publishDestinations))
      form.append('caption_style', captionStyle)
      jobOverrides.caption_style = captionStyle

      form.append('visual_filter', visualFilter)
      jobOverrides.visual_filter = visualFilter

      // BGM Selection: '' -> Default Canonical BGM, 'none' -> Explicitly No BGM, asset_id -> Exact Track
      const effectiveBgmId = selectedBgmId === 'none'
        ? 'none'
        : (bgmAssets.some((a) => a.id === selectedBgmId && a.enabled) ? selectedBgmId : '')
      form.append('bgm_asset_id', effectiveBgmId)
      jobOverrides.bgm_asset_id = effectiveBgmId || null

      form.append('overrides', JSON.stringify(jobOverrides))

      const job = await api.createAutonomousJob(form)
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      setError(err as Error)
      setBusy(null)
    }
  }

  const canSubmit =
    (sourceMode === 'file' && selectedVideoFile !== null) ||
    (sourceMode === 'url' && url.trim().length > 0)

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

        {/* INPUT 2: Campaign Intelligence & Multi-Document Ingestion (Step 14) */}
        <div className="rounded-lg border border-ink-800 bg-ink-900/60 p-6 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400">
                INPUT 2 · Campaign Intelligence
              </span>
              <div className="flex items-center gap-2">
                {specExtracting && (
                  <span className="text-[11px] text-sodium-400 animate-pulse">
                    Analyzing materials…
                  </span>
                )}
                {(guidelineFiles.length > 0 || campaignUrl || driveUrl) && (
                  <button
                    type="button"
                    onClick={clearAllGuidelines}
                    className="text-[11px] text-signal-bad hover:underline"
                  >
                    Clear All
                  </button>
                )}
              </div>
            </div>

            <p className="mt-2 text-xs text-ink-400">
              Provide Campaign URL and/or 1 or many guideline materials (PDF, DOCX, Drive). AutoClip normalizes rules and resolves conflicts.
            </p>

            <div className="mt-4 space-y-3">
              {/* Campaign URL */}
              <div>
                <label className="text-[11px] font-medium text-ink-300 block mb-1">
                  Campaign URL (Notion, Whop, Landing Page):
                </label>
                <div className="flex gap-2">
                  <input
                    type="url"
                    value={campaignUrl}
                    onChange={(e) => {
                      setCampaignUrl(e.target.value)
                    }}
                    onBlur={() => {
                      if (campaignUrl.trim() || guidelineFiles.length > 0 || driveUrl.trim()) {
                        triggerIntelligenceAnalysis(guidelineFiles, campaignUrl, driveUrl)
                      }
                    }}
                    placeholder="https://whop.com/... or https://notion.so/..."
                    className="field text-xs font-mono w-full"
                    disabled={busy !== null}
                  />
                  <button
                    type="button"
                    disabled={specExtracting || !campaignUrl.trim()}
                    onClick={() => triggerIntelligenceAnalysis(guidelineFiles, campaignUrl, driveUrl)}
                    className="btn btn-secondary shrink-0 text-xs px-2.5"
                  >
                    Fetch
                  </button>
                </div>
              </div>

              {/* Multi-Document Drag & Drop Upload */}
              <div>
                <label className="text-[11px] font-medium text-ink-300 block mb-1">
                  Campaign Materials (1 or Many PDF & DOCX Files):
                </label>
                <div
                  onDragOver={(e) => {
                    e.preventDefault()
                    setGuidelineDragging(true)
                  }}
                  onDragLeave={() => setGuidelineDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault()
                    setGuidelineDragging(false)
                    if (e.dataTransfer.files?.length) {
                      handleAddGuidelineFiles(e.dataTransfer.files)
                    }
                  }}
                  onClick={() => guidelineInputRef.current?.click()}
                  className={`flex min-h-24 cursor-pointer flex-col items-center justify-center rounded border border-dashed p-3 text-center transition-colors ${
                    guidelineDragging
                      ? 'border-sodium-500 bg-sodium-500/10'
                      : guidelineFiles.length > 0
                      ? 'border-sodium-500/60 bg-sodium-500/5'
                      : 'border-ink-700 hover:border-ink-600 bg-ink-850/40'
                  }`}
                >
                  <span className="text-xs font-medium text-ink-200 block">
                    Drop PDF or Word (.docx) files or click to browse
                  </span>
                  <span className="text-[10px] text-ink-500 block mt-0.5">
                    Supports multiple simultaneous guideline documents
                  </span>
                </div>
                <input
                  ref={guidelineInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  onChange={(e) => {
                    if (e.target.files?.length) {
                      handleAddGuidelineFiles(e.target.files)
                    }
                    e.target.value = ''
                  }}
                />

                {/* List of uploaded guideline files */}
                {guidelineFiles.length > 0 && (
                  <div className="mt-2 space-y-1 max-h-28 overflow-y-auto pr-1">
                    {guidelineFiles.map((file, idx) => (
                      <div
                        key={`${file.name}-${idx}`}
                        className="flex items-center justify-between bg-ink-950/60 px-2.5 py-1.5 rounded border border-ink-800 text-[11px]"
                      >
                        <div className="flex items-center gap-2 truncate">
                          <span className="text-sodium-400 font-mono text-[10px]">
                            {file.name.endsWith('.pdf') ? 'PDF' : 'DOCX'}
                          </span>
                          <span className="text-ink-200 truncate font-medium">
                            {file.name}
                          </span>
                          <span className="text-ink-500 text-[10px]">
                            ({(file.size / 1024).toFixed(0)} KB)
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleRemoveGuidelineFile(idx)
                          }}
                          className="text-signal-bad hover:underline ml-2 text-[10px]"
                        >
                          ✕
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Optional Google Drive / Google Docs */}
              <div>
                <label className="text-[11px] font-medium text-ink-300 block mb-1">
                  Google Drive / Docs Material Link:
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={driveUrl}
                    onChange={(e) => setDriveUrl(e.target.value)}
                    onBlur={() => {
                      if (driveUrl.trim() || guidelineFiles.length > 0 || campaignUrl.trim()) {
                        triggerIntelligenceAnalysis(guidelineFiles, campaignUrl, driveUrl)
                      }
                    }}
                    placeholder="https://docs.google.com/... or Drive share link"
                    className="field text-xs font-mono w-full"
                    disabled={busy !== null}
                  />
                  <button
                    type="button"
                    disabled={specExtracting || !driveUrl.trim()}
                    onClick={() => triggerIntelligenceAnalysis(guidelineFiles, campaignUrl, driveUrl)}
                    className="btn btn-secondary shrink-0 text-xs px-2.5"
                  >
                    Fetch
                  </button>
                </div>
              </div>
            </div>

            {/* LIVE CAMPAIGN SPECIFICATION & CONFLICT REVIEW BANNER */}
            {campaignSpec && (
              <div className="mt-4 pt-3 border-t border-ink-800/80 space-y-2.5">
                {/* Conflict Alert Banner */}
                {campaignSpec.conflicts && campaignSpec.conflicts.length > 0 && (
                  <div className="rounded border border-signal-warn/60 bg-signal-warn/10 p-2.5 text-[11px]">
                    <div className="flex items-center justify-between text-signal-warn font-semibold">
                      <span>⚠️ {campaignSpec.conflicts.length} Requirement Contradiction(s) Detected</span>
                      <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-signal-warn/20 border border-signal-warn/30">
                        {campaignSpec.has_critical_conflicts ? 'Critical Conflicts' : 'Auto-Resolved'}
                      </span>
                    </div>
                    <div className="mt-1.5 space-y-1.5">
                      {campaignSpec.conflicts.map((c) => (
                        <div
                          key={c.id}
                          className="bg-ink-950/60 p-1.5 rounded border border-signal-warn/20 text-ink-300"
                        >
                          <div className="flex items-center justify-between text-[10px]">
                            <span className="font-semibold text-signal-warn capitalize">
                              [{c.rule_category.replace('_', ' ')}]
                            </span>
                            <span className={`px-1 py-0.2 rounded font-mono ${
                              c.resolution_status === 'superseded' ? 'text-signal-good' : 'text-signal-warn'
                            }`}>
                              {c.resolution_status}
                            </span>
                          </div>
                          <p className="mt-0.5 text-ink-200">{c.description}</p>
                          {c.resolution_notes && (
                            <p className="text-[10px] text-ink-400 mt-0.5 italic">{c.resolution_notes}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Normalized Rules Summary */}
                <div className="rounded bg-ink-950/50 p-2.5 border border-ink-800 text-[11px] space-y-1 text-ink-300">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-sodium-400">
                      ✓ {campaignSpec.title || 'Normalized Campaign Specification'}
                    </span>
                    <span className="text-ink-500 text-[10px]">
                      {campaignSpec.documents.length} Source Document(s)
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-1.5 pt-1 text-[10px]">
                    <div>
                      <span className="text-ink-500">Duration: </span>
                      <span className="text-ink-200 font-medium">
                        {campaignSpec.duration_min_s?.value}s - {campaignSpec.duration_max_s?.value}s
                      </span>
                      <span className="text-ink-500 ml-1">({campaignSpec.duration_min_s?.confidence})</span>
                    </div>
                    <div>
                      <span className="text-ink-500">Ratio: </span>
                      <span className="text-ink-200 font-medium">{campaignSpec.aspect_ratio?.value}</span>
                    </div>
                    <div>
                      <span className="text-ink-500">Hook Window: </span>
                      <span className="text-ink-200 font-medium">&lt; {campaignSpec.hook_window_s?.value}s</span>
                    </div>
                    <div>
                      <span className="text-ink-500">CTA: </span>
                      <span className="text-ink-200 font-medium">
                        {campaignSpec.cta_required?.value ? 'Mandatory' : 'Optional'}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="mt-4 pt-3 border-t border-ink-800/80 flex items-center justify-between text-xs text-ink-500">
            <span>Intelligence Status:</span>
            <span className={campaignSpec ? 'text-sodium-400 font-medium' : 'text-ink-500'}>
              {specExtracting
                ? 'Extracting & Normalizing…'
                : campaignSpec
                ? `✓ Normalized (${campaignSpec.documents.length} docs, ${campaignSpec.conflicts.length} conflicts)`
                : 'Awaiting materials (defaults to viral highlighting)'}
            </span>
          </div>
        </div>
      </div>

      {/* INPUT 3: Publishing Destinations */}
      <div className="mt-6 rounded-lg border border-ink-800 bg-ink-900/60 p-5">
        <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400 block mb-3">
          Publishing & Archival Destinations
        </span>
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-3 text-xs">
          <label className="flex items-center gap-2.5 p-3 rounded border border-ink-700 bg-ink-850/50 cursor-pointer hover:border-ink-600">
            <input
              type="checkbox"
              checked={publishDestinations.includes('telegram')}
              onChange={(e) => {
                if (e.target.checked) setPublishDestinations([...publishDestinations, 'telegram'])
                else setPublishDestinations(publishDestinations.filter(d => d !== 'telegram'))
              }}
              className="rounded border-ink-600 text-sodium-500"
            />
            <div>
              <span className="font-semibold text-ink-100 block">Telegram</span>
              <span className="text-[11px] text-ink-400 block">Production publication</span>
            </div>
          </label>

          <label className="flex items-center gap-2.5 p-3 rounded border border-ink-700 bg-ink-850/50 cursor-pointer hover:border-ink-600">
            <input
              type="checkbox"
              checked={publishDestinations.includes('drive')}
              onChange={(e) => {
                if (e.target.checked) setPublishDestinations([...publishDestinations, 'drive'])
                else setPublishDestinations(publishDestinations.filter(d => d !== 'drive'))
              }}
              className="rounded border-ink-600 text-sodium-500"
            />
            <div>
              <span className="font-semibold text-ink-100 block">Google Drive</span>
              <span className="text-[11px] text-ink-400 block">Durable media archival</span>
            </div>
          </label>

          <label className="flex items-center gap-2.5 p-3 rounded border border-ink-700 bg-ink-850/50 cursor-pointer hover:border-ink-600">
            <input
              type="checkbox"
              checked={publishDestinations.includes('youtube')}
              onChange={(e) => {
                if (e.target.checked) setPublishDestinations([...publishDestinations, 'youtube'])
                else setPublishDestinations(publishDestinations.filter(d => d !== 'youtube'))
              }}
              className="rounded border-ink-600 text-sodium-500"
            />
            <div>
              <span className="font-semibold text-ink-100 block">YouTube Shorts</span>
              <span className="text-[11px] text-ink-400 block">Safe Dry-Run</span>
            </div>
          </label>

          <label className="flex items-center gap-2.5 p-3 rounded border border-ink-700 bg-ink-850/50 cursor-pointer hover:border-ink-600">
            <input
              type="checkbox"
              checked={publishDestinations.includes('instagram')}
              onChange={(e) => {
                if (e.target.checked) setPublishDestinations([...publishDestinations, 'instagram'])
                else setPublishDestinations(publishDestinations.filter(d => d !== 'instagram'))
              }}
              className="rounded border-ink-600 text-sodium-500"
            />
            <div>
              <span className="font-semibold text-ink-100 block">Instagram</span>
              <span className="text-[11px] text-ink-400 block">Reels export staging</span>
            </div>
          </label>
        </div>
      </div>

      {/* SUBTITLE TEMPLATE SELECTION (25 STYLES) */}
      <div className="mt-6 rounded-lg border border-ink-800 bg-ink-900/60 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 mb-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400">
                CAPTION & SUBTITLE TEMPLATES
              </span>
              <span className="text-[10px] rounded bg-sodium-500/10 text-sodium-400 px-2 py-0.5 border border-sodium-500/20 font-medium">
                25 Canonical Styles
              </span>
            </div>
            <p className="text-xs text-ink-400 mt-1">
              Select the typography and pacing aesthetic for generated vertical clips. Every template enforces 9:16 safe margins and word-level timing.
            </p>
          </div>
          <div className="flex items-center gap-2 mt-2 sm:mt-0">
            <label className="text-xs text-ink-300 font-medium whitespace-nowrap">Preset:</label>
            <select
              value={captionStyle}
              onChange={(e) => updateCaptionStyle(e.target.value)}
              className="bg-ink-950 border border-ink-700 text-ink-100 text-xs rounded px-2.5 py-1.5 focus:border-sodium-500"
            >
              {(captionStylesList.length > 0 ? captionStylesList : [
                { key: 'classic_professional', label: 'Classic Professional' },
                { key: 'rich_dynamic', label: 'Rich Dynamic' },
                { key: 'clean_lower', label: 'Clean Lower' },
                { key: 'bold_pop', label: 'Bold Pop' },
                { key: 'karaoke_fill', label: 'Karaoke Fill' },
                { key: 'boxed', label: 'Boxed' },
                { key: 'neon_glow', label: 'Neon Glow' },
                { key: 'minimal_luxury', label: 'Minimal Luxury' },
                { key: 'cyber_glitch', label: 'Cyber Glitch' },
                { key: 'editorial_serif', label: 'Editorial Serif' },
                { key: 'fire_punch', label: 'Fire Punch' },
                { key: 'sunset_warmth', label: 'Sunset Warmth' },
                { key: 'ocean_breeze', label: 'Ocean Breeze' },
                { key: 'monochrome_chic', label: 'Monochrome Chic' },
                { key: 'retro_arcade', label: 'Retro Arcade' },
                { key: 'podcast_subtle', label: 'Podcast Subtle' },
                { key: 'headline_impact', label: 'Headline Impact' },
                { key: 'midnight_blue', label: 'Midnight Blue' },
                { key: 'pastel_dream', label: 'Pastel Dream' },
                { key: 'crimson_shadow', label: 'Crimson Shadow' },
                { key: 'emerald_elite', label: 'Emerald Elite' },
                { key: 'golden_hour', label: 'Golden Hour' },
                { key: 'comic_action', label: 'Comic Action' },
                { key: 'tech_clean', label: 'Tech Clean' },
                { key: 'slate_modern', label: 'Slate Modern' },
              ]).map((st) => (
                <option key={st.key} value={st.key}>
                  {st.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-4 mt-3">
          {[
            { key: 'classic_professional', label: 'Classic Professional', tag: 'Default', desc: 'Minimal clean Inter typography with subtle word pop.' },
            { key: 'rich_dynamic', label: 'Rich Dynamic', tag: 'Kinetic', desc: 'Anton with 116% scale pop, hook punch & climax pop.' },
            { key: 'minimal_luxury', label: 'Minimal Luxury', tag: 'Luxury', desc: 'Italic serif with champagne & gold active accents.' },
            { key: 'neon_glow', label: 'Neon Glow', tag: 'Cyberpunk', desc: 'Electric cyan and neon magenta active highlights.' },
          ].map((item) => (
            <div
              key={item.key}
              onClick={() => updateCaptionStyle(item.key)}
              className={`p-3 rounded-lg border cursor-pointer transition-all flex flex-col justify-between ${
                captionStyle === item.key
                  ? 'border-sodium-500 bg-sodium-500/10 shadow-sm shadow-sodium-500/10'
                  : 'border-ink-800 bg-ink-950/50 hover:border-ink-700'
              }`}
            >
              <div>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-ink-100">{item.label}</span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-ink-800 text-sodium-400">
                    {item.tag}
                  </span>
                </div>
                <p className="text-[11px] text-ink-400 mt-1.5 leading-relaxed">{item.desc}</p>
              </div>
              <div className="mt-2 text-[10px] font-mono text-ink-500 flex items-center gap-1">
                <span className={captionStyle === item.key ? 'text-sodium-400' : 'text-ink-500'}>
                  {captionStyle === item.key ? '● Active' : '○ Select'}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* VISUAL FILTER SYSTEM (20+ FILTERS) */}
      <div className="mt-6 rounded-lg border border-ink-800 bg-ink-900/60 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 mb-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400">
                VISUAL FILTER SYSTEM
              </span>
              <span className="text-[10px] rounded bg-purple-500/10 text-purple-400 px-2 py-0.5 border border-purple-500/20 font-medium">
                20+ Grade Profiles
              </span>
            </div>
            <p className="text-xs text-ink-400 mt-1">
              Applied in FFmpeg before captions without modifying source media. Burned subtitles remain crisp, vibrant, and clean.
            </p>
          </div>
          <div className="flex items-center gap-2 mt-2 sm:mt-0">
            <label className="text-xs text-ink-300 font-medium whitespace-nowrap">Filter:</label>
            <select
              value={visualFilter}
              onChange={(e) => updateVisualFilter(e.target.value)}
              className="bg-ink-950 border border-ink-700 text-ink-100 text-xs rounded px-2.5 py-1.5 focus:border-sodium-500"
            >
              {(visualFiltersList.length > 0 ? visualFiltersList : [
                { id: 'original', name: 'Original', description: 'Source colors without grading' },
                { id: 'black_and_white', name: 'Black & White', description: 'Balanced B&W' },
                { id: 'grayscale', name: 'Grayscale', description: 'Linear luminance' },
                { id: 'vintage', name: 'Vintage', description: 'Warm nostalgic tones' },
                { id: 'warm', name: 'Warm', description: 'Golden sun warm tone' },
                { id: 'cool', name: 'Cool', description: 'Crisp modern bluish tone' },
                { id: 'high_contrast', name: 'High Contrast', description: 'Punchy shadows' },
                { id: 'low_contrast', name: 'Low Contrast', description: 'Soft cinematic shadows' },
                { id: 'cinematic', name: 'Cinematic', description: 'Teal & orange film grade' },
                { id: 'faded', name: 'Faded', description: 'Lifted shadows matte look' },
                { id: 'sepia', name: 'Sepia', description: 'Antique monochrome' },
                { id: 'noir', name: 'Noir', description: 'Crushed blacks dramatic monochrome' },
                { id: 'bright', name: 'Bright', description: 'High-key bright lighting boost' },
                { id: 'dark', name: 'Dark', description: 'Low-key moody shadow depth' },
                { id: 'muted', name: 'Muted', description: 'Subdued desaturated palette' },
                { id: 'sharp', name: 'Sharp', description: 'Enhanced edge crispness' },
                { id: 'soft', name: 'Soft', description: 'Dreamy soft diffusion glow' },
                { id: 'retro', name: 'Retro', description: 'Saturated 80s videotape' },
                { id: 'film', name: 'Film', description: 'Analog 35mm grain emulation' },
                { id: 'monochrome', name: 'Monochrome', description: 'Contemporary studio B&W' },
              ]).map((vf) => (
                <option key={vf.id} value={vf.id}>
                  {vf.name} - {vf.description}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-4 mt-3">
          {[
            { id: 'original', name: 'Original', tag: 'Natural', desc: 'Pure source colors without color grading.' },
            { id: 'cinematic', name: 'Cinematic', tag: 'Teal/Orange', desc: 'Rich cinematic contrast with stylized shadows.' },
            { id: 'vintage', name: 'Vintage', tag: 'Nostalgic', desc: 'Warm 70s film curve with mellow highlights.' },
            { id: 'noir', name: 'Noir', tag: 'Monochrome', desc: 'Dramatic deep blacks and high-contrast punch.' },
          ].map((item) => (
            <div
              key={item.id}
              onClick={() => updateVisualFilter(item.id)}
              className={`p-3 rounded-lg border cursor-pointer transition-all flex flex-col justify-between ${
                visualFilter === item.id
                  ? 'border-sodium-500 bg-sodium-500/10 shadow-sm shadow-sodium-500/10'
                  : 'border-ink-800 bg-ink-950/50 hover:border-ink-700'
              }`}
            >
              <div>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-xs text-ink-100">{item.name}</span>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-ink-800 text-purple-400">
                    {item.tag}
                  </span>
                </div>
                <p className="text-[11px] text-ink-400 mt-1.5 leading-relaxed">{item.desc}</p>
              </div>
              <div className="mt-2 text-[10px] font-mono text-ink-500 flex items-center gap-1">
                <span className={visualFilter === item.id ? 'text-sodium-400' : 'text-ink-500'}>
                  {visualFilter === item.id ? '● Active' : '○ Select'}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Hidden audio element for preview playback */}
      <audio
        ref={audioPreviewRef}
        onEnded={() => setPlayingAudioId(null)}
        className="hidden"
      />

      {/* STEP 20: Operator-Selected Campaign Background Music (BGM Vault) */}
      <div className="mt-6 rounded-lg border border-ink-800 bg-ink-900/60 p-5">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400">
                CAMPAIGN BACKGROUND MUSIC (BGM VAULT)
              </span>
              <span className="text-[10px] rounded bg-blue-500/10 text-blue-400 px-2 py-0.5 border border-blue-500/20 font-medium">
                Step 20 Operator Choice
              </span>
            </div>
            <p className="text-xs text-ink-400 mt-1">
              Select one soundtrack from the BGM Vault for this campaign, or choose No BGM. AL AMR auto-normalizes loudness to -14.0 LUFS with -1.5 dBTP and 16 dB sidechain voice ducking.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setBgmVaultOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium bg-ink-800 hover:bg-ink-700 text-ink-200 border border-ink-700 transition-colors"
          >
            <span>🎵</span>
            <span>Manage BGM Vault ({bgmAssets.length})</span>
          </button>
        </div>

        {/* Selected BGM Indicator */}
        <div className="grid gap-3 sm:grid-cols-3 mt-4">
          {/* Card 1: Default BGM (Canonical Auto) */}
          <label
            className={`flex flex-col justify-between p-3.5 rounded-lg border cursor-pointer transition-all ${
              selectedBgmId === ''
                ? 'border-sodium-500 bg-sodium-500/10 shadow-sm shadow-sodium-500/10'
                : 'border-ink-800 bg-ink-950/50 hover:border-ink-700'
            }`}
          >
            <div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="bgm_selection"
                    value=""
                    checked={selectedBgmId === ''}
                    onChange={() => updateSelectedBgmId('')}
                    className="text-sodium-500 focus:ring-sodium-500"
                  />
                  <span className="font-semibold text-sm text-ink-100">Default BGM</span>
                </div>
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-sodium-500/20 text-sodium-300 border border-sodium-500/30">
                  Default
                </span>
              </div>
              <p className="text-xs text-ink-400 mt-2">
                Auto-mixes canonical soundtrack with 16 dB voice ducking and -14.0 LUFS master normalization.
              </p>
            </div>
            <div className="mt-3 pt-2 border-t border-ink-800/60 text-[11px] text-sodium-400 font-mono">
              Canonical Audio · -14 LUFS
            </div>
          </label>

          {/* Card 2: No BGM (Voice Only) */}
          <label
            className={`flex flex-col justify-between p-3.5 rounded-lg border cursor-pointer transition-all ${
              selectedBgmId === 'none'
                ? 'border-sodium-500 bg-sodium-500/10 shadow-sm shadow-sodium-500/10'
                : 'border-ink-800 bg-ink-950/50 hover:border-ink-700'
            }`}
          >
            <div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="bgm_selection"
                    value="none"
                    checked={selectedBgmId === 'none'}
                    onChange={() => updateSelectedBgmId('none')}
                    className="text-sodium-500 focus:ring-sodium-500"
                  />
                  <span className="font-semibold text-sm text-ink-100">No BGM</span>
                </div>
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-ink-800 text-ink-400">
                  Voice Only
                </span>
              </div>
              <p className="text-xs text-ink-400 mt-2">
                Clips export with original speaker audio only. Zero background soundtrack.
              </p>
            </div>
            <div className="mt-3 pt-2 border-t border-ink-800/60 text-[11px] text-ink-500">
              Clean speech master
            </div>
          </label>

          {/* Enabled BGM Assets */}
          {bgmAssets
            .filter((a) => a.enabled)
            .map((asset) => (
              <label
                key={asset.id}
                className={`flex flex-col justify-between p-3.5 rounded-lg border cursor-pointer transition-all ${
                  selectedBgmId === asset.id
                    ? 'border-sodium-500 bg-sodium-500/10 shadow-sm shadow-sodium-500/10'
                    : 'border-ink-800 bg-ink-950/50 hover:border-ink-700'
                }`}
              >
                <div>
                  <div className="flex items-center justify-between gap-1">
                    <div className="flex items-center gap-2 truncate">
                      <input
                        type="radio"
                        name="bgm_selection"
                        value={asset.id}
                        checked={selectedBgmId === asset.id}
                        onChange={() => updateSelectedBgmId(asset.id)}
                        className="text-sodium-500 focus:ring-sodium-500"
                      />
                      <span className="font-semibold text-sm text-ink-100 truncate" title={asset.name}>
                        {asset.name}
                      </span>
                    </div>
                    {asset.genre && (
                      <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 whitespace-nowrap">
                        {asset.genre}
                      </span>
                    )}
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1 text-[10px] text-ink-400">
                    {asset.mood && (
                      <span className="px-1.5 py-0.2 rounded bg-ink-800 text-ink-300">
                        {asset.mood}
                      </span>
                    )}
                    {asset.tags?.slice(0, 3).map((t) => (
                      <span key={t} className="px-1.5 py-0.2 rounded bg-ink-850 text-ink-400">
                        #{t}
                      </span>
                    ))}
                  </div>
                </div>

                <div className="mt-3 pt-2 border-t border-ink-800/60 flex items-center justify-between text-[11px] text-ink-400">
                  <span>{formatDuration(asset.duration_s)}</span>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault()
                      e.stopPropagation()
                      toggleAudioPreview(asset.id)
                    }}
                    className={`px-2 py-0.5 rounded text-[11px] font-medium border transition-colors ${
                      playingAudioId === asset.id
                        ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                        : 'bg-ink-800 hover:bg-ink-700 text-ink-300 border-ink-700'
                    }`}
                  >
                    {playingAudioId === asset.id ? '⏸ Pause' : '▶ Preview'}
                  </button>
                </div>
              </label>
            ))}
        </div>

        {bgmAssets.filter((a) => a.enabled).length === 0 && (
          <div className="mt-3 p-3 rounded border border-dashed border-ink-800 bg-ink-950/30 text-center text-xs text-ink-500">
            No BGM tracks in vault yet. Click &ldquo;Manage BGM Vault&rdquo; above to upload MP3, WAV, or M4A music files.
          </div>
        )}
      </div>

      {/* Autonomous Launch Action Button */}
      <div className="mt-6 flex flex-col sm:flex-row items-center justify-between gap-4 rounded-lg border border-ink-800 bg-ink-900/40 p-5">
        <div>
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-signal-good animate-pulse"></span>
            <span className="text-sm font-medium text-ink-100">
              Autonomous Production Engine Ready
            </span>
          </div>
          <p className="text-xs text-ink-400 mt-1">
            Cloud worker handles Whisper audio extraction, MediaPipe face tracking, 9:16 reframe, and publication.
          </p>
          <p className="text-[11px] text-sodium-400/90 mt-1 flex items-center gap-1.5">
            <span>🛡️</span>
            <span><strong>Client Disconnect Safe:</strong> Jobs run autonomously on cloud runners. You may safely close this tab at any time.</span>
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

      {/* Step 20 BGM Vault Management Modal */}
      {bgmVaultOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm">
          <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl border border-ink-700 bg-ink-900 p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-ink-800 pb-4">
              <div>
                <h3 className="text-base font-bold text-ink-100 flex items-center gap-2">
                  <span>🎵</span>
                  <span>BGM Vault Manager</span>
                </h3>
                <p className="text-xs text-ink-400 mt-0.5">
                  Upload, tag, preview, and organize background music tracks for campaigns.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setBgmVaultOpen(false)
                  if (audioPreviewRef.current) audioPreviewRef.current.pause()
                  setPlayingAudioId(null)
                }}
                className="text-ink-400 hover:text-ink-200 text-lg p-1"
              >
                ✕
              </button>
            </div>

            {/* Upload Form */}
            <form onSubmit={handleUploadBgm} className="mt-4 p-4 rounded-lg border border-ink-800 bg-ink-950/60 space-y-3">
              <span className="text-xs font-semibold uppercase tracking-wider text-sodium-400 block">
                Upload New BGM Track
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                <div>
                  <label className="block text-ink-400 mb-1">Audio File (MP3, WAV, M4A, AAC, FLAC):</label>
                  <input
                    ref={bgmFileInputRef}
                    type="file"
                    accept=".mp3,.wav,.m4a,.aac,.flac,audio/*"
                    onChange={(e) => {
                      const file = e.target.files?.[0] || null
                      setBgmUploadFile(file)
                      if (file && !bgmUploadName) {
                        setBgmUploadName(file.name.replace(/\.[^/.]+$/, ''))
                      }
                    }}
                    className="w-full text-xs text-ink-300 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:bg-sodium-500/20 file:text-sodium-300 hover:file:bg-sodium-500/30"
                  />
                </div>
                <div>
                  <label className="block text-ink-400 mb-1">Track Name / Title:</label>
                  <input
                    type="text"
                    value={bgmUploadName}
                    onChange={(e) => setBgmUploadName(e.target.value)}
                    placeholder="e.g. Inspiring Piano Hook"
                    className="field w-full text-xs"
                  />
                </div>
                <div>
                  <label className="block text-ink-400 mb-1">Genre / Category:</label>
                  <input
                    type="text"
                    value={bgmUploadGenre}
                    onChange={(e) => setBgmUploadGenre(e.target.value)}
                    placeholder="e.g. Motivational, Action, Podcast"
                    className="field w-full text-xs"
                  />
                </div>
                <div>
                  <label className="block text-ink-400 mb-1">Mood (Optional):</label>
                  <input
                    type="text"
                    value={bgmUploadMood}
                    onChange={(e) => setBgmUploadMood(e.target.value)}
                    placeholder="e.g. Uplifting, Dramatic, Minimal"
                    className="field w-full text-xs"
                  />
                </div>
                <div className="sm:col-span-2">
                  <label className="block text-ink-400 mb-1">Tags (Comma-separated):</label>
                  <input
                    type="text"
                    value={bgmUploadTags}
                    onChange={(e) => setBgmUploadTags(e.target.value)}
                    placeholder="e.g. energetic, speech, fast, drums"
                    className="field w-full text-xs"
                  />
                </div>
              </div>
              <div className="flex justify-end pt-2">
                <button
                  type="submit"
                  disabled={!bgmUploadFile || bgmUploading}
                  className="px-4 py-1.5 rounded text-xs font-semibold bg-sodium-500 text-ink-950 hover:bg-sodium-400 disabled:opacity-50 transition-colors"
                >
                  {bgmUploading ? 'Validating & Uploading…' : 'Upload to Vault'}
                </button>
              </div>
            </form>

            {/* List of Vault Tracks */}
            <div className="mt-5 space-y-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-ink-400 block">
                Vault Inventory ({bgmAssets.length} Tracks)
              </span>

              {bgmAssets.length === 0 ? (
                <div className="p-4 text-center text-xs text-ink-500 rounded border border-ink-800 bg-ink-950/30">
                  Vault is empty. Upload your first audio track above.
                </div>
              ) : (
                <div className="divide-y divide-ink-800 rounded-lg border border-ink-800 bg-ink-950/40">
                  {bgmAssets.map((asset) => (
                    <div key={asset.id} className="p-3 flex items-center justify-between gap-3 text-xs">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-ink-100 truncate">{asset.name}</span>
                          {asset.genre && (
                            <span className="px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 text-[10px] font-mono border border-blue-500/20">
                              {asset.genre}
                            </span>
                          )}
                          {!asset.enabled && (
                            <span className="px-1.5 py-0.5 rounded bg-ink-800 text-ink-500 text-[10px]">
                              Disabled
                            </span>
                          )}
                        </div>
                        <div className="mt-1 flex items-center gap-3 text-[11px] text-ink-400">
                          <span>{formatDuration(asset.duration_s)}</span>
                          <span>{formatBytes(asset.file_size_bytes)}</span>
                          {asset.mood && <span>Mood: {asset.mood}</span>}
                          {asset.tags?.length > 0 && <span>Tags: {asset.tags.join(', ')}</span>}
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => toggleAudioPreview(asset.id)}
                          className={`px-2.5 py-1 rounded text-xs border font-medium transition-colors ${
                            playingAudioId === asset.id
                              ? 'bg-amber-500/20 text-amber-300 border-amber-500/40'
                              : 'bg-ink-800 hover:bg-ink-700 text-ink-300 border-ink-700'
                          }`}
                        >
                          {playingAudioId === asset.id ? '⏸ Pause' : '▶ Play'}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleToggleBgmEnabled(asset)}
                          className={`px-2.5 py-1 rounded text-xs border font-medium transition-colors ${
                            asset.enabled
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                              : 'bg-ink-800 text-ink-500 border-ink-700'
                          }`}
                        >
                          {asset.enabled ? 'Enabled' : 'Disabled'}
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteBgm(asset.id)}
                          className="px-2 py-1 rounded text-xs text-rose-400 hover:bg-rose-500/10 transition-colors"
                          title="Delete track"
                        >
                          🗑
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
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
