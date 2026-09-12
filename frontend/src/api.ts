/**
 * Typed client for the AutoClip API.
 *
 * Same-origin throughout: the server hands out the built bundle, so there is no
 * base URL to configure and no CORS to negotiate.
 */

export interface Source {
  id: string
  type: string
  title: string
  url: string | null
  filename: string | null
  channel: string | null
  duration_s: number
  width: number | null
  height: number | null
  fps: number | null
  has_audio: boolean
  has_video: boolean
  created_at: string
}

export type JobStatus = 'queued' | 'running' | 'failed' | 'done' | 'cancelled'

export interface Job {
  id: string
  source_id: string
  status: JobStatus
  current_stage: string
  progress: number
  error: string | null
  provider: string
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  source: Source | null
  settings?: Record<string, any>
}

export interface ExportRecord {
  id: string
  clip_id: string
  ratio: string
  style: string
  size_bytes: number
  created_at: string
  download_url: string
  stream_url?: string
}

export interface PublishingRecord {
  id: string
  export_id: string
  job_id: string
  platform: string
  status: string
  destination: string
  external_id: string | null
  error: string | null
  metadata: Record<string, any>
  created_at: string
  updated_at: string
}

export interface PublishingPlatformInfo {
  platform: string
  available: boolean
  configured: boolean
  details: string
}

export interface PublishRequest {
  platforms: ('telegram' | 'youtube' | 'instagram')[]
  title?: string
  description?: string
  tags?: string[]
  destination?: string
  dry_run?: boolean
}


export interface CampaignBrief {
  campaign_id?: string
  name: string
  description?: string
  topic_context?: string
  target_audience?: string
  required_topics?: string[]
  required_concepts?: string[]
  optional_keywords?: string[]
  banned_words?: string[]
  banned_topics?: string[]
  minimum_duration?: number
  maximum_duration?: number
  preferred_duration?: number
  maximum_candidates?: number
  hook_required?: boolean
  hook_window_seconds?: number
  minimum_hook_score?: number
  hook_types?: string[]
  minimum_viral_score?: number
  cta_required?: boolean
  cta_types?: string[]
  output_count?: number
  aspect_ratio?: '9:16' | '1:1' | '16:9'
}

export interface CampaignPreset {
  id: string
  name: string
  brief: CampaignBrief
  created_at: string
  updated_at: string
}

export interface CampaignEvaluation {
  clip_id?: string
  campaign_id?: string
  approved: boolean
  final_score: number
  composite_score?: number
  hook_score?: number
  cta_score?: number
  viral_score?: number
  density_score?: number
  hard_failures?: string[]
  soft_warnings?: string[]
  rule_results?: Record<string, any>
  matched_hooks?: string[]
  matched_rules?: string[]
  violations?: string[]
  reasoning?: string
  campaign_alignment_score?: number
  viral_potential_score?: number
  hook_rating_score?: number
}

export type ClipStatus = 'candidate' | 'kept' | 'discarded' | 'exported'

export interface Clip {
  id: string
  job_id: string
  rank: number
  start_s: number
  end_s: number
  duration_s: number
  start_word: number
  end_word: number
  title: string
  hook: string
  score: number
  reason: string
  status: ClipStatus
  user_trimmed: boolean
  caption_style: string
  ratio: string
  exports: ExportRecord[]
  evaluation?: CampaignEvaluation | null
}

export interface Word {
  text: string
  start: number
  end: number
  speaker: string | null
}

export interface CropKeyframe {
  t: number
  x: number
  y: number
}

export interface CropSegment {
  start_s: number
  end_s: number
  width: number
  height: number
  keyframes: CropKeyframe[]
  strategy: 'track' | 'wide' | 'general'
  zoom: number
  fit: boolean
}

/** Mirrors autoclip.pipeline.reframe.croppath.CropPath. */
export interface CropPath {
  source_width: number
  source_height: number
  segments: CropSegment[]
}

export interface CaptionStyle {
  key: string
  label: string
  description: string
  preview: {
    font: string
    primary: string
    accent: string | null
    outline: string
    outlineWidth: number
    allCaps: boolean
    sizeRatio: number
    marginRatio: number
    boxed: boolean
    animation: string
    maxWords: number
  }
}

export interface ProviderStatus {
  name: string
  available: boolean
  detail: string
  models: string[]
  requires_key: boolean
  has_key: boolean
}

export interface Settings {
  active_provider: string
  providers: Record<string, { model: string; base_url: string | null }>
  whisper: { model: string; compute_type: string; language: string; diarization: boolean }
  clips: { min_duration_s: number; max_duration_s: number; max_clips: number }
  ingest: {
    ytdlp_format: string
    cookies_from_browser: string
    prefer_youtube_captions: boolean
  }
  export: {
    ratio: string
    caption_style: string
    loudness_lufs: number
    prefer_hardware_encoder: boolean
    crf: number
    write_srt: boolean
  }
  insecure_secret_storage: boolean
  keys_present: Record<string, boolean>
}

export interface SystemStatus {
  ready: boolean
  python_version: string
  platform: string
  ffmpeg_version: string | null
  has_libass: boolean
  nvenc_works: boolean
  accel: string
  gpu_name: string | null
  compute_type: string
  diarization_available: boolean
}

export interface JobSettingsOverrides {
  provider?: string
  whisper_model?: string
  language?: string
  diarization?: boolean
  min_duration_s?: number
  max_duration_s?: number
  max_clips?: number
  caption_style?: string
  ratio?: string
  campaign?: CampaignBrief | null
}

/** An API error carrying the server's message and its actionable hint. */
export class ApiError extends Error {
  hint: string
  status: number

  constructor(message: string, status: number, hint = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.hint = hint
  }
}

export const API_BASE_URL: string = (
  (typeof import.meta !== 'undefined' && (import.meta as any).env && (import.meta as any).env.VITE_API_BASE_URL) || ''
).replace(/\/$/, '')

export const API_KEY: string =
  (typeof import.meta !== 'undefined' && (import.meta as any).env && (import.meta as any).env.VITE_API_KEY) || ''

export function getStoredToken(): string {
  if (typeof localStorage !== 'undefined') {
    return localStorage.getItem('alamr_api_key') || ''
  }
  return ''
}

export function setStoredToken(token: string | null): void {
  if (typeof localStorage !== 'undefined') {
    if (token) {
      localStorage.setItem('alamr_api_key', token)
    } else {
      localStorage.removeItem('alamr_api_key')
    }
  }
}

export function resolveUrl(path: string): string {
  if (!path) return path
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalized}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const fullUrl = resolveUrl(path)
  const activeToken = getStoredToken() || API_KEY
  const authHeaders: Record<string, string> = activeToken
    ? { Authorization: `Bearer ${activeToken}`, 'X-API-Key': activeToken }
    : {}

  const response = await fetch(fullUrl, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...authHeaders,
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let hint = ''
    try {
      const body = await response.json()
      const detail = body.detail
      if (typeof detail === 'string') {
        message = detail
      } else if (detail && typeof detail === 'object') {
        // The ingest endpoints return {message, hint} so the UI can show the
        // fix alongside the failure.
        message = detail.message ?? message
        hint = detail.hint ?? ''
      }
    } catch {
      /* Response had no JSON body; the status line is the best we have. */
    }
    throw new ApiError(message, response.status, hint)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string; version: string }>('/api/health'),
  system: () => request<SystemStatus>('/api/system'),
  fetchModels: () => request<void>('/api/system/models', { method: 'POST' }),

  listSources: () => request<Source[]>('/api/sources'),

  ingestYouTube: (url: string, cookiesFromBrowser?: string) =>
    request<Source>('/api/sources/youtube', {
      method: 'POST',
      body: JSON.stringify({ url, cookies_from_browser: cookiesFromBrowser || null }),
    }),

  uploadSource: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Source>('/api/sources/upload', { method: 'POST', body: form })
  },

  listJobs: (limit = 50) => request<Job[]>(`/api/jobs?limit=${limit}`),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),

  createJob: (sourceId: string, settings: JobSettingsOverrides = {}, campaign?: CampaignBrief | null) =>
    request<Job>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({ source_id: sourceId, settings, ...(campaign ? { campaign } : {}) }),
    }),

  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),

  listClips: (jobId: string) => request<Clip[]>(`/api/jobs/${jobId}/clips`),
  listAllClips: (limit = 50, status?: string) =>
    request<Clip[]>(`/api/clips?limit=${limit}${status ? `&status=${status}` : ''}`),
  getClip: (clipId: string) => request<Clip>(`/api/clips/${clipId}`),

  listCampaigns: () => request<CampaignPreset[]>('/api/campaigns'),
  saveCampaign: (preset: { id?: string; name: string; brief: CampaignBrief }) =>
    request<CampaignPreset>('/api/campaigns', { method: 'POST', body: JSON.stringify(preset) }),
  getCampaign: (id: string) => request<CampaignPreset>(`/api/campaigns/${id}`),
  deleteCampaign: (id: string) => request<void>(`/api/campaigns/${id}`, { method: 'DELETE' }),

  streamExportUrl: (exportId: string) => resolveUrl(`/api/exports/${exportId}/stream`),
  ready: () => request<{ status: string; ready: boolean; checks: Record<string, any> }>('/ready'),

  getCropPath: (clipId: string) => request<CropPath>(`/api/clips/${clipId}/crop-path`),
  getClipWords: (clipId: string) => request<Word[]>(`/api/clips/${clipId}/words`),

  patchClip: (
    clipId: string,
    patch: { start_s?: number; end_s?: number; title?: string; status?: ClipStatus },
  ) => request<Clip>(`/api/clips/${clipId}`, { method: 'PATCH', body: JSON.stringify(patch) }),

  patchCaptions: (
    clipId: string,
    patch: { words?: Word[]; caption_style?: string; ratio?: string },
  ) =>
    request<Clip>(`/api/clips/${clipId}/captions`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  exportClip: (clipId: string, ratio: string, style: string, writeSrt = false) =>
    request<ExportRecord>(`/api/clips/${clipId}/export`, {
      method: 'POST',
      body: JSON.stringify({ ratio, style, write_srt: writeSrt }),
    }),

  captionStyles: () => request<CaptionStyle[]>('/api/caption-styles'),
  providerStatus: () => request<ProviderStatus[]>('/api/providers/status'),

  getSettings: () => request<Settings>('/api/settings'),
  putSettings: (patch: Partial<Settings>) =>
    request<Settings>('/api/settings', { method: 'PUT', body: JSON.stringify(patch) }),

  putSecret: (key: string, value: string) =>
    request<void>('/api/settings/secrets', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),

  deleteSecret: (key: string) =>
    request<void>(`/api/settings/secrets/${key}`, { method: 'DELETE' }),

  mediaUrl: (jobId: string) => `/api/jobs/${jobId}/media`,

  listPublishing: (params?: {
    job_id?: string
    export_id?: string
    platform?: string
    status?: string
    limit?: number
  }) => {
    const sp = new URLSearchParams()
    if (params?.job_id) sp.set('job_id', params.job_id)
    if (params?.export_id) sp.set('export_id', params.export_id)
    if (params?.platform) sp.set('platform', params.platform)
    if (params?.status) sp.set('status', params.status)
    if (params?.limit) sp.set('limit', String(params.limit))
    const query = sp.toString() ? `?${sp.toString()}` : ''
    return request<PublishingRecord[]>(`/api/publishing${query}`)
  },

  getPublishingPlatforms: () =>
    request<PublishingPlatformInfo[]>('/api/publishing/platforms'),

  getPublishingRecord: (id: string) =>
    request<PublishingRecord>(`/api/publishing/${id}`),

  retryPublishing: (id: string) =>
    request<PublishingRecord>(`/api/publishing/${id}/retry`, { method: 'POST' }),

  getJobPublishing: (jobId: string) =>
    request<PublishingRecord[]>(`/api/jobs/${jobId}/publishing`),

  publishExport: (exportId: string, req: PublishRequest) =>
    request<PublishingRecord[]>(`/api/exports/${exportId}/publish`, {
      method: 'POST',
      body: JSON.stringify(req),
    }),
}

/** Format seconds as m:ss, or h:mm:ss past an hour. */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

/** Format seconds as timecode with centiseconds, for trim handles. */
export function formatTimecode(seconds: number): string {
  const total = Math.max(0, seconds)
  const cs = Math.floor((total % 1) * 100)
  const s = Math.floor(total) % 60
  const m = Math.floor(total / 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(m)}:${pad(s)}.${pad(cs)}`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}
