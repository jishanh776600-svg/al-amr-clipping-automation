package com.alamr.operator.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.alamr.operator.data.api.ApiClient
import com.alamr.operator.data.model.Clip
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun ClipGalleryScreen(
    jobId: String? = null,
    onClipClick: (String) -> Unit
) {
    var clips by remember { mutableStateOf<List<Clip>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var statusFilter by remember { mutableStateOf("all") }
    val scope = rememberCoroutineScope()

    val refresh = {
        scope.launch {
            loading = true
            try {
                val service = ApiClient.getService()
                clips = if (jobId != null) {
                    service.listJobClips(jobId)
                } else {
                    service.listAllClips(100)
                }
            } catch (_: Exception) {
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(jobId) {
        refresh()
    }

    val filtered = clips.filter {
        statusFilter == "all" || it.status == statusFilter
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Ink950)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = if (jobId != null) "Job Clips" else "Clip Gallery",
                fontSize = 24.sp,
                fontWeight = FontWeight.Bold,
                color = Ink100
            )
            IconButton(onClick = { refresh() }) {
                Text("↻", color = Sodium500, fontSize = 20.sp)
            }
        }

        // Filter chips
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            listOf("all", "kept", "candidate", "discarded").forEach { filter ->
                val selected = statusFilter == filter
                FilterChip(
                    selected = selected,
                    onClick = { statusFilter = filter },
                    label = { Text(filter.uppercase(), fontSize = 10.sp) },
                    colors = FilterChipDefaults.filterChipColors(
                        selectedContainerColor = Sodium500,
                        selectedLabelColor = Ink950,
                        containerColor = Ink900,
                        labelColor = Ink400
                    )
                )
            }
        }

        if (loading && clips.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = Sodium500)
            }
        } else if (filtered.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(text = "No clips found in this filter.", color = Ink500, fontSize = 13.sp)
            }
        } else {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(filtered) { clip ->
                    Card(
                        onClick = { onClipClick(clip.id) },
                        colors = CardDefaults.cardColors(containerColor = Ink900),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    text = "${clip.score}/100",
                                    fontSize = 18.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = if (clip.score >= 80) Sodium400 else Ink300
                                )
                                Text(
                                    text = clip.status.uppercase(),
                                    fontSize = 10.sp,
                                    fontWeight = FontWeight.Bold,
                                    color = if (clip.status == "kept") Emerald400 else Ink500
                                )
                            }

                            Text(
                                text = clip.title.ifBlank { "Untitled Clip" },
                                fontSize = 14.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = Ink100,
                                maxLines = 2
                            )

                            Text(
                                text = "Duration: ${clip.duration_s.toInt()}s · Exports: ${clip.exports.size}",
                                fontSize = 11.sp,
                                color = Ink500
                            )

                            clip.evaluation?.let { ev ->
                                Text(
                                    text = "Campaign Score: ${(ev.composite_score ?: ev.final_score).toInt()}/100",
                                    fontSize = 11.sp,
                                    color = Sodium500
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}
