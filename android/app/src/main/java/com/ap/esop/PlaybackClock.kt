package com.ap.esop

/** Monotonic timer, independent of Android and wall-clock adjustments. */
class PlaybackClock(private val now:()->Long) {
    var paused=true;private set
    private var remaining=0L
    private var started=0L
    fun load(duration:Long,pause:Boolean) { remaining=duration.coerceAtLeast(0);paused=pause;started=now() }
    fun left():Long = if(paused) remaining else (remaining-(now()-started)).coerceAtLeast(0)
    fun pause() { if(!paused) { remaining=left();paused=true } }
    fun resume() { if(paused) { started=now();paused=false } }
}
