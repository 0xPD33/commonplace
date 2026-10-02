package app.commonplace

import android.Manifest
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.lifecycle.viewmodel.compose.viewModel
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
import app.commonplace.ui.PassageScreen
import app.commonplace.ui.SettingsScreen

class MainActivity : ComponentActivity() {
    @OptIn(ExperimentalComposeUiApi::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {}
        if (savedInstanceState == null) notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        setContent {
            CommonplaceTheme {
                // testTagsAsResourceId lets adb/uiautomator drive the app by tag (scripts/e2e-emulator.sh).
                Surface(Modifier.semantics { testTagsAsResourceId = true }, color = MaterialTheme.colorScheme.background) {
                    val nav = rememberNavController()
                    // Activity scope, so the screen stays on while an answer streams behind a source page too:
                    // a hidden app gets a 3 GiB memory limit on Android 17, which swaps the model out mid-answer.
                    val ask: AskViewModel = viewModel()
                    val view = LocalView.current
                    val answering = ask.busy
                    DisposableEffect(answering) {
                        view.keepScreenOn = answering
                        onDispose { view.keepScreenOn = false }
                    }
                    NavHost(nav, startDestination = "ask") {
                        composable("ask") {
                            AskScreen(
                                onOpen = { o ->
                                    nav.navigate("passage/${Uri.encode(o.source.packId)}/${o.source.passageId}?h=${Uri.encode(o.highlight)}")
                                },
                                onLibrary = { nav.navigate("library") },
                                onSettings = { nav.navigate("settings") },
                                onDiagnostics = { nav.navigate("diagnostics") },
                                onHistory = { nav.navigate("history") },
                                vm = ask,
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
                                onBack = { nav.popBackStack() },
                                onArticle = { pack, article, focus -> nav.navigate("article/${Uri.encode(pack)}/$article?f=$focus") },
                            )
                        }
                        composable(
                            "article/{pack}/{aid}?f={f}",
                            arguments = listOf(navArgument("aid") { type = NavType.LongType }, navArgument("f") { type = NavType.LongType; defaultValue = 0L }),
                        ) { e ->
                            val a = e.arguments!!
                            ArticleScreen(a.getString("pack")!!, a.getLong("aid").toUInt(), a.getLong("f").toUInt(), onBack = { nav.popBackStack() })
                        }
                        composable("history") { HistoryScreen(ask, onBack = { nav.popBackStack() }) }
                        composable("library") { LibraryScreen(onBack = { nav.popBackStack() }) }
                        composable("settings") { SettingsScreen(onBack = { nav.popBackStack() }, onDiagnostics = { nav.navigate("diagnostics") }) }
                        composable("diagnostics") { DiagnosticsScreen(onBack = { nav.popBackStack() }) }
                    }
                }
            }
        }
    }
}
