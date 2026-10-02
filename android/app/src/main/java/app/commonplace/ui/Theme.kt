package app.commonplace.ui

import android.graphics.Bitmap
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageShader
import androidx.compose.ui.graphics.ShaderBrush
import androidx.compose.ui.graphics.TileMode
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.commonplace.R
import kotlin.random.Random

// Paper and ink: warm cream pages, brown-black ink, rubric red for the accent.
private val Light = lightColorScheme(
    primary = Color(0xFF8B2E1F),
    onPrimary = Color(0xFFFFF8F0),
    primaryContainer = Color(0xFFF2DCCF),
    onPrimaryContainer = Color(0xFF3A0E06),
    inversePrimary = Color(0xFFE39A84),
    secondary = Color(0xFF6B5A2E),
    onSecondary = Color(0xFFFFF8EC),
    secondaryContainer = Color(0xFFECE0BE),
    onSecondaryContainer = Color(0xFF2A2108),
    tertiary = Color(0xFF2F4F5E),
    tertiaryContainer = Color(0xFFD3E1E6),
    background = Color(0xFFF5EFE3),
    onBackground = Color(0xFF2A2420),
    surface = Color(0xFFF5EFE3),
    onSurface = Color(0xFF2A2420),
    surfaceVariant = Color(0xFFE6DCCB),
    onSurfaceVariant = Color(0xFF5E544A),
    surfaceTint = Color(0xFF8C8072),
    surfaceContainerLowest = Color(0xFFFBF7EE),
    surfaceContainerLow = Color(0xFFEFE8DA),
    surfaceContainer = Color(0xFFEAE2D2),
    surfaceContainerHigh = Color(0xFFE4DBC9),
    surfaceContainerHighest = Color(0xFFDDD3BF),
    inverseSurface = Color(0xFF3A332D),
    inverseOnSurface = Color(0xFFF5EFE3),
    outline = Color(0xFF8C8072),
    outlineVariant = Color(0xFFD6CAB6),
    error = Color(0xFFA3261B),
    errorContainer = Color(0xFFF6D9D3),
    onErrorContainer = Color(0xFF410E0A),
)

// Night reading: a dark leather page with parchment-colored ink.
private val Dark = darkColorScheme(
    primary = Color(0xFFE39A84),
    onPrimary = Color(0xFF4A140A),
    primaryContainer = Color(0xFF6B2417),
    onPrimaryContainer = Color(0xFFFFDBCF),
    inversePrimary = Color(0xFF8B2E1F),
    secondary = Color(0xFFD8C48E),
    onSecondary = Color(0xFF3A2F0B),
    secondaryContainer = Color(0xFF4F4321),
    onSecondaryContainer = Color(0xFFF2E3B8),
    tertiary = Color(0xFFA7C7D4),
    tertiaryContainer = Color(0xFF2B4652),
    background = Color(0xFF1A1714),
    onBackground = Color(0xFFE9DFCC),
    surface = Color(0xFF1A1714),
    onSurface = Color(0xFFE9DFCC),
    surfaceVariant = Color(0xFF3A332D),
    onSurfaceVariant = Color(0xFFBFB3A0),
    surfaceTint = Color(0xFFBFB3A0),
    surfaceContainerLowest = Color(0xFF141210),
    surfaceContainerLow = Color(0xFF211D19),
    surfaceContainer = Color(0xFF26211D),
    surfaceContainerHigh = Color(0xFF302A25),
    surfaceContainerHighest = Color(0xFF3A332D),
    inverseSurface = Color(0xFFE9DFCC),
    inverseOnSurface = Color(0xFF2A2420),
    outline = Color(0xFF8D8170),
    outlineVariant = Color(0xFF3E3730),
    error = Color(0xFFF0A99E),
    errorContainer = Color(0xFF6E1D14),
    onErrorContainer = Color(0xFFFFDAD4),
)

/** Colors outside the Material roles: the evidence highlight and the "unverified" mark. */
@Immutable
data class Extra(val highlight: Color, val onHighlight: Color, val unverified: Color, val good: Color)

val LocalExtra = staticCompositionLocalOf { Extra(Color.Yellow, Color.Black, Color.Red, Color.Green) }

/** Literata is a variable font; `opsz` picks the cut drawn for text or for display sizes. */
private fun literata(opsz: Float) = FontFamily(
    listOf(400, 500, 600, 700).flatMap { w ->
        listOf(R.font.literata to FontStyle.Normal, R.font.literata_italic to FontStyle.Italic).map { (res, style) ->
            Font(
                res,
                FontWeight(w),
                style,
                FontVariation.Settings(FontVariation.weight(w), FontVariation.Setting("opsz", opsz)),
            )
        }
    },
)

private val ReadingFamily = literata(12f)
private val DisplayFamily = literata(36f)

private val AppTypography = Typography().let { t ->
    fun TextStyle.text() = copy(fontFamily = ReadingFamily)
    fun TextStyle.display() = copy(fontFamily = DisplayFamily, fontWeight = FontWeight.SemiBold)
    t.copy(
        displayLarge = t.displayLarge.display(),
        displayMedium = t.displayMedium.display(),
        displaySmall = t.displaySmall.display().copy(letterSpacing = (-0.5).sp),
        headlineLarge = t.headlineLarge.display(),
        headlineMedium = t.headlineMedium.display(),
        headlineSmall = t.headlineSmall.display().copy(lineHeight = 32.sp),
        titleLarge = t.titleLarge.display(),
        titleMedium = t.titleMedium.text().copy(fontWeight = FontWeight.SemiBold),
        titleSmall = t.titleSmall.text().copy(fontWeight = FontWeight.SemiBold),
        bodyLarge = t.bodyLarge.text().copy(fontSize = 17.sp, lineHeight = 28.sp),
        bodyMedium = t.bodyMedium.text().copy(lineHeight = 22.sp),
        bodySmall = t.bodySmall.text().copy(lineHeight = 18.sp),
        labelLarge = t.labelLarge.text().copy(fontWeight = FontWeight.SemiBold),
        labelMedium = t.labelMedium.text(),
        labelSmall = t.labelSmall.text(),
    )
}

// Printed matter has small radii; Material's rounded defaults read as app chrome.
private val AppShapes = Shapes(
    extraSmall = RoundedCornerShape(2.dp),
    small = RoundedCornerShape(4.dp),
    medium = RoundedCornerShape(6.dp),
    large = RoundedCornerShape(10.dp),
    extraLarge = RoundedCornerShape(16.dp),
)

/** Serif body text for passages and articles. */
val ReadingStyle = TextStyle(fontFamily = ReadingFamily, fontSize = 18.sp, lineHeight = 30.sp)

/** Light and dark specks, tiled over the whole screen so flat colors read as paper. */
private val grain by lazy {
    val n = 192
    val r = Random(7)
    val px = IntArray(n * n) {
        val a = r.nextInt(0, 26)
        if (r.nextBoolean()) (a shl 24) or 0x3A2A1A else (a shl 24) or 0xFFFFFF
    }
    Bitmap.createBitmap(px, n, n, Bitmap.Config.ARGB_8888).asImageBitmap()
}

fun Modifier.paperGrain(): Modifier = drawWithCache {
    val brush = ShaderBrush(ImageShader(grain, TileMode.Repeated, TileMode.Repeated))
    onDrawWithContent {
        drawContent()
        drawRect(brush, alpha = 0.6f)
    }
}

@Composable
fun CommonplaceTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val scheme: ColorScheme = if (dark) Dark else Light
    val extra = if (dark) {
        Extra(highlight = Color(0xFF5A4818), onHighlight = Color(0xFFFBEFC8), unverified = Color(0xFFE8A25C), good = Color(0xFF9CC48F))
    } else {
        Extra(highlight = Color(0xFFF1DB98), onHighlight = Color(0xFF2A2108), unverified = Color(0xFFA3561B), good = Color(0xFF3F6B3A))
    }
    androidx.compose.runtime.CompositionLocalProvider(LocalExtra provides extra) {
        MaterialTheme(colorScheme = scheme, typography = AppTypography, shapes = AppShapes, content = content)
    }
}
