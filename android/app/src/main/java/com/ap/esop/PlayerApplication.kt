package com.ap.esop

import android.app.Application
import kotlinx.coroutines.*

class PlayerApplication:Application() {
    val scope=CoroutineScope(SupervisorJob()+Dispatchers.IO)
    val settings by lazy { DeviceSettings(this) }
    val repository by lazy { Repository(PlayerDatabase.open(this)) }
    val cache by lazy { MediaCache(java.io.File(filesDir,"media")) }
    var lastSave:Deferred<Unit>?=null
}
