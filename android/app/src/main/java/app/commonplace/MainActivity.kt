package app.commonplace

import android.Manifest
import android.content.ContentResolver
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.IntentCompat
import androidx.core.os.BundleCompat
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import app.commonplace.ui.rememberReduceMotion
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import app.commonplace.ui.ArticleScreen
import app.commonplace.ui.AskScreen
import app.commonplace.ui.AskViewModel
import app.commonplace.ui.CommonplaceTheme
import app.commonplace.ui.DiagnosticsScreen
import app.commonplace.ui.HistoryScreen
import app.commonplace.ui.LibraryScreen
import app.commonplace.ui.LibraryViewModel
import app.commonplace.ui.SharedDocumentDialog
import app.commonplace.ui.LicensesScreen
import app.commonplace.ui.NoticeScreen
import app.commonplace.ui.PassageScreen
import app.commonplace.ui.SettingsScreen
import app.commonplace.ui.paperGrain

/** What another app sent: text to ask about, or a file for My documents. */
private sealed interface Shared {
    data class Draft(val text: String) : Shared
    data class Document(val uri: Uri) : Shared
}

private const val SHARED_DOCUMENT = "shared_document"

class MainActivity : ComponentActivity() {
    private var incoming by mutableStateOf<Shared?>(null)

    // The app reads a shared URI with its own rights, so a sender must not be able to name the app's private files.
    private fun readableShare(uri: Uri) = uri.scheme == ContentResolver.SCHEME_CONTENT &&
        uri.authority?.let { packageManager.resolveContentProvider(it, 0)?.packageName } != packageName

    private fun shared(intent: Intent?): Shared? = when (intent?.action) {
        Intent.ACTION_PROCESS_TEXT -> intent.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT)?.let { Shared.Draft(it.toString()) }
        Intent.ACTION_SEND -> {
            val stream = IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)
            if (stream != null) Shared.Document(stream).takeIf { readableShare(stream) }
            else intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.let { Shared.Draft(it.toString()) }
        }
        else -> null
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        shared(intent)?.let { incoming = it }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        (incoming as? Shared.Document)?.let { outState.putParcelable(SHARED_DOCUMENT, it.uri) }
    }

    @OptIn(ExperimentalComposeUiApi::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Paper runs under the 3-button nav bar too; the default scrim is a white strip.
        enableEdgeToEdge(navigationBarStyle = SystemBarStyle.auto(Color.TRANSPARENT, Color.TRANSPARENT))
        window.isNavigationBarContrastEnforced = false
        val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
        if (savedInstanceState == null) {
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
            // Recents replays the intent that started the task; that share is already handled.
            if (intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY == 0) incoming = shared(intent)
        } else {
            BundleCompat.getParcelable(savedInstanceState, SHARED_DOCUMENT, Uri::class.java)?.let { incoming = Shared.Document(it) }
        }
        setContent {
            CommonplaceTheme {
                // testTagsAsResourceId lets adb/uiautomator drive the app by tag (scripts/e2e-emulator.sh).
                Surface(Modifier.semantics { testTagsAsResourceId = true }.paperGrain(),color = MaterialTheme.colorScheme.background) {
                    val nav = rememberNavController()
                    // A second tap during the page animation must not pop the first page, which leaves a blank screen.
                    val back: () -> Unit = { if (nav.previousBackStackEntry != null) nav.popBackStack() }
                    // Activity scope, so the screen stays on while an answer streams behind a source page too:
                    // a hidden app gets a 3 GiB memory limit on Android 17, which swaps the model out mid-answer.
                    val ask: AskViewModel = viewModel()
                    // Activity scope too: an install started on the Ask screen and one started in Library share one result.
                    val packs: LibraryViewModel = viewModel()
                    LaunchedEffect(incoming) {
                        (incoming as? Shared.Draft)?.let {
                            ask.receiveDraft(it.text)
                            nav.popBackStack("ask", inclusive = false)
                            incoming = null
                        }
                    }
                    (incoming as? Shared.Document)?.let { d ->
                        SharedDocumentDialog(
                            d.uri,
                            packs,
                            onDismiss = { incoming = null },
                            onAdded = {
                                incoming = null
                                nav.navigate("library")
                            },
                        )
                    }
                    val view = LocalView.current
                    val answering = ask.busy
                    DisposableEffect(answering) {
                        view.keepScreenOn = answering
                        onDispose { view.keepScreenOn = false }
                    }
                    // A new page slides in a short way from the right; Back reverses it. Fades only when animations are off.
                    val reduce = rememberReduceMotion()
                    val ease = FastOutSlowInEasing
                    NavHost(
                        nav,
                        startDestination = "ask",
                        // The old page fades out before the new one fades in, so the two never show on top of each other.
                        enterTransition = { if (reduce) fadeIn(tween(0)) else slideInHorizontally(tween(300, easing = ease)) { it / 5 } + fadeIn(tween(210, delayMillis = 90)) },
                        exitTransition = { if (reduce) fadeOut(tween(0)) else slideOutHorizontally(tween(300, easing = ease)) { -it / 12 } + fadeOut(tween(90)) },
                        popEnterTransition = { if (reduce) fadeIn(tween(0)) else slideInHorizontally(tween(300, easing = ease)) { -it / 12 } + fadeIn(tween(210, delayMillis = 90)) },
                        popExitTransition = { if (reduce) fadeOut(tween(0)) else slideOutHorizontally(tween(300, easing = ease)) { it / 5 } + fadeOut(tween(90)) },
                    ) {
                        composable("ask") {
                            AskScreen(
                                onDetails = { o ->
                                    nav.navigate("passage/${Uri.encode(o.source.packId)}/${o.source.passageId}?h=${Uri.encode(o.highlight)}")
                                },
                                onArticle = { pack, article, focus -> nav.navigate("article/${Uri.encode(pack)}/$article?f=$focus") },
                                onLibrary = { nav.navigate("library") },
                                onSettings = { nav.navigate("settings") },
                                onHistory = { nav.navigate("history") },
                                vm = ask,
                                packs = packs,
                                onMorePacks = { nav.navigate("library?more=true") },
                            )
                        }
                        composable(
                            "passage/{pack}/{pid}?h={h}",
                            arguments = listOf(
                                navArgument("pid") { type = NavType.LongType },
                                navArgument("h") { defaultValue = "" },
                            ),
                        ) { e ->
                            val a = e.arguments!!
                            PassageScreen(
                                packId = a.getString("pack")!!,
                                passageId = a.getLong("pid").toUInt(),
                                highlight = a.getString("h") ?: "",
                                onBack = back,
                                onArticle = { pack, article, focus -> nav.navigate("article/${Uri.encode(pack)}/$article?f=$focus") },
                            )
                        }
                        composable(
                            "article/{pack}/{aid}?f={f}",
                            arguments = listOf(navArgument("aid") { type = NavType.LongType }, navArgument("f") { type = NavType.LongType; defaultValue = 0L }),
                        ) { e ->
                            val a = e.arguments!!
                            ArticleScreen(a.getString("pack")!!, a.getLong("aid").toUInt(), a.getLong("f").toUInt(), onBack = back)
                        }
                        composable("history") { HistoryScreen(ask, onBack = back) }
                        composable(
                            "library?more={more}",
                            arguments = listOf(navArgument("more") { type = NavType.BoolType; defaultValue = false }),
                        ) { e ->
                            LibraryScreen(
                                vm = packs,
                                moreFirst = e.arguments!!.getBoolean("more"),
                                onBack = back,
                                onNotice = { id -> nav.navigate("notice/${Uri.encode(id)}") },
                                onAskDocument = { id, title ->
                                    ask.searchOnly(id, title)
                                    back()
                                },
                            )
                        }
                        composable("notice/{pack}") { e -> NoticeScreen(e.arguments!!.getString("pack")!!, onBack = back) }
                        composable("settings") {
                            SettingsScreen(onBack = back, onDiagnostics = { nav.navigate("diagnostics") }, onLicenses = { nav.navigate("licenses") })
                        }
                        composable("licenses") { LicensesScreen(onBack = back) }
                        composable("diagnostics") { DiagnosticsScreen(onBack = back) }
                    }
                }
            }
        }
    }
}
