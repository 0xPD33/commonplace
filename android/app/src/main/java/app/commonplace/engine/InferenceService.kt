package app.commonplace.engine

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import app.commonplace.CommonplaceApp
import app.commonplace.MainActivity

/**
 * Keeps the process (and the loaded model) alive while the app is in use and for a few
 * minutes after it goes to the background. Type specialUse: dataSync is capped at 6 h/day.
 */
class InferenceService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private val stopper = Runnable {
        (application as CommonplaceApp).engine.unloadModel()
        stopSelf()
    }
    private val observer = object : DefaultLifecycleObserver {
        override fun onStart(owner: LifecycleOwner) = handler.removeCallbacks(stopper)
        override fun onStop(owner: LifecycleOwner) {
            handler.postDelayed(stopper, KEEP_ALIVE_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        ProcessLifecycleOwner.get().lifecycle.addObserver(observer)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopper.run()
            return START_NOT_STICKY
        }
        // Throws if the app went to the background before this ran; the model then simply is not kept alive.
        runCatching { startForeground(NOTIFICATION_ID, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE) }.onFailure { stopSelf() }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        ProcessLifecycleOwner.get().lifecycle.removeObserver(observer)
        handler.removeCallbacks(stopper)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun notification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Model in memory", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, InferenceService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_search)
            .setContentTitle("Commonplace is ready")
            .setContentText("The offline model stays loaded for quick answers.")
            .setContentIntent(open)
            .addAction(Notification.Action.Builder(null, "Unload", stop).build())
            .setOngoing(true)
            .build()
    }

    companion object {
        private const val CHANNEL = "model"
        private const val NOTIFICATION_ID = 7
        private const val ACTION_STOP = "app.commonplace.STOP"
        private const val KEEP_ALIVE_MS = 5 * 60_000L

        fun start(context: Context) {
            runCatching { context.startForegroundService(Intent(context, InferenceService::class.java)) }
        }
    }
}
