package app.commonplace

import android.app.Application
import android.content.ComponentCallbacks2
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import app.commonplace.engine.EngineHolder

class CommonplaceApp : Application() {
    lateinit var engine: EngineHolder
        private set

    override fun onCreate() {
        super.onCreate()
        engine = EngineHolder(this)
        engine.start()
        ProcessLifecycleOwner.get().lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onStart(owner: LifecycleOwner) = engine.loadModelOnScreen()
            override fun onStop(owner: LifecycleOwner) = engine.unloadModelOverHiddenLimit()
        })
    }

    @Suppress("DEPRECATION")
    override fun onTrimMemory(level: Int) {
        super.onTrimMemory(level)
        // Backgrounded under pressure: drop the LLM, keep the retrieval readers.
        if (level >= ComponentCallbacks2.TRIM_MEMORY_BACKGROUND) engine.unloadModel()
    }
}
