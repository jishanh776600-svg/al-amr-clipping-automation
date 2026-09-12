package com.alamr.operator.data.api

import com.alamr.operator.data.model.*
import retrofit2.Response
import retrofit2.http.*

interface AlAmrApiService {

    @GET("health")
    suspend fun health(): HealthResponse

    @GET("ready")
    suspend fun ready(): ReadyResponse

    @GET("api/system")
    suspend fun system(): SystemStatus

    @GET("api/jobs")
    suspend fun listJobs(@Query("limit") limit: Int = 50): List<Job>

    @GET("api/jobs/{id}")
    suspend fun getJob(@Path("id") id: String): Job

    @POST("api/sources/youtube")
    suspend fun ingestYouTube(@Body request: IngestYouTubeRequest): Source

    @POST("api/jobs")
    suspend fun createJob(
        @Query("source_id") sourceId: String,
        @Body settings: Map<String, Any?> = emptyMap()
    ): Job

    @POST("api/jobs/{id}/cancel")
    suspend fun cancelJob(@Path("id") id: String): Job

    @POST("api/jobs/{id}/retry")
    suspend fun retryJob(@Path("id") id: String): Job

    @GET("api/jobs/{id}/clips")
    suspend fun listJobClips(@Path("id") id: String): List<Clip>

    @GET("api/clips")
    suspend fun listAllClips(
        @Query("limit") limit: Int = 100,
        @Query("status") status: String? = null
    ): List<Clip>

    @GET("api/clips/{id}")
    suspend fun getClip(@Path("id") id: String): Clip

    @PATCH("api/clips/{id}")
    suspend fun patchClip(
        @Path("id") id: String,
        @Body patch: PatchClipRequest
    ): Clip

    @POST("api/clips/{id}/export")
    suspend fun exportClip(
        @Path("id") id: String,
        @Query("ratio") ratio: String = "9:16",
        @Query("caption_style") captionStyle: String = "bold_pop"
    ): ExportRecord

    @GET("api/campaigns")
    suspend fun listCampaigns(): List<CampaignPreset>

    @POST("api/campaigns")
    suspend fun saveCampaign(@Body preset: CampaignPreset): CampaignPreset

    @DELETE("api/campaigns/{id}")
    suspend fun deleteCampaign(@Path("id") id: String): Response<Unit>

    @GET("api/publishing")
    suspend fun listPublishing(
        @Query("job_id") jobId: String? = null,
        @Query("export_id") exportId: String? = null,
        @Query("platform") platform: String? = null,
        @Query("status") status: String? = null,
        @Query("limit") limit: Int = 50
    ): List<PublishingRecord>

    @GET("api/publishing/platforms")
    suspend fun getPublishingPlatforms(): List<PublishingPlatformInfo>

    @POST("api/exports/{export_id}/publish")
    suspend fun publishExport(
        @Path("export_id") exportId: String,
        @Body request: PublishRequest
    ): List<PublishingRecord>

    @POST("api/publishing/{id}/retry")
    suspend fun retryPublishing(
        @Path("id") id: String
    ): PublishingRecord
}
