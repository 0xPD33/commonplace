package app.commonplace.engine

import android.content.Context
import android.os.Build
import android.os.OutcomeReceiver
import android.os.PowerMonitor
import android.os.PowerMonitorReadings
import android.os.health.SystemHealthManager
import androidx.annotation.RequiresApi
import app.commonplace.BuildConfig
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Energy per answer from the phone's power rails (ODPM) and modeled consumers, Android 15 and later. Each answer
 * appends one line to `files/energy.jsonl`: wall time and the energy in microwatt-seconds per monitor, keyed
 * `t<type>:<name>` (`PowerMonitor.getType`: measured rail or modeled consumer).
 * Debug builds only, because the file holds the user's questions. Phones without power monitors (and older Android)
 * write nothing.
 */
class PowerMeter(private val context: Context) {
    private val shm = context.getSystemService(SystemHealthManager::class.java)
    private var monitors: List<PowerMonitor>? = null

    /** The accumulated energy of every monitor, or null when the phone has none. Blocks for at most 2 s. */
    fun read(): Map<String, Long>? {
        if (!BuildConfig.DEBUG || Build.VERSION.SDK_INT < 35 || shm == null) return null
        return readV35()
    }

    @RequiresApi(35)
    private fun readV35(): Map<String, Long>? {
        val list = monitors ?: run {
            val done = CountDownLatch(1)
            var got: List<PowerMonitor> = emptyList()
            shm.getSupportedPowerMonitors(null) { got = it; done.countDown() }
            done.await(2, TimeUnit.SECONDS)
            got.also { monitors = it }
        }
        if (list.isEmpty()) return null
        val done = CountDownLatch(1)
        var readings: PowerMonitorReadings? = null
        shm.getPowerMonitorReadings(list, null, object : OutcomeReceiver<PowerMonitorReadings, RuntimeException> {
            override fun onResult(r: PowerMonitorReadings) { readings = r; done.countDown() }
            override fun onError(e: RuntimeException) { done.countDown() }
        })
        done.await(2, TimeUnit.SECONDS)
        val r = readings ?: return null
        // The type prefix separates measured rails from modeled consumers, which overlap them.
        return list.associate { "t${it.type}:${it.name}" to r.getConsumedEnergy(it) }
    }

    /** Appends the energy between two [read]s; monitors that went backwards or are missing are skipped. */
    fun log(query: String, model: String?, startMs: Long, before: Map<String, Long>?, after: Map<String, Long>?) {
        if (before == null || after == null) return
        val rails = JSONObject()
        for ((k, v) in after) before[k]?.let { b -> if (v >= b) rails.put(k, v - b) }
        val line = JSONObject()
            .put("ts_ms", startMs)
            .put("wall_ms", System.currentTimeMillis() - startMs)
            .put("query", query)
            .put("model", model)
            .put("energy_uws", rails)
        File(context.filesDir, "energy.jsonl").appendText(line.toString() + "\n")
    }
}
