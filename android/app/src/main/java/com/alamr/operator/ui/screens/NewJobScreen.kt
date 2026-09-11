package com.alamr.operator.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.alamr.operator.data.api.ApiClient
import com.alamr.operator.data.model.CampaignPreset
import com.alamr.operator.data.model.IngestYouTubeRequest
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun NewJobScreen(
    onJobCreated: (String) -> Unit
) {
    var url by remember { mutableStateOf("") }
    var campaigns by remember { mutableStateOf<List<CampaignPreset>>(emptyList()) }
    var selectedCampaign by remember { mutableStateOf<CampaignPreset?>(null) }
    var minDuration by remember { mutableFloatStateOf(20f) }
    var maxDuration by remember { mutableFloatStateOf(90f) }
    var maxClips by remember { mutableFloatStateOf(5f) }
    var submitting by remember { mutableStateOf(false) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        try {
            campaigns = ApiClient.getService().listCampaigns()
        } catch (_: Exception) {}
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Ink950)
            .padding(20.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "INGEST DISPATCHER",
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            color = Sodium500,
            letterSpacing = 1.5.sp
        )

        Text(
            text = "New Ingest Job",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = Ink100
        )

        Text(
            text = "Job runs remotely on the server. Closing the mobile app will not disrupt execution.",
            fontSize = 13.sp,
            color = Ink500
        )

        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("YouTube URL") },
            placeholder = { Text("https://www.youtube.com/watch?v=...") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Sodium500,
                unfocusedBorderColor = Ink700,
                focusedLabelColor = Sodium500,
                unfocusedLabelColor = Ink500
            )
        )

        Text(
            text = "Campaign Rules & Hooks",
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = Ink300
        )

        campaigns.forEach { c ->
            val selected = selectedCampaign?.id == c.id
            Card(
                onClick = { selectedCampaign = if (selected) null else c },
                colors = CardDefaults.cardColors(
                    containerColor = if (selected) Sodium500.copy(alpha = 0.15f) else Ink900
                ),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(12.dp)) {
                    Text(
                        text = (if (selected) "✓ " else "") + c.name,
                        fontWeight = FontWeight.Bold,
                        color = if (selected) Sodium400 else Ink100,
                        fontSize = 13.sp
                    )
                    c.brief?.topic_context?.let { ctx ->
                        Text(text = ctx, fontSize = 11.sp, color = Ink500)
                    }
                }
            }
        }

        Text(
            text = "Duration: ${minDuration.toInt()}s – ${maxDuration.toInt()}s",
            fontSize = 13.sp,
            color = Ink300
        )
        RangeSlider(
            value = minDuration..maxDuration,
            onValueChange = {
                minDuration = it.start
                maxDuration = it.endInclusive
            },
            valueRange = 10f..180f,
            colors = SliderDefaults.colors(thumbColor = Sodium500, activeTrackColor = Sodium500)
        )

        Text(
            text = "Target Clip Count: ${maxClips.toInt()}",
            fontSize = 13.sp,
            color = Ink300
        )
        Slider(
            value = maxClips,
            onValueChange = { maxClips = it },
            valueRange = 1f..15f,
            steps = 14,
            colors = SliderDefaults.colors(thumbColor = Sodium500, activeTrackColor = Sodium500)
        )

        errorMessage?.let { err ->
            Text(text = err, color = Rose500, fontSize = 12.sp)
        }

        Button(
            onClick = {
                scope.launch {
                    submitting = true
                    errorMessage = null
                    try {
                        val service = ApiClient.getService()
                        val source = service.ingestYouTube(IngestYouTubeRequest(url.trim()))
                        val settingsMap = mutableMapOf<String, Any?>(
                            "min_duration_s" to minDuration.toInt(),
                            "max_duration_s" to maxDuration.toInt(),
                            "max_clips" to maxClips.toInt()
                        )
                        selectedCampaign?.brief?.let { brief ->
                            settingsMap["campaign"] = brief
                        }
                        val job = service.createJob(source.id, settingsMap)
                        onJobCreated(job.id)
                    } catch (e: Exception) {
                        errorMessage = "Dispatch failed: ${e.localizedMessage ?: e.message}"
                    } finally {
                        submitting = false
                    }
                }
            },
            enabled = !submitting && url.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = Sodium500, contentColor = Ink950)
        ) {
            if (submitting) {
                CircularProgressIndicator(color = Ink950, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("Dispatching to Server...")
            } else {
                Text("Dispatch Job to Server", fontWeight = FontWeight.Bold)
            }
        }
    }
}
