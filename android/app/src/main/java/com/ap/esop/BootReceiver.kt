package com.ap.esop

import android.app.*
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build

class BootReceiver:BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) {
        if(intent.action!=Intent.ACTION_BOOT_COMPLETED)return
        val enabled=runCatching {DeviceSettings(context).load()?.bootEnabled==true}.getOrDefault(false)
        if(!enabled)return
        val launch=Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if(Build.VERSION.SDK_INT<29){runCatching {context.startActivity(launch)};return}
        val manager=context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("esop_start","ESOP 启动提醒",NotificationManager.IMPORTANCE_DEFAULT))
        val pending=PendingIntent.getActivity(context,0,launch,PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification=Notification.Builder(context,"esop_start").setSmallIcon(android.R.drawable.ic_media_play).setContentTitle("ESOP 已准备就绪")
            .setContentText("点击恢复本地播放；无人值守启动请配置固件自启动或设备管理策略")
            .setContentIntent(pending).setAutoCancel(true).build()
        runCatching {manager.notify(1,notification)}
    }
}
