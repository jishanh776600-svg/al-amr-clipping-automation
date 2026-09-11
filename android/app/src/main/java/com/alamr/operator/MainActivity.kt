package com.alamr.operator

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.navigation.NavType
import androidx.navigation.compose.*
import androidx.navigation.navArgument
import com.alamr.operator.data.api.ApiClient
import com.alamr.operator.data.repository.PreferencesRepository
import com.alamr.operator.ui.screens.*
import com.alamr.operator.ui.theme.*

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = PreferencesRepository(this)
        ApiClient.configure(prefs.serverUrl, prefs.apiKey)

        setContent {
            AlAmrTheme {
                val navController = rememberNavController()
                val navBackStackEntry by navController.currentBackStackEntryAsState()
                val currentRoute = navBackStackEntry?.destination?.route

                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    bottomBar = {
                        NavigationBar(
                            containerColor = Ink900,
                            tonalElevation = 8.dp
                        ) {
                            NavigationBarItem(
                                selected = currentRoute == "mission_control",
                                onClick = { navController.navigate("mission_control") },
                                label = { Text("Control", fontSize = 10.sp) },
                                icon = { Text("⚡", color = if (currentRoute == "mission_control") Sodium500 else Ink500) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedTextColor = Sodium500,
                                    unselectedTextColor = Ink500,
                                    indicatorColor = Ink800
                                )
                            )
                            NavigationBarItem(
                                selected = currentRoute == "new_job",
                                onClick = { navController.navigate("new_job") },
                                label = { Text("Ingest", fontSize = 10.sp) },
                                icon = { Text("＋", color = if (currentRoute == "new_job") Sodium500 else Ink500) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedTextColor = Sodium500,
                                    unselectedTextColor = Ink500,
                                    indicatorColor = Ink800
                                )
                            )
                            NavigationBarItem(
                                selected = currentRoute == "clips",
                                onClick = { navController.navigate("clips") },
                                label = { Text("Clips", fontSize = 10.sp) },
                                icon = { Text("🎬", color = if (currentRoute == "clips") Sodium500 else Ink500) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedTextColor = Sodium500,
                                    unselectedTextColor = Ink500,
                                    indicatorColor = Ink800
                                )
                            )
                            NavigationBarItem(
                                selected = currentRoute == "publishing",
                                onClick = { navController.navigate("publishing") },
                                label = { Text("Publish", fontSize = 10.sp) },
                                icon = { Text("🚀", color = if (currentRoute == "publishing") Sodium500 else Ink500) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedTextColor = Sodium500,
                                    unselectedTextColor = Ink500,
                                    indicatorColor = Ink800
                                )
                            )
                            NavigationBarItem(
                                selected = currentRoute == "settings",
                                onClick = { navController.navigate("settings") },
                                label = { Text("Node", fontSize = 10.sp) },
                                icon = { Text("⚙", color = if (currentRoute == "settings") Sodium500 else Ink500) },
                                colors = NavigationBarItemDefaults.colors(
                                    selectedTextColor = Sodium500,
                                    unselectedTextColor = Ink500,
                                    indicatorColor = Ink800
                                )
                            )
                        }
                    }
                ) { innerPadding ->
                    NavHost(
                        navController = navController,
                        startDestination = "mission_control",
                        modifier = Modifier.padding(innerPadding)
                    ) {
                        composable("mission_control") {
                            MissionControlScreen(
                                onNavigateToJob = { id -> navController.navigate("job/$id") },
                                onNavigateToNewJob = { navController.navigate("new_job") }
                            )
                        }
                        composable("new_job") {
                            NewJobScreen(
                                onJobCreated = { id -> navController.navigate("job/$id") }
                            )
                        }
                        composable(
                            route = "job/{jobId}",
                            arguments = listOf(navArgument("jobId") { type = NavType.StringType })
                        ) { backStackEntry ->
                            val jobId = backStackEntry.arguments?.getString("jobId") ?: ""
                            JobProgressScreen(
                                jobId = jobId,
                                onNavigateToReview = { id -> navController.navigate("clips/$id") }
                            )
                        }
                        composable("clips") {
                            ClipGalleryScreen(
                                onClipClick = { clipId -> navController.navigate("clip_detail/$clipId") }
                            )
                        }
                        composable(
                            route = "clips/{jobId}",
                            arguments = listOf(navArgument("jobId") { type = NavType.StringType })
                        ) { backStackEntry ->
                            val jobId = backStackEntry.arguments?.getString("jobId")
                            ClipGalleryScreen(
                                jobId = jobId,
                                onClipClick = { clipId -> navController.navigate("clip_detail/$clipId") }
                            )
                        }
                        composable(
                            route = "clip_detail/{clipId}",
                            arguments = listOf(navArgument("clipId") { type = NavType.StringType })
                        ) { backStackEntry ->
                            val clipId = backStackEntry.arguments?.getString("clipId") ?: ""
                            ClipDetailScreen(
                                clipId = clipId,
                                onBack = { navController.popBackStack() }
                            )
                        }
                        composable("publishing") {
                            PublishingScreen()
                        }
                        composable("settings") {
                            ConnectionScreen(
                                prefs = prefs,
                                onConnected = { navController.navigate("mission_control") }
                            )
                        }
                    }
                }
            }
        }
    }
}
