package com.alamr.operator.data.api

import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {
    private var currentBaseUrl: String = "http://10.0.2.2:8000/"
    private var currentApiKey: String? = null

    private var apiServiceInstance: AlAmrApiService? = null

    private val authInterceptor = Interceptor { chain ->
        val original = chain.request()
        val builder = original.newBuilder()

        currentApiKey?.takeIf { it.isNotBlank() }?.let { key ->
            builder.addHeader("Authorization", "Bearer $key")
            builder.addHeader("X-API-Key", key)
        }

        chain.proceed(builder.build())
    }

    private val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BASIC
            })
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    fun configure(baseUrl: String, apiKey: String?) {
        try {
            var url = baseUrl.trim()
            if (url.isBlank()) {
                url = "https://al-amr-clipping-automation.onrender.com"
            }
            if (!url.startsWith("http://") && !url.startsWith("https://")) {
                url = "https://$url"
            }
            val normalized = if (url.endsWith("/")) url else "$url/"
            if (normalized != currentBaseUrl || apiKey != currentApiKey || apiServiceInstance == null) {
                currentBaseUrl = normalized
                currentApiKey = apiKey

                val retrofit = Retrofit.Builder()
                    .baseUrl(currentBaseUrl)
                    .client(client)
                    .addConverterFactory(GsonConverterFactory.create())
                    .build()

                apiServiceInstance = retrofit.create(AlAmrApiService::class.java)
            }
        } catch (_: Exception) {
            // Safe fallback to avoid startup crashes
            currentBaseUrl = "https://al-amr-clipping-automation.onrender.com/"
            val retrofit = Retrofit.Builder()
                .baseUrl(currentBaseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
            apiServiceInstance = retrofit.create(AlAmrApiService::class.java)
        }
    }

    fun getService(): AlAmrApiService {
        if (apiServiceInstance == null) {
            configure(currentBaseUrl, currentApiKey)
        }
        return apiServiceInstance!!
    }

    fun getBaseUrl(): String = currentBaseUrl

    fun getApiKey(): String? = currentApiKey

    fun getStreamUrl(exportId: String): String {
        val base = if (currentBaseUrl.endsWith("/")) currentBaseUrl else "$currentBaseUrl/"
        return "${base}api/exports/$exportId/stream"
    }

    fun getOkHttpClient(): OkHttpClient = client
}
