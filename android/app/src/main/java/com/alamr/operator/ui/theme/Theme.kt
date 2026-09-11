package com.alamr.operator.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val DarkColorScheme = darkColorScheme(
    primary = Sodium500,
    onPrimary = Ink950,
    primaryContainer = Ink800,
    onPrimaryContainer = Sodium400,
    secondary = Sodium400,
    onSecondary = Ink950,
    background = Ink950,
    onBackground = Ink100,
    surface = Ink900,
    onSurface = Ink100,
    surfaceVariant = Ink850,
    onSurfaceVariant = Ink300,
    error = Rose500,
    onError = Ink100
)

@Composable
fun AlAmrTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkColorScheme,
        content = content
    )
}
