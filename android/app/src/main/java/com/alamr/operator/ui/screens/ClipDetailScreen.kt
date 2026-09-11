package com.alamr.operator.ui.screens

import android.net.Uri
import android.widget.MediaController
import android.widget.VideoView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.alamr.operator.data.api.ApiClient
import com.alamr.operator.data.model.Clip
import com.alamr.operator.data.model.PatchClipRequest
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun ClipDetailScreen(
    clipId: String,
    onBack: () -> Unit
) {
    var clip by remember { mutableStateOf<Clip?>(null) }
    var loading by remember { mutableStateOf(true) }
    var exporting by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    val load = {
        scope.launch {
            loading = true
            try {
                clip = ApiClient.getService().getClip(clipId)
            } catch (_: Exception) {
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(clipId) {
        load()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Ink950)
            .padding(16.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            TextButton(onClick = onBack) {
                Text("← Back", color = Sodium500)
            }
            Text(
                text = "${clip?.score ?: 0}/100",
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = Sodium400
            )
        }

        clip?.let { c ->
            Text(
                text = c.title.ifBlank { "Clip Details" },
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                color = Ink100
            )

            // Video Streaming Surface via HTTP Range Request
            val export = c.exports.firstOrNull()
            if (export != null) {
                val streamUrl = ApiClient.getStreamUrl(export.id)
                Card(
                    colors = CardDefaults.cardColors(containerColor = Ink900),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(280.dp)
                ) {
                    AndroidView(
                        factory = { ctx ->
                            VideoView(ctx).apply {
                                val mc = MediaController(ctx)
                                mc.setAnchorView(this)
                                setMediaController(mc)
                                val headers = mutableMapOf<String, String>()
                                ApiClient.getApiKey()?.takeIf { it.isNotBlank() }?.let {
                                    headers["Authorization"] = "Bearer $it"
                                }
                                setVideoURI(Uri.parse(streamUrl), headers)
                                setOnPreparedListener { start() }
                            }
                        },
                        modifier = Modifier.fillMaxSize()
                    )
                }
                Text(
                    text = "Streaming inline via HTTP Range: ${export.ratio} · ${export.style}",
                    fontSize = 11.sp,
                    color = Ink500
                )
            } else {
                Card(
                    colors = CardDefaults.cardColors(containerColor = Ink900),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 12.dp)
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        Text("No rendered MP4 export yet for this clip.", color = Ink400, fontSize = 12.sp)
                        Spacer(modifier = Modifier.height(8.dp))
                        Button(
                            onClick = {
                                scope.launch {
                                    exporting = true
                                    try {
                                        ApiClient.getService().exportClip(c.id, "9:16", "bold_pop")
                                        load()
                                    } catch (_: Exception) {
                                    } finally {
                                        exporting = false
                                    }
                                }
                            },
                            enabled = !exporting,
                            colors = ButtonDefaults.buttonColors(containerColor = Sodium500, contentColor = Ink950)
                        ) {
                            Text(if (exporting) "Rendering Remotely..." else "Render 9:16 Short on Server", fontSize = 12.sp)
                        }
                    }
                }
            }

            // Status Actions (Keep / Discard)
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Button(
                    onClick = {
                        scope.launch {
                            val nextStatus = if (c.status == "kept") "candidate" else "kept"
                            clip = ApiClient.getService().patchClip(c.id, PatchClipRequest(status = nextStatus))
                        }
                    },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (c.status == "kept") Emerald500 else Ink800,
                        contentColor = if (c.status == "kept") Ink950 else Ink100
                    )
                ) {
                    Text(if (c.status == "kept") "✓ Kept" else "Keep Clip")
                }

                OutlinedButton(
                    onClick = {
                        scope.launch {
                            val nextStatus = if (c.status == "discarded") "candidate" else "discarded"
                            clip = ApiClient.getService().patchClip(c.id, PatchClipRequest(status = nextStatus))
                        }
                    },
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = if (c.status == "discarded") Rose500 else Ink500
                    )
                ) {
                    Text(if (c.status == "discarded") "Discarded" else "Discard")
                }
            }

            // Campaign Evaluation Card
            c.evaluation?.let { ev ->
                Card(
                    colors = CardDefaults.cardColors(containerColor = Ink900),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(modifier = Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(
                            text = "CAMPAIGN EVALUATION",
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            color = Sodium500
                        )
                        Text(
                            text = "Composite Score: ${(ev.composite_score ?: ev.final_score).toInt()}/100",
                            fontWeight = FontWeight.Bold,
                            fontSize = 14.sp,
                            color = Ink100
                        )
                        ev.matched_hooks?.forEach { hook ->
                            Text(text = "✓ $hook", color = Emerald400, fontSize = 12.sp)
                        }
                        ev.reasoning?.let { r ->
                            Text(text = "\"$r\"", color = Ink500, fontSize = 11.sp)
                        }
                    }
                }
            }
        }
    }
}
