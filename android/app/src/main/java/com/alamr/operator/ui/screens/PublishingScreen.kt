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
import com.alamr.operator.data.model.Clip
import com.alamr.operator.data.model.PublishRequest
import com.alamr.operator.data.model.PublishingPlatformInfo
import com.alamr.operator.data.model.PublishingRecord
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun PublishingScreen() {
    var clips by remember { mutableStateOf<List<Clip>>(emptyList()) }
    var selectedClip by remember { mutableStateOf<Clip?>(null) }
    var selectedPlatform by remember { mutableStateOf("YouTube Shorts") }
    var platforms by remember { mutableStateOf<List<PublishingPlatformInfo>>(emptyList()) }
    var publishingRecords by remember { mutableStateOf<List<PublishingRecord>>(emptyList()) }
    var dryRun by remember { mutableStateOf(false) }
    var publishing by remember { mutableStateOf(false) }
    var publishNotice by remember { mutableStateOf<String?>(null) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    val refreshData = {
        scope.launch {
            try {
                val service = ApiClient.getService()
                val all = service.listAllClips(50)
                val exportable = all.filter { it.exports.isNotEmpty() }
                clips = exportable
                if (selectedClip == null) {
                    selectedClip = exportable.firstOrNull()
                }
                platforms = service.getPublishingPlatforms()
                publishingRecords = service.listPublishing(limit = 20)
            } catch (e: Exception) {
                errorMessage = e.message
            }
        }
    }

    LaunchedEffect(Unit) {
        refreshData()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Ink950)
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Text(
            text = "DISTRIBUTION STAGING",
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            color = Sodium500,
            letterSpacing = 1.5.sp
        )

        Text(
            text = "Publishing Console",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = Ink100
        )

        publishNotice?.let { notice ->
            Card(
                colors = CardDefaults.cardColors(containerColor = Emerald500.copy(alpha = 0.15f)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = notice,
                    color = Emerald400,
                    fontSize = 12.sp,
                    modifier = Modifier.padding(12.dp)
                )
            }
        }

        errorMessage?.let { err ->
            Card(
                colors = CardDefaults.cardColors(containerColor = Rose500.copy(alpha = 0.15f)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = "Error: $err",
                    color = Rose400,
                    fontSize = 12.sp,
                    modifier = Modifier.padding(12.dp)
                )
            }
        }

        // Channel Badges
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            val ytConfigured = platforms.find { it.platform == "youtube" }?.configured ?: true
            val igConfigured = platforms.find { it.platform == "instagram" }?.configured ?: false
            val tgConfigured = platforms.find { it.platform == "telegram" }?.configured ?: true
            ChannelBadge("YouTube", ytConfigured, Modifier.weight(1f))
            ChannelBadge("Instagram", igConfigured, Modifier.weight(1f))
            ChannelBadge("Telegram", tgConfigured, Modifier.weight(1f))
        }

        Text(
            text = "Select Exported Clip",
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = Ink200
        )

        if (clips.isEmpty()) {
            Text(text = "No rendered clips ready for publishing.", color = Ink500, fontSize = 12.sp)
        } else {
            clips.take(5).forEach { clip ->
                val isSelected = selectedClip?.id == clip.id
                Card(
                    onClick = { selectedClip = clip },
                    colors = CardDefaults.cardColors(
                        containerColor = if (isSelected) Sodium500.copy(alpha = 0.15f) else Ink900
                    ),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Text(
                            text = (if (isSelected) "✓ " else "") + clip.title.ifBlank { "Untitled" },
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = if (isSelected) Sodium400 else Ink100
                        )
                        Text(
                            text = "Score: ${clip.score} · Exports: ${clip.exports.size}",
                            fontSize = 11.sp,
                            color = Ink500
                        )
                    }
                }
            }
        }

        Text(
            text = "Target Platform",
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            color = Ink200
        )

        listOf("YouTube Shorts", "Instagram Reels", "Telegram Channel").forEach { platform ->
            Row(modifier = Modifier.fillMaxWidth()) {
                RadioButton(
                    selected = selectedPlatform == platform,
                    onClick = { selectedPlatform = platform },
                    colors = RadioButtonDefaults.colors(selectedColor = Sodium500)
                )
                Text(
                    text = platform,
                    color = Ink100,
                    modifier = Modifier.padding(start = 8.dp, top = 12.dp),
                    fontSize = 13.sp
                )
            }
        }

        // Dry run checkbox
        Row(modifier = Modifier.fillMaxWidth()) {
            Checkbox(
                checked = dryRun,
                onCheckedChange = { dryRun = it },
                colors = CheckboxDefaults.colors(checkedColor = Sodium500)
            )
            Text(
                text = "Dry Run (validate API tokens without public video)",
                color = Ink300,
                modifier = Modifier.padding(start = 8.dp, top = 12.dp),
                fontSize = 12.sp
            )
        }

        Button(
            onClick = {
                val clip = selectedClip ?: return@Button
                val exportRec = clip.exports.firstOrNull() ?: return@Button
                val target = when (selectedPlatform) {
                    "YouTube Shorts" -> "youtube"
                    "Instagram Reels" -> "instagram"
                    else -> "telegram"
                }

                scope.launch {
                    publishing = true
                    errorMessage = null
                    publishNotice = null
                    try {
                        val req = PublishRequest(
                            platforms = listOf(target),
                            title = clip.title,
                            description = clip.hook,
                            dry_run = dryRun
                        )
                        ApiClient.getService().publishExport(exportRec.id, req)
                        publishNotice = "✓ Successfully published \"${clip.title}\" to $selectedPlatform!"
                        publishingRecords = ApiClient.getService().listPublishing(limit = 20)
                    } catch (e: Exception) {
                        errorMessage = "Publish failed: ${e.message}"
                    } finally {
                        publishing = false
                    }
                }
            },
            enabled = !publishing && selectedClip != null,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = Sodium500, contentColor = Ink950)
        ) {
            if (publishing) {
                CircularProgressIndicator(color = Ink950, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("Publishing...")
            } else {
                Text("Publish to $selectedPlatform", fontWeight = FontWeight.Bold)
            }
        }

        // Publishing History
        if (publishingRecords.isNotEmpty()) {
            Text(
                text = "Recent Publishing Activity",
                fontSize = 14.sp,
                fontWeight = FontWeight.SemiBold,
                color = Ink200
            )

            publishingRecords.take(5).forEach { rec ->
                Card(
                    colors = CardDefaults.cardColors(containerColor = Ink900),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(12.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween
                        ) {
                            Text(
                                text = rec.platform.uppercase(),
                                fontSize = 12.sp,
                                fontWeight = FontWeight.Bold,
                                color = Ink100
                            )
                            Text(
                                text = rec.status.uppercase(),
                                fontSize = 11.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = if (rec.status == "published") Emerald400 else if (rec.status == "failed") Rose400 else Sodium400
                            )
                        }
                        rec.external_id?.let {
                            Text(text = "ID: $it", fontSize = 10.sp, color = Ink400)
                        }
                        rec.error?.let {
                            Text(text = "Error: $it", fontSize = 10.sp, color = Rose400)
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun ChannelBadge(name: String, active: Boolean, modifier: Modifier = Modifier) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Ink900),
        modifier = modifier
    ) {
        Column(modifier = Modifier.padding(8.dp)) {
            Text(text = name, fontSize = 11.sp, fontWeight = FontWeight.Bold, color = Ink100)
            Text(
                text = if (active) "Ready" else "Off",
                fontSize = 10.sp,
                color = if (active) Emerald400 else Ink500
            )
        }
    }
}
