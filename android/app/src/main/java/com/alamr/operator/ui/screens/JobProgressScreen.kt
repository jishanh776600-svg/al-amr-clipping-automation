package com.alamr.operator.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.alamr.operator.data.api.ApiClient
import com.alamr.operator.data.model.Job
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun JobProgressScreen(
    jobId: String,
    onNavigateToReview: (String) -> Unit
) {
    var job by remember { mutableStateOf<Job?>(null) }
    var loading by remember { mutableStateOf(true) }
    var isConnected by remember { mutableStateOf(true) }
    val scope = rememberCoroutineScope()

    // Polling loop resilient to phone disconnect/sleep
    LaunchedEffect(jobId) {
        while (true) {
            try {
                job = ApiClient.getService().getJob(jobId)
                isConnected = true
            } catch (_: Exception) {
                isConnected = false
            } finally {
                loading = false
            }
            if (job?.status == "done" || job?.status == "failed" || job?.status == "cancelled") {
                break
            }
            delay(1500)
        }
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
            text = "REMOTE JOB MONITOR",
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            color = Sodium500,
            letterSpacing = 1.5.sp
        )

        Text(
            text = job?.source?.title ?: "Job $jobId",
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold,
            color = Ink100
        )

        // Disconnected Invariant Banner
        Card(
            colors = CardDefaults.cardColors(
                containerColor = if (isConnected) Ink900 else Rose500.copy(alpha = 0.15f)
            ),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Text(
                    text = if (isConnected) "● Server Processing Authoritative" else "⚠ Reconnecting to Remote Server...",
                    color = if (isConnected) Emerald400 else Rose500,
                    fontWeight = FontWeight.SemiBold,
                    fontSize = 12.sp
                )
                Text(
                    text = "Closing this screen or turning off phone does not interrupt processing.",
                    color = Ink500,
                    fontSize = 11.sp
                )
            }
        }

        job?.let { j ->
            Card(
                colors = CardDefaults.cardColors(containerColor = Ink850),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(text = "Stage: ${j.current_stage}", color = Ink100, fontWeight = FontWeight.Bold)
                        Text(
                            text = "${(j.progress * 100).toInt()}%",
                            color = Sodium400,
                            fontWeight = FontWeight.Bold
                        )
                    }

                    LinearProgressIndicator(
                        progress = { j.progress.toFloat() },
                        modifier = Modifier.fillMaxWidth(),
                        color = Sodium500,
                        trackColor = Ink700
                    )

                    Text(
                        text = "Status: ${j.status.uppercase()}",
                        color = when (j.status) {
                            "done" -> Emerald400
                            "failed" -> Rose500
                            else -> Sodium400
                        },
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold
                    )

                    j.error?.let { err ->
                        Text(text = "Error: $err", color = Rose500, fontSize = 11.sp)
                    }
                }
            }

            if (j.status == "done") {
                Button(
                    onClick = { onNavigateToReview(j.id) },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Emerald500, contentColor = Ink950)
                ) {
                    Text("Review Generated Clips →", fontWeight = FontWeight.Bold)
                }
            } else if (j.status == "running" || j.status == "queued") {
                OutlinedButton(
                    onClick = {
                        scope.launch {
                            try {
                                job = ApiClient.getService().cancelJob(j.id)
                            } catch (_: Exception) {}
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Rose500)
                ) {
                    Text("Cancel Server Job")
                }
            } else if (j.status == "failed") {
                Button(
                    onClick = {
                        scope.launch {
                            try {
                                job = ApiClient.getService().retryJob(j.id)
                            } catch (_: Exception) {}
                        }
                    },
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.buttonColors(containerColor = Sodium500, contentColor = Ink950)
                ) {
                    Text("Retry Job")
                }
            }
        }
    }
}
