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
