package com.ap.esop

import com.google.gson.JsonParser
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File

data class PlayerInfo(val deviceId:String,val deviceName:String,val assignedAt:String?,val communication:String,val lastContactAt:String?)

interface PlayerScreen {
    fun render(item:PlayItem,file:File,paused:Boolean,position:Long,ready:()->Unit,ended:()->Unit,failed:(String)->Unit)
    fun pause(paused:Boolean)
    fun videoPosition():Long
    fun update(manifest:Manifest?,category:String,index:Int,paused:Boolean,status:String,error:String?,progress:Progress?,info:PlayerInfo)
}

class PlayerEngine(private val app:PlayerApplication,private val config:DeviceConfig,private val screen:PlayerScreen) {
    private val scope=CoroutineScope(SupervisorJob()+Dispatchers.Main.immediate)
    private val repo=app.repository
    private val client=RelayClient(config.relayUrl,config.deviceKey)
    private val cache=app.cache
    private var state=PlayerState()
    private var manifest:Manifest?=null
    private var desired:Assignment?=null
    private var proposal:Prepared?=null
    private var downloadJob:Job?=null
    private var heartbeat=10L
    private var alive=false
    private var confirmed=false
    private var identityVerified=false
    private var verified=false
    private var online=false
    private var deviceName=config.deviceId
    private var offlineSeconds=40L
    private var lastSync=0L
    @Volatile private var lastReport=0L
    @Volatile private var reportFailed=false
    @Volatile private var lastContactAt:String?=null
    private var lastError:String?=null
    private var networkError:String?=null
    private var transfer:Progress?=null
    private var renderGeneration=0L
    private var retryItem=0
    private var failedRevision=0L
    private var candidateFailures=0
    private var retryAt=0L
    private var lastProgressUi=0L
    private val downloadLock=Mutex()
    private val clock=PlaybackClock { android.os.SystemClock.elapsedRealtime() }
    private data class Prepared(val assignment:Assignment,val manifest:Manifest,val snapshot:Snapshot)
    fun currentState()=state
    fun start() {
        alive=true
        scope.launch {
          try {
            app.lastSave?.await()
            state=withContext(Dispatchers.IO){repo.load()}
            deviceName=withContext(Dispatchers.IO){app.settings.deviceName(config.deviceId)}
            identityVerified=withContext(Dispatchers.IO){(repo.dao.counter("report_seq") ?: 0)>0}
            desired=state.desiredJson?.let { Wire.gson.fromJson(it,Assignment::class.java) }
            restore()
            launch { while(isActive){ poll();delay(heartbeat*1000) } }
            launch { while(isActive){ emit();delay(heartbeat*1000) } }
            launch(Dispatchers.IO) { while(isActive){flush();delay(3000)} }
            launch { while(isActive){delay(200);if(confirmed && !state.paused && currentItem()?.type!="video" && clock.left()==0L) move(1);update()} }
            launch { while(isActive){delay(5000);persist()} }
          } catch(e:CancellationException){throw e} catch(e:Exception){android.util.Log.e("ESOPStorage","Player state initialization failed",e);lastError="本地播放状态无法恢复，请检查存储空间或设备设置";update()}
        }
    }
    private suspend fun restore() {
        val snapshot=state.activeVersion?.let { withContext(Dispatchers.IO){repo.dao.snapshot(it)} }
        val restored=snapshot?.let { runCatching { parse(it) }.getOrNull() }
        if(restored!=null && withContext(Dispatchers.IO){restored.items.map { it.file }.distinctBy { it.id }.all { cache.valid(it) }}) {
            manifest=restored
            if(restored.moduleItems(state.category).isEmpty())state=state.copy(category=restored.firstCategory(),itemIndex=0)
            val count=restored.moduleItems(state.category).size
            state=state.copy(itemIndex=state.itemIndex.coerceIn(0,count-1))
            showCurrent(true)
        } else if(state.previousVersion!=null) {
            state=state.copy(serial=state.serial+1,activeVersion=state.previousVersion,activeRevision=state.previousRevision,
                previousVersion=null,previousRevision=0,itemIndex=0,remainingMs=20000,videoPositionMs=0)
            withContext(Dispatchers.IO){repo.save(state)};restore()
        } else {manifest=null;confirmed=false;lastError=if(snapshot!=null)"本地缓存不完整，等待重新同步" else null;update()}
    }
    private fun parse(snapshot:Snapshot)=Wire.manifest(JsonParser.parseString(snapshot.manifest).asJsonObject,snapshot.sha256,snapshot.versionId)
    private suspend fun poll() {
        try {
            val reply=client.sync(config.deviceId,state.knownRevision)
            val sequence=withContext(Dispatchers.IO){repo.dao.counter("report_seq") ?: 0}
            check(reply.reportSeqFloor<=sequence) { "设备编号已被其他安装使用，请管理员分配新编号" }
            identityVerified=true
            online=true;networkError=null;heartbeat=reply.heartbeatSeconds.toLong().coerceIn(1,3600)
            lastSync=android.os.SystemClock.elapsedRealtime();lastContactAt=Wire.utc()
            offlineSeconds=(reply.offlineSeconds?.toLong() ?: 40L).coerceAtLeast(heartbeat+1)
            reply.deviceName?.takeIf {it.isNotBlank()}?.let {name->deviceName=name.take(120);withContext(Dispatchers.IO){runCatching {app.settings.rememberDeviceName(config.deviceId,deviceName)}}}
            val a=reply.assignment
            if(a==null){verified=state.activeVersion==null;update();return}
            require(a.deviceId==config.deviceId && a.assignmentRevision>=state.knownRevision) { "中转返回了过期安排" }
            Wire.id(a.playlistVersionId)
            if(desired?.assignmentRevision==a.assignmentRevision)require(desired?.playlistVersionId==a.playlistVersionId) { "相同安排编号对应不同内容" }
            if(a.assignmentRevision>state.knownRevision || desired==null) {
                proposal?.let { proposal=null;showCurrent(true) }
                downloadJob?.cancel()
                desired=a;state=state.copy(serial=state.serial+1,knownRevision=a.assignmentRevision,desiredJson=Wire.gson.toJson(a))
                withContext(Dispatchers.IO){repo.save(state)}
                candidateFailures=0;failedRevision=0;retryAt=0;transfer=null;lastError=null
            }
            verified=a.assignmentRevision==state.activeRevision && a.playlistVersionId==state.activeVersion
            if(verified){update();return}
            if(!reply.ready || reply.manifest==null || reply.manifestSha256==null){lastError=reply.error;update();return}
            if(proposal==null && downloadJob?.isActive!=true && System.currentTimeMillis()>=retryAt && (failedRevision!=a.assignmentRevision || candidateFailures<3)) {
                val content=Wire.manifest(reply.manifest,reply.manifestSha256,a.playlistVersionId)
                val snapshot=Snapshot(a.playlistVersionId,Wire.canonical(reply.manifest),reply.manifestSha256)
                withContext(Dispatchers.IO){repo.snapshot(snapshot)}
                downloadJob=scope.launch { downloadLock.withLock { cleanup();prepare(Prepared(a,content,snapshot)) } }
            }
            update()
        } catch(e:CancellationException){throw e} catch(e:Exception){online=false;verified=false;networkError=safeError(e);update()}
    }
    private suspend fun prepare(prepared:Prepared) {
        val a=prepared.assignment;val files=prepared.manifest.items.map { it.file }.distinctBy { it.id }
        val total=files.sumOf { it.sizeBytes };var done=0L;var completed=0
        try {
            for(file in files) {
                cache.fetch(client,file) { bytes -> withContext(Dispatchers.Main) {
                    transfer=Progress(a.playlistVersionId,done+bytes,total,completed,files.size)
                    if(System.currentTimeMillis()-lastProgressUi>200){lastProgressUi=System.currentTimeMillis();update()}
                } }
                done+=file.sizeBytes;completed++
                transfer=Progress(a.playlistVersionId,done,total,completed,files.size)
            }
            val fresh=client.sync(config.deviceId,state.knownRevision)
            if(fresh.assignment?.assignmentRevision!=a.assignmentRevision || fresh.assignment.playlistVersionId!=a.playlistVersionId || !fresh.ready) {
                scope.launch { poll() };return
            }
            require(client.activation(a)) { "目标已被替换，暂不激活" }
            if(!alive || desired?.assignmentRevision!=a.assignmentRevision)return
            proposal=prepared;showPrepared(prepared)
        } catch(e:CancellationException){throw e} catch(e:Exception){candidateFailure(a,safeError(e))}
    }
    private fun showPrepared(prepared:Prepared) {
        renderGeneration++;val generation=renderGeneration
        val item=prepared.manifest.moduleItems(prepared.manifest.firstCategory()).first()
        screen.render(item,cache.path(item.file),false,0,ready={
            if(alive && generation==renderGeneration && proposal===prepared)scope.launch { commit(prepared) }
        },ended={if(proposal==null && confirmed)move(1)},failed={message->
            if(alive && generation==renderGeneration){proposal=null;candidateFailure(prepared.assignment,message);showCurrent(true)}
        })
        update()
    }
    private suspend fun commit(prepared:Prepared) {
        val a=prepared.assignment
        if(!alive || desired?.assignmentRevision!=a.assignmentRevision)return
        val old=state
        val next=state.copy(serial=state.serial+1,activeVersion=a.playlistVersionId,activeRevision=a.assignmentRevision,
            previousVersion=state.activeVersion,previousRevision=state.activeRevision,category=prepared.manifest.firstCategory(),
            itemIndex=0,paused=false,remainingMs=((prepared.manifest.moduleItems(prepared.manifest.firstCategory()).first().durationSeconds ?: 20.0)*1000).toLong(),videoPositionMs=0)
        try {
            withContext(Dispatchers.IO){repo.save(next)}
            if(!alive || desired?.assignmentRevision!=a.assignmentRevision){proposal=null;showCurrent(true);return}
            state=next;manifest=prepared.manifest;proposal=null;confirmed=true;verified=true;transfer=null;lastError=null;retryItem=0
            clock.load(state.remainingMs,false);emit();update()
            scope.launch { downloadLock.withLock {cleanup()} }
        } catch(e:CancellationException){throw e} catch(e:Exception) {state=old;proposal=null;candidateFailure(a,"本地切换保存失败，继续旧内容");showCurrent(true)}
    }
    private fun candidateFailure(a:Assignment,message:String) {
        failedRevision=a.assignmentRevision;candidateFailures++;retryAt=System.currentTimeMillis()+30000
        lastError=message+if(candidateFailures>=3)"；已停止自动接管，请管理员检查后重试" else "；保留旧内容，稍后重试"
        transfer=null;update();scope.launch { emit() }
    }
    private fun currentItem()=manifest?.moduleItems(state.category)?.getOrNull(state.itemIndex)
    private fun showCurrent(restore:Boolean=false) {
        val item=currentItem() ?: run {confirmed=false;update();return}
        renderGeneration++;val generation=renderGeneration;confirmed=false
        val remaining=if(restore)state.remainingMs else ((item.durationSeconds ?: 20.0)*1000).toLong()
        clock.load(remaining,true)
        screen.render(item,cache.path(item.file),state.paused,if(restore)state.videoPositionMs else 0,ready={
            if(alive && generation==renderGeneration){confirmed=true;retryItem=0;if(desired?.assignmentRevision==state.activeRevision)lastError=null;clock.load(remaining,state.paused);scope.launch {emit()};update()}
        },ended={if(alive && generation==renderGeneration && !state.paused)move(1)},failed={message->
            if(alive && generation==renderGeneration){lastError=message;confirmed=false;retryItem++
                if(retryItem<3)scope.launch {delay(1500L*retryItem);if(alive && generation==renderGeneration)showCurrent(true)}
                else {state=state.copy(paused=true);lastError=message+"；重试已停止，可手动跳过或重播";scope.launch {persist();emit()}}
                update()
            }
        })
        update()
    }
    fun move(delta:Int) {
        if(proposal!=null)return
        val entries=manifest?.moduleItems(state.category).orEmpty();if(entries.isEmpty())return
        val index=(state.itemIndex+delta+entries.size)%entries.size
        state=state.copy(serial=state.serial+1,itemIndex=index,remainingMs=((entries[index].durationSeconds ?: 20.0)*1000).toLong(),videoPositionMs=0)
        retryItem=0;showCurrent();scope.launch {persist()}
    }
    fun replay(){if(currentItem()!=null)move(0)}
    fun togglePause()=setPaused(!state.paused)
    fun setPaused(paused:Boolean) {
        if(proposal!=null)return
        if(paused)clock.pause() else clock.resume()
        state=state.copy(serial=state.serial+1,paused=paused);screen.pause(paused);update()
        scope.launch {persist();emit()}
    }
    fun category(value:String) {
        if(proposal!=null || manifest?.moduleItems(value).isNullOrEmpty())return
        state=state.copy(serial=state.serial+1,category=value,itemIndex=0,remainingMs=((manifest!!.moduleItems(value).first().durationSeconds ?: 20.0)*1000).toLong(),videoPositionMs=0)
        retryItem=0;showCurrent();scope.launch {persist()}
    }
    fun forceSync(){candidateFailures=0;retryAt=0;scope.launch {poll()}}
    private suspend fun persist() {
        if(proposal!=null)return
        val item=currentItem()
        val value=state.copy(serial=state.serial+1,remainingMs=if(item?.type!="video")clock.left() else state.remainingMs,videoPositionMs=screen.videoPosition())
        state=value
        try{withContext(Dispatchers.IO){repo.save(value)}}catch(e:CancellationException){throw e}catch(e:Exception){lastError="播放位置未能保存，请检查磁盘空间";update()}
    }
    private suspend fun emit() {
        if(!identityVerified)return
        try {
            val actual=state.activeVersion!=null && confirmed && proposal==null
            val playing=when { actual -> if(state.paused)"paused" else "playing";desired!=null->"downloading";else->"idle" }
            val snapshot=state;val item=currentItem();val pending=desired?.playlistVersionId?.takeIf { desired?.assignmentRevision!=state.activeRevision }
            val error=lastError ?: networkError;val progress=transfer
            withContext(Dispatchers.IO){repo.enqueue(config,snapshot,actual,item,playing,pending,error,progress)}
        } catch(e:CancellationException){throw e} catch(e:Exception){lastError="播放报告未能持久保存，请检查磁盘空间";update()}
    }
    private suspend fun cleanup() {
      try {
        val protected=mutableSetOf<String>()
        listOf(state.activeVersion,state.previousVersion,desired?.playlistVersionId).filterNotNull().forEach { protected.add(it) }
        withContext(Dispatchers.IO) {
            var after=0L
            while(true){val page=repo.dao.pendingPage(after);if(page.isEmpty())break
                page.forEach { event -> val report=Wire.gson.fromJson(event.payload,Report::class.java)
                    report.actualVersionId?.let {protected.add(it)};report.pendingVersionId?.let {protected.add(it)}
                };after=page.last().seq
            }
            val keep=mutableSetOf<String>()
            protected.forEach { id -> repo.dao.snapshot(id)?.let { snapshot -> parse(snapshot).items.forEach { keep.add(cache.path(it.file).name) } } }
            cache.directory.listFiles()?.forEach { file ->
                val name=file.name.removeSuffix(".partial")
                if((file.name.endsWith(".bin") || file.name.endsWith(".bin.partial")) && name !in keep && file.canonicalFile.parentFile==cache.directory.canonicalFile)file.delete()
            }
        }
      } catch(e:CancellationException){throw e} catch(e:Exception){lastError="旧缓存暂未清理，请检查存储空间";update()}
    }
    private suspend fun flush() {
        for(event in repo.dao.pending()) {
            if(event.error!=null)continue
            try {val outcome=client.report(event.payload);check(outcome in setOf("accepted","duplicate","stale"));repo.dao.remove(event.eventId);lastReport=android.os.SystemClock.elapsedRealtime();lastContactAt=Wire.utc();reportFailed=false}
            catch(e:CancellationException){throw e}
            catch(e:RelayFailure){reportFailed=true;if(e.status in listOf(400,404,409,422)){repo.dao.block(event.eventId,"回报被拒绝（${e.status}），请管理员检查");withContext(Dispatchers.Main){lastError="有播放报告被中转拒绝，请管理员检查";update()}}else return}
            catch(e:Exception){reportFailed=true;return}
        }
    }
    private fun update() {
        val pending=desired?.assignmentRevision!=null && desired?.assignmentRevision!=state.activeRevision
        val elapsed=android.os.SystemClock.elapsedRealtime()
        val communication=CommunicationHealth.state(online,networkError!=null,reportFailed,lastSync,lastReport,elapsed,offlineSeconds)
        val status=when { !online || communication=="offline"->"离线 / 最新安排待核对";!verified->if(pending)"新资料准备中 · 保留本地播放" else "安排待核对";else->"中转已连接 · 安排已核对" }
        screen.update(manifest,state.category,state.itemIndex,state.paused,status,lastError ?: networkError,transfer,PlayerInfo(config.deviceId,deviceName,desired?.assignedAt,communication,lastContactAt))
    }
    private fun safeError(e:Exception)=when(e){is RelayFailure->e.message ?: "中转访问失败";is IllegalArgumentException,is IllegalStateException->e.message ?: "文件或协议校验失败";else->"中转连接中断，继续本地播放"}
    fun stop() {
        if(!alive)return
        alive=false;renderGeneration++;clock.pause()
        val saved=state.copy(serial=state.serial+1,remainingMs=clock.left(),videoPositionMs=screen.videoPosition())
        val item=currentItem();val actual=confirmed && state.activeVersion!=null
        scope.cancel();client.close()
        app.lastSave=app.scope.async {
            repo.save(saved)
            if(identityVerified)runCatching {repo.enqueue(config,saved,actual,item,if(actual)"paused" else "idle",desired?.playlistVersionId?.takeIf {it!=saved.activeVersion},"播放页面已离开",null)}
        }
    }
}
