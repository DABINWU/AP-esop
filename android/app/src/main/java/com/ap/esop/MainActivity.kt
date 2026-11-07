package com.ap.esop

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Color
import android.net.Uri
import android.os.*
import android.provider.Settings
import android.text.InputType
import android.view.*
import android.widget.*
import androidx.activity.ComponentActivity
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.*
import java.io.File

class MainActivity:ComponentActivity(),PlayerScreen {
    private val app get()=application as PlayerApplication
    private var engine:PlayerEngine?=null
    private var player:ExoPlayer?=null
    private lateinit var playerView:PlayerView
    private lateinit var image:TouchImageView
    private lateinit var title:TextView
    private lateinit var status:TextView
    private lateinit var itemLabel:TextView
    private lateinit var overlay:TextView
    private lateinit var pauseButton:Button
    private val modules=linkedMapOf("production" to "SOP", "inspection" to "SIP", "check_video" to "点检视频", "custom" to "自定义")
    private val moduleButtons=mutableMapOf<String,Button>()
    private var uiScope=CoroutineScope(SupervisorJob()+Dispatchers.Main.immediate)
    private var decodeJob:Job?=null
    private var renderGeneration=0L
    private var firstFrame:(()->Unit)?=null
    private var ended:(()->Unit)?=null
    private var failed:((String)->Unit)?=null
    private var activeItem:PlayItem?=null
    private var started=false
    private var settingsOpen=false
    private fun dp(value:Int)=(value*resources.displayMetrics.density).toInt()
    private fun text(value:String,size:Float=16f)=TextView(this).apply {text=value;textSize=size;setTextColor(Color.WHITE);gravity=Gravity.CENTER_VERTICAL;setPadding(dp(10),0,dp(10),0)}
    private fun button(value:String,action:()->Unit)=Button(this).apply {text=value;textSize=16f;isAllCaps=false;minHeight=dp(48);setOnClickListener {action()} }
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility=View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        val root=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setBackgroundColor(Color.rgb(20,34,77))}
        val header=LinearLayout(this).apply {gravity=Gravity.CENTER_VERTICAL}
        header.addView(ImageView(this).apply {setImageResource(R.drawable.company_logo);setPadding(dp(5),dp(5),dp(5),dp(5));setOnLongClickListener{admin();true}},LinearLayout.LayoutParams(dp(52),dp(52)))
        title=text("艾普 ESOP · 等待下发",20f);title.setOnLongClickListener{admin();true};header.addView(title,LinearLayout.LayoutParams(0,dp(52),1f))
        header.addView(button("管理员"){admin()},LinearLayout.LayoutParams(dp(105),dp(48)))
        root.addView(header)
        status=text("首次配置后连接车间中转",13f);root.addView(status,LinearLayout.LayoutParams(-1,dp(35)))
        val frame=FrameLayout(this).apply {setBackgroundColor(Color.WHITE)}
        image=TouchImageView(this).apply {onZoom={engine?.setPaused(true)}}
        playerView=PlayerView(this).apply {useController=false;resizeMode=AspectRatioFrameLayout.RESIZE_MODE_FIT;setKeepContentOnPlayerReset(true);visibility=View.GONE}
        frame.addView(image,FrameLayout.LayoutParams(-1,-1));frame.addView(playerView,FrameLayout.LayoutParams(-1,-1))
        overlay=text("等待下发",24f).apply {gravity=Gravity.CENTER;setTextColor(Color.rgb(40,65,110));setBackgroundColor(Color.WHITE)}
        frame.addView(overlay,FrameLayout.LayoutParams(-1,-1));root.addView(frame,LinearLayout.LayoutParams(-1,0,1f))
        itemLabel=text("—",14f);root.addView(itemLabel,LinearLayout.LayoutParams(-1,dp(28)))
        val categories=LinearLayout(this)
        modules.forEach { (id,label)->val b=button(label){engine?.category(id)};moduleButtons[id]=b;categories.addView(b,LinearLayout.LayoutParams(0,dp(48),1f)) }
        root.addView(categories)
        val controls=LinearLayout(this)
        controls.addView(button("上一项"){engine?.move(-1)},LinearLayout.LayoutParams(0,dp(56),1f))
        pauseButton=button("暂停"){engine?.togglePause()};controls.addView(pauseButton,LinearLayout.LayoutParams(0,dp(56),1f))
        controls.addView(button("下一项 / 跳过"){engine?.move(1)},LinearLayout.LayoutParams(0,dp(56),1f))
        controls.addView(button("重播当前项"){engine?.replay()},LinearLayout.LayoutParams(0,dp(56),1f))
        controls.addView(button("退出放大"){image.resetZoom()},LinearLayout.LayoutParams(0,dp(56),1f))
        root.addView(controls);setContentView(root)
    }
    override fun onStart() {
        super.onStart();started=true
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        uiScope=CoroutineScope(SupervisorJob()+Dispatchers.Main.immediate)
        val config=try {app.settings.load()} catch(e:Exception){null}
        if(config==null)settings(null) else begin(config)
    }
    private fun begin(config:DeviceConfig) {
        if(!started)return
        engine?.stop();engine=PlayerEngine(app,config,this).also {it.start()}
    }
    override fun onStop() {
        started=false;engine?.stop();engine=null;renderGeneration++;decodeJob?.cancel();uiScope.cancel()
        playerView.player=null;player?.release();player=null;image.clear()
        firstFrame=null;ended=null;failed=null
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        super.onStop()
    }
    private fun ensurePlayer():ExoPlayer {
        player?.let {return it}
        return ExoPlayer.Builder(this).build().also { value ->
            player=value;playerView.player=value
            value.addListener(object:Player.Listener {
                override fun onRenderedFirstFrame(){firstFrame?.let {firstFrame=null;it()}}
                override fun onPlaybackStateChanged(playbackState:Int){if(playbackState==Player.STATE_ENDED)ended?.invoke()}
                override fun onPlayerError(error:PlaybackException){failed?.invoke("视频无法播放（${error.errorCodeName}）")}
            })
        }
    }
    override fun render(item:PlayItem,file:File,paused:Boolean,position:Long,ready:()->Unit,ended:()->Unit,failed:(String)->Unit) {
        renderGeneration++;val generation=renderGeneration;decodeJob?.cancel()
        activeItem=item;this.firstFrame=null;this.ended=null;this.failed=null
        if(item.type=="video") {
            playerView.visibility=View.VISIBLE;image.visibility=View.GONE;overlay.visibility=View.GONE
            val p=ensurePlayer();p.stop();p.clearMediaItems()
            this.firstFrame={if(generation==renderGeneration)ready()}
            this.ended={if(generation==renderGeneration)ended()};this.failed={if(generation==renderGeneration)failed(it)}
            p.setMediaItem(MediaItem.fromUri(Uri.fromFile(file)));p.prepare();p.seekTo(position);p.playWhenReady=!paused
        } else {
            player?.pause();player?.stop();player?.clearMediaItems()
            decodeJob=uiScope.launch {
                try {
                    val bitmap=withContext(Dispatchers.IO) {
                        val bounds=BitmapFactory.Options().apply {inJustDecodeBounds=true};BitmapFactory.decodeFile(file.path,bounds)
                        check(bounds.outWidth>0 && bounds.outHeight>0) {"图片无法解码"}
                        var sample=1;while(bounds.outWidth.toLong()*bounds.outHeight/(sample.toLong()*sample)>4_000_000 || bounds.outWidth/sample>4096 || bounds.outHeight/sample>4096)sample*=2
                        BitmapFactory.decodeFile(file.path,BitmapFactory.Options().apply {inSampleSize=sample;inPreferredConfig=android.graphics.Bitmap.Config.ARGB_8888}) ?: error("图片加载失败")
                    }
                    if(generation!=renderGeneration || !started){bitmap.recycle();return@launch}
                    playerView.visibility=View.GONE;image.visibility=View.VISIBLE;overlay.visibility=View.GONE
                    image.display(bitmap){if(generation==renderGeneration)ready()}
                } catch(e:CancellationException){throw e} catch(e:Throwable){if(generation==renderGeneration)failed("图片无法加载，请核对文件或终端内存")}
            }
        }
    }
    override fun pause(paused:Boolean) {if(activeItem?.type=="video")player?.playWhenReady=!paused}
    override fun videoPosition()=if(activeItem?.type=="video")player?.currentPosition ?: 0 else 0
    override fun update(manifest:Manifest?,category:String,index:Int,paused:Boolean,status:String,error:String?,progress:Progress?) {
        title.text=manifest?.name ?: "艾普 ESOP · 等待下发"
        this.status.text=buildString {append(status);if(progress!=null)append(" · 下载 ${progress.completedFiles}/${progress.totalFiles}（${progress.receivedBytes*100/progress.totalBytes.coerceAtLeast(1)}%）");if(error!=null)append(" · $error")}
        pauseButton.text=if(paused)"继续" else "暂停"
        val count=manifest?.moduleItems(category)?.size ?: 0
        itemLabel.text=if(count>0)"${modules[category]} · ${index+1} / $count"+(if(paused)" · 已暂停" else " · 循环播放") else "等待下发"
        moduleButtons.forEach { (id,b)->val size=manifest?.moduleItems(id)?.size ?: 0;b.text="${modules[id]} ($size)";b.isEnabled=size>0;b.alpha=if(id==category)1f else .7f }
        if(manifest==null){overlay.text=error ?: "等待下发";overlay.visibility=View.VISIBLE}
    }
    @Deprecated("Back exits only through administrator settings")
    override fun onBackPressed(){admin()}
    private fun admin() {
        if(settingsOpen)return
        val config=try{app.settings.load()}catch(e:Exception){null}
        if(config==null){settings(null);return}
        val pin=EditText(this).apply {inputType=InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD;hint="管理员设置密码"}
        val dialog=AlertDialog.Builder(this).setTitle("管理员验证").setView(pin).setNegativeButton("取消",null).setPositiveButton("验证",null).create()
        dialog.setOnShowListener {dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
            val value=pin.text.toString();pin.text.clear();uiScope.launch {
                val valid=withContext(Dispatchers.IO){app.settings.verifyPin(value,config)}
                if(valid){dialog.dismiss();settings(config)}else pin.error="设置密码错误，连续5次错误锁定1分钟"
            }
        } };dialog.show()
    }
    private fun settings(old:DeviceConfig?) {
        if(settingsOpen)return
        settingsOpen=true
        val column=LinearLayout(this).apply {orientation=LinearLayout.VERTICAL;setPadding(dp(20),dp(5),dp(20),dp(5))}
        fun input(label:String,value:String="",secret:Boolean=false):EditText {
            column.addView(TextView(this).apply {text=label;setTextColor(Color.DKGRAY)})
            return EditText(this).apply {setText(value);inputType=InputType.TYPE_CLASS_TEXT or if(secret)InputType.TYPE_TEXT_VARIATION_PASSWORD else InputType.TYPE_TEXT_VARIATION_NORMAL;column.addView(this)}
        }
        val address=input("中转地址（只访问车间中转）",old?.relayUrl ?: "http://")
        val id=input("唯一 device_id（在中心预先登记）",old?.deviceId.orEmpty()).apply {isEnabled=old==null}
        val secret=input(if(old==null)"终端访问密钥" else "终端访问密钥（留空保留原值）",secret=true)
        val pin=input(if(old==null)"设置管理员密码（至少6位）" else "新管理员设置密码（留空不变）",secret=true)
        val confirm=input("确认管理员设置密码",secret=true)
        val boot=CheckBox(this).apply {text="开机启动适配（新系统可能仅提供启动通知）";isChecked=old?.bootEnabled==true};column.addView(boot)
        val feedback=TextView(this).apply {setTextColor(Color.rgb(150,55,40));text="设备：${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE} / API ${Build.VERSION.SDK_INT}\n编号不得复用；密钥只在本机加密保存。"};column.addView(feedback)
        fun entered():DeviceConfig {require(pin.text.toString()==confirm.text.toString()){ "管理员设置密码两次输入不同" };return app.settings.create(address.text.toString(),id.text.toString(),secret.text.toString(),pin.text.toString(),old,boot.isChecked)}
        val tools=LinearLayout(this)
        tools.addView(button("连接测试") {uiScope.launch {try {
            val candidate=entered();feedback.text="正在验证中转与登记编号…"
            val reply=RelayClient(candidate.relayUrl,candidate.deviceKey).let { c->try{c.sync(candidate.deviceId,engine?.currentState()?.knownRevision ?: 0)}finally{c.close()} }
            val seq=withContext(Dispatchers.IO){app.repository.dao.counter("report_seq") ?: 0}
            require(reply.reportSeqFloor<=seq){"此编号已经使用过，请在中心登记新编号"}
            feedback.text="连接成功，编号已登记；"+(if(reply.assignment==null)"等待中心下发" else "已存在目标安排")
        }catch(e:Exception){feedback.text=e.message ?: "连接测试失败"}} })
        if(old!=null){tools.addView(button("立即同步"){engine?.forceSync();feedback.text="已请求同步"});tools.addView(button("系统设置"){startActivity(Intent(Settings.ACTION_SETTINGS))})}
        column.addView(tools)
        if(old!=null)column.addView(button("申请屏幕固定模式"){runCatching{startLockTask()}.onFailure{feedback.text="固件未允许固定模式，请在系统管理中配置"}})
        val scroll=ScrollView(this).apply {addView(column)}
        val dialog=AlertDialog.Builder(this).setTitle("终端管理员设置").setView(scroll).setNegativeButton(if(old==null)"稍后设置" else "关闭",null).setPositiveButton("保存",null).create()
        dialog.setOnDismissListener {settingsOpen=false}
        dialog.setOnShowListener {dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {uiScope.launch {
            try {
                val candidate=entered()
                val reply=RelayClient(candidate.relayUrl,candidate.deviceKey).let { c->try{c.sync(candidate.deviceId,engine?.currentState()?.knownRevision ?: 0)}finally{c.close()} }
                val seq=withContext(Dispatchers.IO){app.repository.dao.counter("report_seq") ?: 0}
                require(reply.reportSeqFloor<=seq){"此编号已被其他安装使用，请登记新编号"}
                withContext(Dispatchers.IO){app.settings.save(candidate)}
                secret.text.clear();pin.text.clear();confirm.text.clear();dialog.dismiss()
                if(candidate.bootEnabled && Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS),1)
                begin(candidate)
            } catch(e:Exception){feedback.text=e.message ?: "保存失败，请检查中转连接"}
        }} };dialog.show()
    }
}
