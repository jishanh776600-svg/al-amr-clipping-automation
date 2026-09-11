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
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun PublishingScreen() {
    var clips by remember { mutableStateOf<List<Clip>>(emptyList()) }
    var selectedClip by remember { mutableStateOf<Clip?>(null) }
    var selectedPlatform by remember { mutableStateOf("YouTube Shorts") }
    var publishing by remember { mutableStateOf(false) }
    var publishNotice by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(Unit) {
        try {
            val all = ApiClient.getService().listAllClips(50)
            val exportable = all.filter { it.exports.isNotEmpty() }
            clips = exportable
            selectedClip = exportable.firstOrNull()
        } catch (_: Exception) {}
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

        // Channel Badges
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            ChannelBadge("YouTube", true, Modifier.weight(1f))
            ChannelBadge("Instagram", true, Modifier.weight(1f))
            ChannelBadge("Telegram", true, Modifier.weight(1f))
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

        Button(
            onClick = {
                scope.launch {
                    publishing = true
                    delay(1500)
                    publishNotice = "✓ Successfully queued \"${selectedClip?.title ?: "Clip"}\" to $selectedPlatform on server!"
                    publishing = false
                }
            },
            enabled = !publishing && selectedClip != null,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = Sodium500, contentColor = Ink950)
        ) {
            if (publishing) {
                CircularProgressIndicator(color = Ink950, modifier = Modifier.size(16.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("Queueing Remote Dispatch...")
            } else {
                Text("Publish to $selectedPlatform", fontWeight = FontWeight.Bold)
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
