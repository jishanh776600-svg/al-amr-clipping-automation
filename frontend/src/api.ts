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

export type JobStatus =
  | 'queued'
  | 'dispatching'
  | 'running'
  | 'processing'
  | 'uploading'
  | 'publishing'
  | 'done'
  | 'failed'
  | 'cancel_requested'
  | 'cancelled'

export interface Job {
  id: string
  source_id: string
  status: JobStatus
  current_stage: string
  progress: number
  error: string | null
  provider: string
  dispatch_mode?: string
  github_run_id?: string | null
  github_run_url?: string | null
  github_workflow?: string | null
  attempt?: number
  max_attempts?: number
  last_heartbeat_at?: string | null
  stale_at?: string | null
  dispatched_at?: string | null
  started_at: string | null
  completed_at?: string | null
  failed_at?: string | null
  cancelled_at?: string | null
  cancel_requested_at?: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
  source: Source | null
  settings?: Record<string, any>
  guideline?: CampaignGuideline | null
  campaign_spec_id?: string | null
  campaign_spec?: CampaignSpecification | null
}

export interface RequirementItem<T = any> {
  value: T
  confidence: 'explicit' | 'inferred'
  source_doc_id: string
  source_filename: string
  source_type: string
  snippet: string
  weight: number
}

export interface IngestedDocument {
  doc_id: string
  source_type: string
  filename: string
  source_url?: string | null
  sha256?: string | null
  size_bytes: number
  word_count: number
  char_count: number
  status: 'extracted' | 'failed'
  error?: string | null
  warning?: string | null
  extracted_at: string
}

export interface CampaignConflict {
  id: string
  rule_category: string
  severity: 'critical' | 'warning' | 'info'
  document_a: Record<string, any>
  document_b: Record<string, any>
  description: string
  resolution_status: 'unresolved' | 'superseded' | 'manual_override'
  resolution_notes?: string | null
}

export interface CampaignSpecification {
  campaign_id: string
  title: string
  description?: string
  objective?: string
  created_at: string
  campaign_url?: string | null
  documents: IngestedDocument[]
  desired_topics: RequirementItem<string>[]
  preferred_speakers: RequirementItem<string>[]
  required_themes: RequirementItem<string>[]
  banned_topics: RequirementItem<string>[]
  banned_words: RequirementItem<string>[]
  duration_min_s: RequirementItem<number>
  duration_max_s: RequirementItem<number>
  duration_preferred_s: RequirementItem<number | null>
  output_count: RequirementItem<number>
  aspect_ratio: RequirementItem<string>
  hook_required: RequirementItem<boolean>
  hook_window_s: RequirementItem<number>
  hook_min_score: RequirementItem<number>
  hook_types: RequirementItem<string>[]
  hook_instructions: RequirementItem<string>[]
  cta_required: RequirementItem<boolean>
  cta_types: RequirementItem<string>[]
  cta_window_s: RequirementItem<number>
  cta_instructions: RequirementItem<string>[]
  tone: RequirementItem<string>
  pacing: RequirementItem<string>
  caption_preset: RequirementItem<string>
  caption_instructions: RequirementItem<string>[]
  visual_instructions: RequirementItem<string>[]
  branding_rules: RequirementItem<string>[]
  title_patterns: RequirementItem<string>[]
  description_guidelines: RequirementItem<string>[]
  hashtags: RequirementItem<string>[]
  keywords: RequirementItem<string>[]
  required_mentions: RequirementItem<string>[]
  platforms: string[]
  platform_rules: Record<string, RequirementItem<string>[]>
  max_silence_s: RequirementItem<number>
  min_speech_density: RequirementItem<number>
  conflicts: CampaignConflict[]
  warnings: string[]
  has_critical_conflicts: boolean
}

export interface CampaignUrlExtractOut {
  url: string
  title: string
  description: string
  headings: string[]
  raw_text: string
  status: string
  error?: string | null
  word_count: number
  char_count: number
}

export interface CampaignGuideline {
  id: string
  job_id?: string | null
  filename: string
  mime_type: string
  size_bytes: number
  source_type?: string
  drive_file_id?: string | null
  sha256?: string | null
  word_count?: number
  char_count?: number
  extracted_text: string
  parsed_brief: Record<string, any>
  status: string
  error?: string | null
  created_at: string
}

export interface ClipCandidate {
  id: string
  job_id: string
  rank: number
  selected: boolean
  status: 'discovered' | 'scored' | 'selected' | 'rejected'
  start_s: number
  end_s: number
  duration_s: number
  start_word: number
  end_word: number
  title: string
  hook_text: string
  reason: string
  transcript_slice: string
  score: number
  score_breakdown: Record<string, any>
  hook_signals: Record<string, any>
  climax_signals: Record<string, any>
  cta_signals: Record<string, any>
  requirement_matches: Array<Record<string, any>>
  rejection_reasons: string[]
  created_at: string
  updated_at: string
}

export interface ClipSpecification {
  id: string
  job_id: string
  candidate_id: string
  source_id: string
  start_time: number
  end_time: number
  duration: number
  start_word: number
  end_word: number
  hook_start?: number | null
  hook_end?: number | null
  hook_type: string
  climax_start?: number | null
  climax_end?: number | null
  cta_start?: number | null
  cta_end?: number | null
  boundary_adjustments: Record<string, any>
  requirement_matches: Array<Record<string, any>>
  quality_score: number
  quality_status: 'QUALITY_PASS' | 'QUALITY_WARN' | 'QUALITY_REJECT'
  rejection_reasons: string[]
  warnings: string[]
  final_rank: number
  version: number
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export interface VisualComposition {
  id: string
  clip_id: string
  job_id: string
  source_width: number
  source_height: number
  output_width: number
  output_height: number
  crop_strategy: string
  tracking_strategy: string
  tracking_confidence: number
  camera_movement_score: number
  smoothing_parameters: Record<string, any>
  fallback_used: boolean
  fallback_reason: string
  quality_score: number
  quality_status: 'VISUAL_PASS' | 'VISUAL_WARN' | 'VISUAL_REJECT'
  warnings: string[]
  rejection_reasons: string[]
  version: number
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export interface RetentionOptimization {
  id: string
  clip_id: string
  job_id: string
  retention_score: number
  final_score: number
  quality_status: 'FINAL_PASS' | 'FINAL_WARN' | 'FINAL_REJECT'
  hook_strength: number
  speech_density_wps: number
  dead_air_percentage: number
  pacing_score: number
  narrative_score: number
  editing_decisions: Record<string, any>
  visual_emphasis: Array<Record<string, any>>
  scoring_breakdown: Record<string, any>
  rejection_reasons: string[]
  warnings: string[]
  processing_time_s: number
  version: number
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export interface CaptionOptimization {
  id: string
  clip_id: string
  job_id: string
  style_key: string
  style_label: string
  caption_segments: Array<Record<string, any>>
  emphasis_metadata: Record<string, any>
  hook_treatment: Record<string, any>
  climax_treatment: Record<string, any>
  cta_treatment: Record<string, any>
  quality_score: number
  quality_status: 'CAPTION_PASS' | 'CAPTION_WARN' | 'CAPTION_REJECT'
  rejection_reasons: string[]
  warnings: string[]
  fallback_used: boolean
  fallback_reason: string
  render_time_s: number
  version: number
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export interface BGMAsset {
  id: string
  name: string
  genre: string
  mood: string
  tags: string[]
  mime_type: string
  duration_s: number
  file_size_bytes: number
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface BGMMix {
  id: string
  clip_id: string
  job_id: string
  bgm_asset_id?: string | null
  bgm_asset_name: string
  bgm_applied: boolean
  clip_duration_s: number
  bgm_duration_s: number
  loop_trim_decision: string
  ducking_applied: boolean
  ducking_parameters: Record<string, any>
  normalization_applied: boolean
  integrated_lufs: number
  true_peak_db: number
  quality_score: number
  quality_status: 'MIX_PASS' | 'MIX_WARN' | 'MIX_REJECT'
  warnings: string[]
  rejection_reasons: string[]
  processing_time_s: number
  mixed_audio_path: string
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export interface FinalRender {
  id: string
  job_id: string
  clip_id: string
  output_path: string
  package_dir: string
  duration: number
  width: number
  height: number
  fps: number
  video_codec: string
  audio_codec: string
  caption_style: string
  bgm_asset_id?: string | null
  quality_score: number
  quality_status: 'RENDER_PASS' | 'RENDER_WARN' | 'RENDER_REJECT'
  render_status: 'completed' | 'failed'
  render_attempt: number
  error_details: string[]
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export interface ClipMetadata {
  id: string
  job_id: string
  clip_id: string
  generated_title: string
  final_title: string
  generated_description: string
  final_description: string
  generated_hashtags: string[]
  final_hashtags: string[]
  generated_mentions: string[]
  final_mentions: string[]
  generated_cta: string
  final_cta: string
  campaign_requirements_matched: Record<string, any>
  compliance_status: 'SEO_PASS' | 'SEO_WARN' | 'SEO_REJECT'
  compliance_score: number
  validation_errors: string[]
  validation_warnings: string[]
  version: number
  is_publish_ready: boolean
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export type ApprovalStatus =
  | 'PENDING_REVIEW'
  | 'APPROVED'
  | 'REJECTED'
  | 'CHANGES_REQUESTED'
  | 'PUBLISHING_LOCKED'

export interface ClipApprovalHistoryEntry {
  from_status: string
  to_status: string
  operator_action: string
  operator_note: string
  actor: string
  version: number
  timestamp: string
}

export interface ClipApproval {
  id: string
  job_id: string
  clip_id: string
  current_status: ApprovalStatus
  operator_action: string | null
  operator_note: string
  version: number
  previous_status: string | null
  publish_eligible: boolean
  blocking_reasons: string[]
  is_approved_for_publishing: boolean
  history: ClipApprovalHistoryEntry[]
  telemetry: Record<string, any>
  created_at: string
  updated_at: string
}

export interface ClipApprovalTelemetry {
  total_eligible: number
  pending: number
  approved: number
  rejected: number
  changes_requested: number
  publishing_locked: number
  publish_ready: number
}

export type PublicationStatus =
  | 'PENDING'
  | 'UPLOADING'
  | 'PUBLISHED'
  | 'FAILED_RETRYABLE'
  | 'FAILED_PERMANENT'
  | 'SKIPPED'
  | 'CANCELLED'

export interface Publication {
  id: string
  job_id: string
  clip_id: string
  platform: string
  destination_id: string
  account_id: string
  status: PublicationStatus
  attempt_number: number
  idempotency_key: string
  final_render_id?: string | null
  remote_media_id?: string | null
  remote_post_id?: string | null
  permalink?: string | null
  upload_started_at?: string | null
  upload_completed_at?: string | null
  published_at?: string | null
  error_code?: string | null
  error_message?: string | null
  response_metadata: Record<string, any>
  retry_count: number
  version: number
  telemetry: Record<string, any>
  is_published: boolean
  is_retryable: boolean
  created_at: string
  updated_at: string
}

export interface PublishingTelemetry {
  total_destinations: number
  published: number
  failed: number
  retryable_failures: number
  permanent_failures: number
  skipped: number
  total_attempts: number
}

// ---------------------------------------------------------------------------
// Step 26: Publishing Destinations & Queue Types
// ---------------------------------------------------------------------------

export interface Destination {
  id: string
  platform: 'youtube' | 'instagram' | 'telegram'
  display_name: string
  account_identifier: string
  enabled: boolean
  priority: number
  config_metadata: Record<string, any>
  daily_limit: number
  spacing_seconds: number
  created_at: string
  updated_at: string
}

export type QueueStatus =
  | 'QUEUED'
  | 'SCHEDULED'
  | 'CLAIMED'
  | 'PUBLISHING'
  | 'PUBLISHED'
  | 'FAILED_RETRYABLE'
  | 'FAILED_PERMANENT'
  | 'CANCELLED'
  | 'SKIPPED'

export interface QueueItem {
  id: string
  job_id: string
  clip_id: string
  destination_id: string
  destination_name?: string
  platform: string
  scheduled_at: string
  priority: number
  status: QueueStatus
  attempt_count: number
  claimed_by?: string | null
  claimed_at?: string | null
  lease_expires_at?: string | null
  error_message?: string | null
  publication_id?: string | null
  idempotency_key: string
  created_at: string
  updated_at: string
}

export interface QueueTelemetry {
  total: number
  queued: number
  scheduled: number
  claimed: number
  publishing: number
  published: number
  failed_retryable: number
  failed_permanent: number
  failed: number
  cancelled: number
  skipped: number
}



export interface JobManifestClipExport {
  export_id: string
  ratio: string
  style: string
  size_bytes: number
  drive_file_id?: string | null
  drive_storage_key?: string | null
  drive_web_view_link?: string | null
  download_url: string
  stream_url: string
  publishing_records: PublishingRecord[]
}

export interface JobManifestClip {
  clip_id: string
  rank: number
  title: string
  hook: string
  duration_s: number
  start_s: number
  end_s: number
  score: number
  status: string
  campaign_evaluation?: any
  exports: JobManifestClipExport[]
}

export interface JobManifest {
  job_id: string
  status: JobStatus
  current_stage: string
  progress: number
  error: string | null
  dispatch_mode: string
  attempt: number
  max_attempts: number
  github: {
    run_id?: string | null
    workflow?: string | null
    job_id?: string | null
    run_url?: string | null
    run_status?: string | null
    conclusion?: string | null
  }
  timestamps: Record<string, string | null>
  source?: Source | null
  guideline?: Record<string, any> | null
  campaign?: Record<string, any> | null
  clips: JobManifestClip[]
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
    proxy?: string
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
  credentials_status?: Record<string, CredentialStatus>
  github_pat?: string | null
}

export interface CredentialStatus {
  configured: boolean
  masked?: string
  updated_at?: string
}

export interface ValidateSecretResult {
  valid: boolean
  message: string
  username?: string | null
  scopes?: string[]
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
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('alamr:auth-changed', { detail: token }))
    }
  }
}

export function onAuthChange(callback: (token: string) => void): () => void {
  if (typeof window === 'undefined') return () => {}
  const handler = () => callback(getStoredToken())
  window.addEventListener('alamr:auth-changed', handler)
  return () => window.removeEventListener('alamr:auth-changed', handler)
}

export function resolveUrl(path: string): string {
  if (!path) return path
  if (path.startsWith('http://') || path.startsWith('https://')) {
    if (typeof window !== 'undefined' && window.location.protocol === 'https:' && path.startsWith('http://')) {
      return path.replace(/^http:\/\//i, 'https://')
    }
    return path
  }
  const normalized = path.startsWith('/') ? path : `/${path}`
  let base = API_BASE_URL
  if (typeof window !== 'undefined' && window.location.protocol === 'https:' && base.startsWith('http://')) {
    base = base.replace(/^http:\/\//i, 'https://')
  }
  return `${base}${normalized}`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const fullUrl = resolveUrl(path)
  const activeToken = getStoredToken() || API_KEY
  const authHeaders: Record<string, string> = activeToken
    ? { Authorization: `Bearer ${activeToken}`, 'X-API-Key': activeToken }
    : {}

  let response: Response
  try {
    response = await fetch(fullUrl, {
      ...init,
      headers: {
        ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
        ...authHeaders,
        ...init?.headers,
      },
    })
  } catch (err: any) {
    const rawMsg = err?.message || String(err)
    if (
      rawMsg.includes('Failed to fetch') ||
      rawMsg.includes('NetworkError') ||
      rawMsg.includes('Network request failed') ||
      err?.name === 'TypeError'
    ) {
      const isOffline = typeof navigator !== 'undefined' && !navigator.onLine
      const hint = isOffline
        ? 'Your browser appears offline. Check your internet connection.'
        : 'Could not connect to the AL AMR server. Ensure the server is reachable and valid authentication credentials are configured in Settings.'
      throw new ApiError(`Network request failed: ${rawMsg}`, 0, hint)
    }
    throw err
  }

  if (!response.ok) {
    if (response.status === 401 && typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('alamr:auth-unauthorized', { detail: { path, status: 401 } }))
    }
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
  testAuth: async (token: string): Promise<boolean> => {
    const fullUrl = resolveUrl('/api/settings')
    try {
      const resp = await fetch(fullUrl, {
        headers: {
          Authorization: `Bearer ${token}`,
          'X-API-Key': token,
        },
      })
      return resp.ok
    } catch {
      return false
    }
  },
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
  getJobManifest: (id: string) => request<JobManifest>(`/api/jobs/${id}/manifest`),

  uploadGuideline: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<CampaignGuideline>('/api/jobs/guidelines/upload', {
      method: 'POST',
      body: form,
    })
  },

  uploadDriveGuideline: (driveUrl: string) =>
    request<CampaignGuideline>('/api/jobs/guidelines/drive', {
      method: 'POST',
      body: JSON.stringify({ drive_url: driveUrl }),
    }),

  extractCampaignUrl: (url: string) =>
    request<CampaignUrlExtractOut>('/api/campaigns/intelligence/url', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  extractCampaignIntelligence: (payload: FormData | object) => {
    if (payload instanceof FormData) {
      return request<CampaignSpecification>('/api/campaigns/intelligence/extract', {
        method: 'POST',
        body: payload,
      })
    }
    return request<CampaignSpecification>('/api/campaigns/intelligence/extract', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },

  getJobCampaignSpecification: (jobId: string) =>
    request<CampaignSpecification>(`/api/jobs/${jobId}/campaign-specification`),

  createAutonomousJob: (form: FormData) =>
    request<Job>('/api/jobs/create-autonomous', {
      method: 'POST',
      body: form,
    }),

  createJob: (
    sourceId: string,
    settings: JobSettingsOverrides = {},
    campaign?: CampaignBrief | null,
    guidelineId?: string | null,
  ) =>
    request<Job>('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({
        source_id: sourceId,
        settings,
        ...(campaign ? { campaign } : {}),
        ...(guidelineId ? { guideline_id: guidelineId } : {}),
      }),
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

  validateSecret: (key: string, value?: string) =>
    request<ValidateSecretResult>(`/api/settings/secrets/${key}/validate`, {
      method: 'POST',
      body: JSON.stringify(value ? { key, value } : null),
    }),

  getSettingsDiagnostics: () => request<Record<string, any>>('/api/settings/diagnostics'),

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

  getJobCandidates: (jobId: string, selectedOnly: boolean = false) => {
    const q = selectedOnly ? '?selected_only=true' : ''
    return request<ClipCandidate[]>(`/api/jobs/${jobId}/candidates${q}`)
  },

  getJobClipSpecifications: (jobId: string, approvedOnly: boolean = false) => {
    const q = approvedOnly ? '?approved_only=true' : ''
    return request<ClipSpecification[]>(`/api/jobs/${jobId}/clip-specifications${q}`)
  },

  getJobVisualCompositions: (jobId: string, status?: string) => {
    const q = status ? `?status=${encodeURIComponent(status)}` : ''
    return request<VisualComposition[]>(`/api/jobs/${jobId}/visual-compositions${q}`)
  },

  getJobRetentionOptimizations: (jobId: string, approvedOnly: boolean = false, status?: string) => {
    const params = new URLSearchParams()
    if (approvedOnly) params.set('approved_only', 'true')
    if (status) params.set('status', status)
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<RetentionOptimization[]>(`/api/jobs/${jobId}/retention-optimizations${qs}`)
  },

  getJobCaptions: (jobId: string, approvedOnly: boolean = false, status?: string) => {
    const params = new URLSearchParams()
    if (approvedOnly) params.set('approved_only', 'true')
    if (status) params.set('status', status)
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<CaptionOptimization[]>(`/api/jobs/${jobId}/captions${qs}`)
  },

  getJobAudioMixes: (jobId: string, approvedOnly: boolean = false, status?: string) => {
    const params = new URLSearchParams()
    if (approvedOnly) params.set('approved_only', 'true')
    if (status) params.set('status', status)
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<BGMMix[]>(`/api/jobs/${jobId}/audio-mix${qs}`)
  },

  getJobFinalRenders: (jobId: string, approvedOnly: boolean = false, status?: string) => {
    const params = new URLSearchParams()
    if (approvedOnly) params.set('approved_only', 'true')
    if (status) params.set('status', status)
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<FinalRender[]>(`/api/jobs/${jobId}/final-renders${qs}`)
  },

  getJobMetadata: (jobId: string) =>
    request<ClipMetadata[]>(`/api/jobs/${jobId}/metadata`),

  getClipMetadata: (jobId: string, clipId: string) =>
    request<ClipMetadata>(`/api/jobs/${jobId}/clips/${clipId}/metadata`),

  patchClipMetadata: (
    jobId: string,
    clipId: string,
    data: {
      final_title?: string
      final_description?: string
      final_hashtags?: string[]
      final_mentions?: string[]
      final_cta?: string
      action?: string
    }
  ) =>
    request<ClipMetadata>(`/api/jobs/${jobId}/clips/${clipId}/metadata`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),

  listBGMAssets: (enabledOnly: boolean = false, genre?: string) => {
    const params = new URLSearchParams()
    if (enabledOnly) params.set('enabled_only', 'true')
    if (genre) params.set('genre', genre)
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<BGMAsset[]>(`/api/bgm${qs}`)
  },

  uploadBGMAsset: (form: FormData) =>
    request<BGMAsset>('/api/bgm', {
      method: 'POST',
      body: form,
    }),

  getBGMAsset: (id: string) => request<BGMAsset>(`/api/bgm/${id}`),

  updateBGMAsset: (
    id: string,
    patch: {
      name?: string
      genre?: string
      mood?: string
      tags?: string[]
      enabled?: boolean
    }
  ) =>
    request<BGMAsset>(`/api/bgm/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),

  deleteBGMAsset: (id: string) =>
    request<{ status: string; id: string }>(`/api/bgm/${id}`, {
      method: 'DELETE',
    }),

  // Step 24: Clip Approvals
  listJobApprovals: (jobId: string) =>
    request<ClipApproval[]>(`/api/jobs/${jobId}/approvals`),

  getApprovalTelemetry: (jobId: string) =>
    request<ClipApprovalTelemetry>(`/api/jobs/${jobId}/approvals/telemetry`),

  getClipApproval: (jobId: string, clipId: string) =>
    request<ClipApproval>(`/api/jobs/${jobId}/clips/${clipId}/approval`),

  postClipApprovalAction: (
    jobId: string,
    clipId: string,
    data: {
      action: 'APPROVE' | 'REJECT' | 'REQUEST_CHANGES' | 'LOCK'
      operator_note?: string
      expected_version?: number
    }
  ) =>
    request<ClipApproval>(`/api/jobs/${jobId}/clips/${clipId}/approval`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),

  resetClipApproval: (jobId: string, clipId: string) =>
    request<ClipApproval>(`/api/jobs/${jobId}/clips/${clipId}/approval/reset`, {
      method: 'POST',
    }),

  getClipReviewPackage: (jobId: string, clipId: string) =>
    request<Record<string, any>>(`/api/jobs/${jobId}/clips/${clipId}/approval/review-package`),

  // Step 25: Remote Publishing
  getJobPublications: (jobId: string) =>
    request<Publication[]>(`/api/jobs/${jobId}/publications`),

  getJobPublishingTelemetry: (jobId: string) =>
    request<PublishingTelemetry>(`/api/jobs/${jobId}/publications/telemetry`),

  getPublication: (jobId: string, publicationId: string) =>
    request<Publication>(`/api/jobs/${jobId}/publications/${publicationId}`),

  publishClip: (
    jobId: string,
    clipId: string,
    data: {
      platforms: string[]
      destination?: string
      dry_run?: boolean
    }
  ) =>
    request<Publication[]>(`/api/jobs/${jobId}/clips/${clipId}/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),

  publishAllClips: (
    jobId: string,
    clipId: string,
    data: {
      platforms: string[]
      destination?: string
      dry_run?: boolean
    }
  ) =>
    request<Publication[]>(`/api/jobs/${jobId}/clips/${clipId}/publish-all`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),

  retryPublication: (publicationId: string, dryRun: boolean = false) =>
    request<Publication>(`/api/publications/${publicationId}/retry?dry_run=${dryRun}`, {
      method: 'POST',
    }),

  // Step 26: Multi-Account Destinations & Queue Orchestration
  listDestinations: (platform?: string, enabledOnly = false) => {
    const params = new URLSearchParams()
    if (platform) params.set('platform', platform)
    if (enabledOnly) params.set('enabled_only', 'true')
    const qs = params.toString() ? `?${params.toString()}` : ''
    return request<Destination[]>(`/api/publishing/destinations${qs}`)
  },

  createDestination: (dest: Partial<Destination>) =>
    request<Destination>('/api/publishing/destinations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(dest),
    }),

  updateDestination: (id: string, patch: Partial<Destination>) =>
    request<Destination>(`/api/publishing/destinations/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),

  deleteDestination: (id: string) =>
    request<{ ok: boolean }>(`/api/publishing/destinations/${id}`, {
      method: 'DELETE',
    }),

  listQueue: (params?: {
    job_id?: string
    clip_id?: string
    destination_id?: string
    status?: string
    limit?: number
  }) => {
    const sp = new URLSearchParams()
    if (params?.job_id) sp.set('job_id', params.job_id)
    if (params?.clip_id) sp.set('clip_id', params.clip_id)
    if (params?.destination_id) sp.set('destination_id', params.destination_id)
    if (params?.status) sp.set('status', params.status)
    if (params?.limit) sp.set('limit', String(params.limit))
    const qs = sp.toString() ? `?${sp.toString()}` : ''
    return request<QueueItem[]>(`/api/publishing/queue${qs}`)
  },

  getQueueTelemetry: (jobId?: string) => {
    const qs = jobId ? `?job_id=${encodeURIComponent(jobId)}` : ''
    return request<QueueTelemetry>(`/api/publishing/queue/telemetry${qs}`)
  },

  scheduleClipPublication: (
    jobId: string,
    clipId: string,
    data: { destination_id: string; scheduled_at?: string; priority?: number }
  ) =>
    request<QueueItem>(`/api/jobs/${jobId}/clips/${clipId}/schedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),

  cancelQueueItem: (queueId: string, reason?: string) =>
    request<QueueItem>(`/api/publishing/queue/${queueId}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason || 'Cancelled by operator' }),
    }),

  rescheduleQueueItem: (queueId: string, newScheduledAt: string) =>
    request<QueueItem>(`/api/publishing/queue/${queueId}/reschedule`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_scheduled_at: newScheduledAt }),
    }),

  processNextQueueItem: (workerId = 'worker-ui', dryRun = false) =>
    request<QueueItem | null>(`/api/publishing/queue/process-next?worker_id=${workerId}&dry_run=${dryRun}`, {
      method: 'POST',
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
