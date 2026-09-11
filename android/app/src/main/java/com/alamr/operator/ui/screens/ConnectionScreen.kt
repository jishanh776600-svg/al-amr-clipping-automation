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
import com.alamr.operator.data.repository.PreferencesRepository
import com.alamr.operator.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun ConnectionScreen(
    prefs: PreferencesRepository,
    onConnected: () -> Unit
) {
    var serverUrl by remember { mutableStateOf(prefs.serverUrl) }
    var apiKey by remember { mutableStateOf(prefs.apiKey) }
    var testing by remember { mutableStateOf(false) }
    var testResult by remember { mutableStateOf<String?>(null) }
    var isSuccess by remember { mutableStateOf(false) }
    val coroutineScope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Ink950)
            .padding(24.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        Spacer(modifier = Modifier.height(16.dp))

        Text(
            text = "AL AMR OPERATOR",
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            color = Sodium500,
            letterSpacing = 2.sp
        )

        Text(
            text = "Remote Node Setup",
            fontSize = 28.sp,
            fontWeight = FontWeight.Bold,
            color = Ink100
        )

        Text(
            text = "Connect this thin client to your persistent AL AMR remote compute instance. Heavy processing runs entirely on the server.",
            fontSize = 14.sp,
            color = Ink500
        )

        Spacer(modifier = Modifier.height(8.dp))

        OutlinedTextField(
            value = serverUrl,
            onValueChange = { serverUrl = it },
            label = { Text("Remote Server URL") },
            placeholder = { Text("http://192.168.1.100:8000 or https://alamr.domain.com") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Sodium500,
                unfocusedBorderColor = Ink700,
                focusedLabelColor = Sodium500,
                unfocusedLabelColor = Ink500
            )
        )

        OutlinedTextField(
            value = apiKey,
            onValueChange = { apiKey = it },
            label = { Text("Operator API Key (Optional)") },
            placeholder = { Text("Enter remote secret token") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = Sodium500,
                unfocusedBorderColor = Ink700,
                focusedLabelColor = Sodium500,
                unfocusedLabelColor = Ink500
            )
        )

        testResult?.let { msg ->
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = if (isSuccess) Emerald500.copy(alpha = 0.15f) else Rose500.copy(alpha = 0.15f)
                ),
                modifier = Modifier.fillMaxWidth()
            ) {
                Text(
                    text = msg,
                    color = if (isSuccess) Emerald400 else Rose500,
                    fontSize = 13.sp,
                    modifier = Modifier.padding(12.dp)
                )
            }
        }

        Button(
            onClick = {
                coroutineScope.launch {
                    testing = true
                    testResult = null
                    try {
                        ApiClient.configure(serverUrl, apiKey)
                        val ready = ApiClient.getService().ready()
                        prefs.serverUrl = serverUrl
                        prefs.apiKey = apiKey
                        isSuccess = true
                        testResult = "✓ Connected to AL AMR Node! Status: ${if (ready.ready) "Ready" else "Checking components"}"
                    } catch (e: Exception) {
                        isSuccess = false
                        testResult = "Connection failed: ${e.localizedMessage ?: e.message}"
                    } finally {
                        testing = false
                    }
                }
            },
            enabled = !testing && serverUrl.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.buttonColors(containerColor = Sodium500, contentColor = Ink950)
        ) {
            if (testing) {
                CircularProgressIndicator(
                    color = Ink950,
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text("Testing Connection...")
            } else {
                Text("Verify Node Connection", fontWeight = FontWeight.Bold)
            }
        }

        if (isSuccess) {
            OutlinedButton(
                onClick = onConnected,
                modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = Sodium500)
            ) {
                Text("Proceed to Mission Control →")
            }
        }
    }
}
