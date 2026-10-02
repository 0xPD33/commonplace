package app.commonplace

import android.app.Application
import android.content.ComponentCallbacks2
import app.commonplace.engine.EngineHolder

class CommonplaceApp : Application() {
    lateinit var engine: EngineHolder
        private set

    override fun onCreate() {
        super.onCreate()
        engine = EngineHolder(this)
        engine.start()
    }

    @Suppress("DEPRECATION")
    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        // Backgrounded under pressure: drop the LLM, keep the retrieval readers (PLAN.md §8.2).
        if (level >= ComponentCallbacks2.TRIM_MEMORY_BACKGROUND) engine.unloadModel()
    }
}
