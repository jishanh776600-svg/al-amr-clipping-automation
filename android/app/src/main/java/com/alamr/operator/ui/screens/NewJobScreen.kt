package com.alamr.operator.ui.screens

import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
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
import com.alamr.operator.data.api.ApiClient
import com.alamr.operator.data.model.AutonomousJobRequest
import com.alamr.operator.data.model.CampaignPreset
import com.alamr.operator.data.model.DriveGuidelineRequest
import com.alamr.operator.data.model.IngestYouTubeRequest
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.toRequestBody

@Composable
fun NewJobScreen(
    onJobCreated: (String) -> Unit
) {
    var url by remember { mutableStateOf("") }
    var guidelineMode by remember { mutableStateOf("drive") } // "drive" or "file"
    var driveGuidelineUrl by remember { mutableStateOf("") }
    var guidelineUri by remember { mutableStateOf<Uri?>(null) }
    var guidelineName by remember { mutableStateOf<String?>(null) }
    var guidelineUploadedId by remember { mutableStateOf<String?>(null) }
    var campaigns by remember { mutableStateOf<List<CampaignPreset>>(emptyList()) }
    var selectedCampaign by remember { mutableStateOf<CampaignPreset?>(null) }

    // Destinations
    var destTelegram by remember { mutableStateOf(true) }
    var destYouTube by remember { mutableStateOf(true) }
    var destDrive by remember { mutableStateOf(true) }

    var minDuration by remember { mutableFloatStateOf(20f) }
    var maxDuration by remember { mutableFloatStateOf(75f) }
    var maxClips by remember { mutableFloatStateOf(5f) }
    var submitting by remember { mutableStateOf(false) }
    var statusText by remember { mutableStateOf<String?>(null) }
    var errorMessage by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    val guidelinePicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent()
    ) { uri: Uri? ->
        guidelineUri = uri
        if (uri != null) {
            val cursor = context.contentResolver.query(uri, null, null, null, null)
            val nameIndex = cursor?.getColumnIndex(OpenableColumns.DISPLAY_NAME) ?: -1
            if (cursor != null && cursor.moveToFirst() && nameIndex >= 0) {
                guidelineName = cursor.getString(nameIndex)
                cursor.close()
            } else {
                guidelineName = uri.lastPathSegment ?: "guideline.pdf"
            }
        } else {
            guidelineName = null
        }
    }

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
            text = "AUTONOMOUS INGEST DISPATCHER",
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            color = Sodium500,
            letterSpacing = 1.5.sp
        )

        Text(
            text = "New Autonomous Job",
            fontSize = 24.sp,
            fontWeight = FontWeight.Bold,
            color = Ink100
        )

        Text(
            text = "Input 1: Video link · Input 2: Guidelines (Google Drive or PDF/DOCX) · Autonomous processing on cloud worker.",
            fontSize = 13.sp,
            color = Ink500
        )

        // Banner: Autonomous Worker Assurance
        Card(
            colors = CardDefaults.cardColors(containerColor = Ink900),
            modifier = Modifier.fillMaxWidth()
        ) {
            Row(
                modifier = Modifier.padding(12.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(text = "⚡", fontSize = 16.sp)
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "Client disconnect safe: Once launched, the pipeline executes autonomously on the cloud worker. You can safely close this app.",
                    fontSize = 11.sp,
                    color = Ink400,
                    lineHeight = 15.sp
                )
            }
        }

        // INPUT 1
        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("INPUT 1 · Source Video / YouTube URL") },
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

        // INPUT 2
        Text(
            text = "INPUT 2 · Campaign Guidelines Document",
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = Ink300
        )

        // Mode switch: Google Drive vs File Upload
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Button(
                onClick = { guidelineMode = "drive" },
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (guidelineMode == "drive") Sodium500 else Ink900,
                    contentColor = if (guidelineMode == "drive") Ink950 else Ink300
                ),
                modifier = Modifier.weight(1f)
            ) {
                Text("Google Drive Link", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }

            Button(
                onClick = { guidelineMode = "file" },
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (guidelineMode == "file") Sodium500 else Ink900,
                    contentColor = if (guidelineMode == "file") Ink950 else Ink300
                ),
                modifier = Modifier.weight(1f)
            ) {
                Text("Upload PDF / DOCX", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
            }
        }

        if (guidelineMode == "drive") {
            OutlinedTextField(
                value = driveGuidelineUrl,
                onValueChange = { driveGuidelineUrl = it },
                label = { Text("Google Drive / Docs URL or File ID") },
                placeholder = { Text("https://docs.google.com/document/d/... or Drive Link") },
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
                text = "AL AMR automatically exports native Google Docs or downloads PDF/DOCX from Drive.",
                fontSize = 11.sp,
                color = Ink500
            )
        } else {
            Card(
                onClick = { guidelinePicker.launch("*/*") },
                colors = CardDefaults.cardColors(
                    containerColor = if (guidelineUri != null) Sodium500.copy(alpha = 0.15f) else Ink900
                ),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(modifier = Modifier.padding(14.dp)) {
                    if (guidelineUri != null) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = "📄 ${guidelineName ?: "Campaign Guideline"}",
                                    fontWeight = FontWeight.Bold,
                                    color = Sodium400,
                                    fontSize = 14.sp
                                )
                                Text(
                                    text = "Document attached · Rules will be extracted on dispatch",
                                    fontSize = 11.sp,
                                    color = Ink400
                                )
                            }
                            TextButton(
                                onClick = {
                                    guidelineUri = null
                                    guidelineName = null
                                    guidelineUploadedId = null
                                }
                            ) {
                                Text("Remove", color = Rose500, fontSize = 12.sp)
                            }
                        }
                    } else {
                        Text(
                            text = "+ Attach Campaign Guidelines (.pdf or .docx)",
                            fontWeight = FontWeight.SemiBold,
                            color = Ink200,
                            fontSize = 13.sp
                        )
                        Text(
                            text = "Upload PDF or Word document to guide highlights and CTA rules",
                            fontSize = 11.sp,
                            color = Ink500
                        )
                    }
                }
            }
        }

        // INPUT 3: Publishing Destinations
        Text(
            text = "INPUT 3 · Publishing Destinations",
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
            color = Ink300
        )

        Card(
            colors = CardDefaults.cardColors(containerColor = Ink900),
            modifier = Modifier.fillMaxWidth()
        ) {
            Column(modifier = Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Checkbox(
                        checked = destTelegram,
                        onCheckedChange = { destTelegram = it },
                        colors = CheckboxDefaults.colors(checkedColor = Sodium500)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Column {
                        Text("Telegram Channel", fontWeight = FontWeight.Bold, color = Ink100, fontSize = 13.sp)
                        Text("Verified live publishing to operator Telegram channel", fontSize = 11.sp, color = Ink500)
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Checkbox(
                        checked = destYouTube,
                        onCheckedChange = { destYouTube = it },
                        colors = CheckboxDefaults.colors(checkedColor = Sodium500)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Column {
                        Text("YouTube Shorts", fontWeight = FontWeight.Bold, color = Ink100, fontSize = 13.sp)
                        Text("Auto-publish or dry-run draft depending on cloud credentials", fontSize = 11.sp, color = Ink500)
                    }
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Checkbox(
                        checked = destDrive,
                        onCheckedChange = { destDrive = it },
                        colors = CheckboxDefaults.colors(checkedColor = Sodium500)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Column {
                        Text("Google Drive Media Vault", fontWeight = FontWeight.Bold, color = Ink100, fontSize = 13.sp)
                        Text("Permanent cloud archival of master rendered 9:16 clips", fontSize = 11.sp, color = Ink500)
                    }
                }
            }
        }

        if (campaigns.isNotEmpty()) {
            Text(
                text = "Preset Campaign Override (Optional)",
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
                    statusText = "Preparing autonomous pipeline dispatch..."
                    try {
                        val service = ApiClient.getService()
                        val selectedDestinations = mutableListOf<String>()
                        if (destTelegram) selectedDestinations.add("telegram")
                        if (destYouTube) selectedDestinations.add("youtube")
                        if (destDrive) selectedDestinations.add("drive")

                        var guidelineId: String? = null

                        // If user selected file upload, upload it first
                        if (guidelineMode == "file" && guidelineUri != null) {
                            statusText = "Uploading & extracting campaign guidelines..."
                            val inputStream = context.contentResolver.openInputStream(guidelineUri!!)
                            val bytes = inputStream?.readBytes() ?: byteArrayOf()
                            inputStream?.close()
                            val mimeType = context.contentResolver.getType(guidelineUri!!) ?: "application/octet-stream"
                            val reqBody = bytes.toRequestBody(mimeType.toMediaTypeOrNull())
                            val part = MultipartBody.Part.createFormData("file", guidelineName ?: "guideline.pdf", reqBody)
                            val uploaded = service.uploadGuideline(part)
                            guidelineId = uploaded.id
                        }

                        val settingsMap = mutableMapOf<String, Any?>(
                            "min_duration_s" to minDuration.toInt(),
                            "max_duration_s" to maxDuration.toInt(),
                            "max_clips" to maxClips.toInt()
                        )

                        if (selectedCampaign?.brief != null) {
                            settingsMap["campaign"] = selectedCampaign!!.brief
                        }

                        val driveUrl = if (guidelineMode == "drive" && driveGuidelineUrl.isNotBlank()) driveGuidelineUrl.trim() else null

                        statusText = "Dispatching autonomous job..."
                        val job = service.createAutonomousJob(
                            AutonomousJobRequest(
                                video_url = url.trim(),
                                guideline_id = guidelineId,
                                drive_guideline_url = driveUrl,
                                destinations = selectedDestinations,
                                settings = settingsMap
                            )
                        )
                        onJobCreated(job.id)
                    } catch (e: Exception) {
                        errorMessage = "Dispatch failed: ${e.localizedMessage ?: e.message}"
                    } finally {
                        submitting = false
                        statusText = null
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
                Text(statusText ?: "Dispatching...")
            } else {
                Text("Launch Autonomous Pipeline", fontWeight = FontWeight.Bold)
            }
        }
    }
}

