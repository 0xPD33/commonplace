package app.commonplace.ui

import android.provider.Settings
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

/** True when the user turned animations off in the Android settings ("Remove animations"). */
@Composable
fun rememberReduceMotion(): Boolean {
    val ctx = LocalContext.current
    return remember { Settings.Global.getFloat(ctx.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }
}

/** Keys whose entrance already played in this process: an item scrolled back into view does not rise again. */
private val entered = HashSet<Any>()

/** 0 to 1 the first time [key] appears, then always 1. A null [key] or "Remove animations" skips to 1. */
@Composable
fun rememberSweep(key: Any?, delayMs: Int = 0, durationMs: Int = 420): Float {
    val reduce = rememberReduceMotion()
    val first = remember(key) { key != null && !reduce && entered.add(key) }
    val p = remember(key) { Animatable(if (first) 0f else 1f) }
    LaunchedEffect(key) {
        if (first) {
            delay(delayMs.toLong())
            p.animateTo(1f, tween(durationMs, easing = FastOutSlowInEasing))
        }
    }
    return p.value
}

/**
 * Fade in and rise by [rise] the first time an element with [key] appears, after [delayMs].
 * Only transform and alpha change, so the layout never moves.
 */
fun Modifier.riseIn(key: Any, delayMs: Int = 0, rise: Dp = 16.dp, enabled: Boolean = true): Modifier = composed {
    val reduce = rememberReduceMotion()
    val first = remember(key) { entered.add(key) }
    if (!enabled || !first || reduce) return@composed this
    val p = remember(key) { Animatable(0f) }
    LaunchedEffect(key) {
        delay(delayMs.toLong())
        p.animateTo(1f, tween(320, easing = FastOutSlowInEasing))
    }
    val px = with(LocalDensity.current) { rise.toPx() }
    graphicsLayer {
        alpha = p.value
        translationY = (1f - p.value) * px
    }
}
