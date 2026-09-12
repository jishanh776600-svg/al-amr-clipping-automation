package com.alamr.operator.data.model

import com.google.gson.annotations.SerializedName

data class HealthResponse(
    val status: String = "ok",
    val service: String? = null,
    val version: String = ""
)

data class ReadyResponse(
    val status: String = "ready",
    val ready: Boolean = false,
    val checks: Map<String, Any?> = emptyMap()
)

data class SystemStatus(
    val ready: Boolean = false,
    val python_version: String = "",
    val platform: String = "",
    val ffmpeg_version: String? = null,
    val accel: String = "cpu",
    val gpu_name: String? = null,
    val compute_type: String = ""
)

data class Source(
    val id: String = "",
    val type: String = "",
    val title: String = "",
    val url: String? = null,
    val duration_s: Double = 0.0
)

data class Job(
    val id: String = "",
    val source_id: String = "",
    val status: String = "queued",
    val current_stage: String = "",
    val progress: Double = 0.0,
    val error: String? = null,
    val provider: String = "",
    val dispatch_mode: String = "local",
    val github_run_id: String? = null,
    val github_run_url: String? = null,
    val attempt: Int = 1,
    val max_attempts: Int = 3,
    val stale_at: String? = null,
    val created_at: String = "",
    val started_at: String? = null,
    val finished_at: String? = null,
    val source: Source? = null,
    val settings: Map<String, Any?>? = null
)

data class ExportRecord(
    val id: String = "",
    val clip_id: String = "",
    val ratio: String = "9:16",
    val style: String = "bold_pop",
    val size_bytes: Long = 0,
    val download_url: String = "",
    val stream_url: String? = null
)

data class CampaignBrief(
    val campaign_id: String? = null,
    val name: String = "",
    val description: String? = null,
    val topic_context: String? = null,
    val target_audience: String? = null,
    val required_topics: List<String>? = emptyList(),
    val required_concepts: List<String>? = emptyList(),
    val optional_keywords: List<String>? = emptyList(),
    val banned_words: List<String>? = emptyList(),
    val banned_topics: List<String>? = emptyList(),
    val minimum_duration: Double? = 20.0,
    val maximum_duration: Double? = 90.0,
    val hook_required: Boolean = true,
    val output_count: Int? = 5
)

data class CampaignPreset(
    val id: String = "",
    val name: String = "",
    val brief: CampaignBrief? = null,
    val created_at: String = "",
    val updated_at: String = ""
)

data class CampaignEvaluation(
    val approved: Boolean = true,
    val final_score: Double = 0.0,
    val composite_score: Double? = null,
    val hook_score: Double? = null,
    val viral_score: Double? = null,
    val density_score: Double? = null,
    val matched_hooks: List<String>? = emptyList(),
    val hard_failures: List<String>? = emptyList(),
    val reasoning: String? = null
)

data class Clip(
    val id: String = "",
    val job_id: String = "",
    val rank: Int = 0,
    val start_s: Double = 0.0,
    val end_s: Double = 0.0,
    val duration_s: Double = 0.0,
    val title: String = "",
    val hook: String = "",
    val score: Int = 0,
    val reason: String = "",
    val status: String = "candidate",
    val exports: List<ExportRecord> = emptyList(),
    val evaluation: CampaignEvaluation? = null
)

data class CreateJobRequest(
    val source_id: String,
    val settings: Map<String, Any?> = emptyMap()
)

data class IngestYouTubeRequest(
    val url: String,
    val cookies_from_browser: String? = null
)

data class PatchClipRequest(
    val status: String? = null,
    val title: String? = null
)

data class ExportClipRequest(
    val ratio: String = "9:16",
    val caption_style: String = "bold_pop"
)

data class PublishingRecord(
    val id: String = "",
    val export_id: String = "",
    val job_id: String = "",
    val platform: String = "",
    val status: String = "pending",
    val destination: String = "",
    val external_id: String? = null,
    val error: String? = null,
    val metadata: Map<String, Any?> = emptyMap(),
    val created_at: String = "",
    val updated_at: String = ""
)

data class PublishingPlatformInfo(
    val platform: String = "",
    val available: Boolean = true,
    val configured: Boolean = false,
    val details: String = ""
)

data class PublishRequest(
    val platforms: List<String> = listOf("telegram"),
    val title: String? = null,
    val description: String? = null,
    val tags: List<String> = emptyList(),
    val destination: String = "",
    val dry_run: Boolean = false
)

data class CampaignGuidelineResponse(
    val id: String = "",
    val filename: String = "",
    val mime_type: String = "",
    val size_bytes: Long = 0L,
    val status: String = "extracted",
    val parsed_brief: Map<String, Any?>? = null
)

