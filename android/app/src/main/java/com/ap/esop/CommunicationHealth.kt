package com.ap.esop

object CommunicationHealth {
    fun state(online:Boolean,hasError:Boolean,reportFailed:Boolean,lastSync:Long,lastReport:Long,now:Long,thresholdSeconds:Long):String=when {
        lastSync==0L && !hasError && !reportFailed -> "unknown"
        !online || reportFailed || (lastSync>0 && now-lastSync>thresholdSeconds*1000) || (lastReport>0 && now-lastReport>thresholdSeconds*1000) -> "offline"
        lastReport==0L -> "pending"
        else -> "online"
    }
}
