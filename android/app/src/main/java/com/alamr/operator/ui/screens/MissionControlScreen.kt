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
import com.alamr.operator.data.model.ReadyResponse
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun MissionControlScreen(
    onNavigateToJob: (String) -> Unit,
    onNavigateToNewJob: () -> Unit
) {
    var loading by remember { mutableStateOf(true) }
    var readyInfo by remember { mutableStateOf<ReadyResponse?>(null) }
    var jobs by remember { mutableStateOf<List<Job>>(emptyList()) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    val refresh = {
        scope.launch {
            loading = true
            errorMessage = null
            try {
                val service = ApiClient.getService()
                readyInfo = service.ready()
                jobs = service.listJobs(20)
            } catch (e: Exception) {
                errorMessage = e.localizedMessage ?: "Failed to connect to AL AMR remote node."
            } finally {
                loading = false
            }
        }
    }

    LaunchedEffect(Unit) {
        refresh()
    }

    val runningJob = jobs.find { it.status == "running" }
    val queuedCount = jobs.count { it.status == "queued" }
    val completedCount = jobs.count { it.status == "done" }

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
            Column {
                Text(
                    text = "OPERATOR CONTROL",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = Sodium500,
                    letterSpacing = 1.5.sp
                )
                Text(
                    text = "Mission Control",
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Bold,
                    color = Ink100
                )
            }

            IconButton(onClick = { refresh() }) {
                Text("↻", color = Sodium500, fontSize = 20.sp)
            }
        }

        errorMessage?.let { err ->
            Card(
                colors = CardDefaults.cardColors(containerColor = Rose500.copy(alpha = 0.15f)),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = err,
                    color = Rose500,
                    fontSize = 12.sp,
                    modifier = Modifier.padding(12.dp)
                )
            }
        }

        // Readiness Banner
        Card(
            colors = CardDefaults.cardColors(
                containerColor = if (readyInfo?.ready == true) Emerald500.copy(alpha = 0.12f) else Ink850
            ),
            modifier = Modifier.fillMaxWidth()
        ) {
            Row(
                modifier = Modifier.padding(14.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = if (readyInfo?.ready == true) "● REMOTE NODE OPERATIONAL" else "○ REMOTE NODE STANDBY",
                    color = if (readyInfo?.ready == true) Emerald400 else Sodium500,
                    fontWeight = FontWeight.Bold,
                    fontSize = 12.sp
                )
            }
        }

        // Metric Row
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            MetricCard(label = "Active", value = if (runningJob != null) "1" else "0", modifier = Modifier.weight(1f))
            MetricCard(label = "Queued", value = queuedCount.toString(), modifier = Modifier.weight(1f))
            MetricCard(label = "Done", value = completedCount.toString(), modifier = Modifier.weight(1f))
        }

        // Active Job Preview
        if (runningJob != null) {
            Card(
                colors = CardDefaults.cardColors(containerColor = Ink850),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        text = "PROCESSING REMOTELY",
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        color = Sodium500
                    )
                    Text(
                        text = runningJob.source?.title ?: "Job ${runningJob.id.take(8)}",
                        fontWeight = FontWeight.Bold,
                        fontSize = 15.sp,
                        color = Ink100
                    )
                    Text(
                        text = "Stage: ${runningJob.current_stage}",
                        fontSize = 12.sp,
                        color = Ink300
                    )
                    LinearProgressIndicator(
                        progress = { runningJob.progress.toFloat() },
                        modifier = Modifier.fillMaxWidth(),
                        color = Sodium500,
                        trackColor = Ink700
                    )
                    Button(
                        onClick = { onNavigateToJob(runningJob.id) },
                        colors = ButtonDefaults.buttonColors(containerColor = Ink700, contentColor = Ink100),
                        modifier = Modifier.align(Alignment.End)
                    ) {
                        Text("Live Monitor →", fontSize = 12.sp)
                    }
                }
            }
        }

        // Quick Ingest Button
        Button(
            onClick = onNavigateToNewJob,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = Sodium500, contentColor = Ink950)
        ) {
            Text("+ Start New Ingest", fontWeight = FontWeight.Bold)
        }

        Text(
            text = "Recent Server Jobs",
            fontSize = 16.sp,
            fontWeight = FontWeight.Bold,
            color = Ink100
        )

        jobs.take(10).forEach { job ->
            Card(
                colors = CardDefaults.cardColors(containerColor = Ink900),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(
                            text = job.source?.title ?: "Job ${job.id.take(8)}",
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = Ink100,
                            maxLines = 1
                        )
                        Text(
                            text = "Status: ${job.status} · ${job.provider}",
                            fontSize = 11.sp,
                            color = Ink500
                        )
                    }
                    Button(
                        onClick = { onNavigateToJob(job.id) },
                        colors = ButtonDefaults.buttonColors(containerColor = Ink800, contentColor = Ink300)
                    ) {
                        Text("View", fontSize = 11.sp)
                    }
                }
            }
        }
    }
}

@Composable
fun MetricCard(label: String, value: String, modifier: Modifier = Modifier) {
    Card(
        colors = CardDefaults.cardColors(containerColor = Ink900),
        modifier = modifier
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Text(text = value, fontSize = 20.sp, fontWeight = FontWeight.Bold, color = Ink100)
            Text(text = label, fontSize = 11.sp, color = Ink500)
        }
    }
}
