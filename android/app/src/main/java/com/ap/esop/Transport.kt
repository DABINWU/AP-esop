package com.ap.esop

import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

class RelayFailure(val status:Int,message:String):Exception(message)
class RelayClient(val url:String,private val accessKey:String,private val http:OkHttpClient=OkHttpClient.Builder()
    .connectTimeout(5,TimeUnit.SECONDS).readTimeout(20,TimeUnit.SECONDS).callTimeout(180,TimeUnit.SECONDS)
    .followRedirects(false).followSslRedirects(false).build()) {
    private fun request(path:String):Request.Builder {
        require(path.startsWith("/api/v1/") && !path.contains("..") && !path.contains('?') && !path.contains('#'))
        return Request.Builder().url(url+path).header("Authorization","Bearer $accessKey").header("Accept-Encoding","identity")
    }
    suspend fun json(path:String,body:Any):JsonObject=withContext(Dispatchers.IO) {
        val call=http.newCall(request(path).post(Wire.gson.toJson(body).toRequestBody("application/json".toMediaType())).build())
        val cancel=currentCoroutineContext().job.invokeOnCompletion { if(it is CancellationException)call.cancel() }
        try { call.execute().use { response ->
            if(!response.isSuccessful)throw RelayFailure(response.code,when(response.code) {401->"终端密钥无效";404->"屏幕未登记或接口不存在";409->"安排已更新，请重新同步";503->"中转缓存或目标暂未准备好";else->"中转请求失败（${response.code}）"})
            val input=response.body?.byteStream() ?: error("中转响应为空")
            val output=java.io.ByteArrayOutputStream();val buffer=ByteArray(65536)
            while(true){currentCoroutineContext().ensureActive();val n=input.read(buffer);if(n<0)break
                require(output.size()+n<=16*1024*1024) { "中转响应超过上限" };output.write(buffer,0,n)}
            val bytes=output.toByteArray()
            JsonParser.parseString(String(bytes,Charsets.UTF_8)).asJsonObject
        } } finally { cancel.dispose() }
    }
    suspend fun sync(deviceId:String,known:Long):SyncReply=Wire.gson.fromJson(json("/api/v1/device/sync",mapOf("device_id" to deviceId,"known_assignment_revision" to known)),SyncReply::class.java)
    suspend fun activation(a:Assignment):Boolean {
        val result=json("/api/v1/device/activation-check",mapOf("device_id" to a.deviceId,"assignment_revision" to a.assignmentRevision,"playlist_version_id" to a.playlistVersionId))
        val returned=Wire.gson.fromJson(result.get("assignment"),Assignment::class.java)
        return result.get("allowed")?.asBoolean==true && returned.assignmentRevision==a.assignmentRevision && returned.playlistVersionId==a.playlistVersionId
    }
    suspend fun report(payload:String):String {
        val body=JsonObject();body.add("report",JsonParser.parseString(payload))
        return json("/api/v1/device/reports",body).get("status").asString
    }
    fun fileRequest(file:MediaFile,offset:Long):Request {
        require(file.downloadPath=="/api/v1/files/"+Wire.id(file.id))
        return request(file.downloadPath).apply { if(offset>0){header("Range","bytes=$offset-");header("If-Range","\"${file.sha256}\"")} }.build()
    }
    fun call(request:Request)=http.newCall(request)
    fun close(){http.dispatcher.cancelAll();http.connectionPool.evictAll()}
}

class MediaCache(val directory:File,private val reserveBytes:Long=64L*1024*1024) {
    init { directory.mkdirs() }
    fun path(file:MediaFile)=File(directory,"${Wire.id(file.id)}.${file.sha256}.bin").also {
        require(Regex("[0-9a-f]{64}").matches(file.sha256) && it.canonicalFile.parentFile==directory.canonicalFile)
    }
    fun partial(file:MediaFile)=File(directory,path(file).name+".partial")
    fun valid(file:MediaFile):Boolean {
        val target=path(file);return target.isFile && target.length()==file.sizeBytes && digest(target)==file.sha256
    }
    fun digest(file:File):String {
        val hash=MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { stream -> val buffer=ByteArray(128*1024);while(true){val n=stream.read(buffer);if(n<0)break;hash.update(buffer,0,n)} }
        return hash.digest().joinToString(""){ "%02x".format(it) }
    }
    suspend fun fetch(client:RelayClient,file:MediaFile,progress:suspend(Long)->Unit) = withContext(Dispatchers.IO) {
        if(valid(file)){progress(file.sizeBytes);return@withContext}
        val temp=partial(file)
        var last:Exception?=null
        repeat(3) { attempt ->
            currentCoroutineContext().ensureActive()
            try {
                if(temp.exists() && temp.length()>file.sizeBytes)temp.delete()
                if(temp.exists() && temp.length()==file.sizeBytes) {
                    if(digest(temp)==file.sha256){promote(temp,file);progress(file.sizeBytes);return@withContext} else temp.delete()
                }
                val offset=if(temp.exists())temp.length() else 0L
                check(directory.usableSpace>=file.sizeBytes-offset+reserveBytes) { "终端磁盘空间不足，保留旧内容" }
                val call=client.call(client.fileRequest(file,offset))
                val cancellation=currentCoroutineContext().job.invokeOnCompletion { if(it is CancellationException)call.cancel() }
                try { call.execute().use { response ->
                    if(response.code==416){temp.delete();throw RelayFailure(416,"断点已失效，重新下载")}
                    if(response.code !in listOf(200,206))throw RelayFailure(response.code,"文件下载失败（${response.code}）")
                    val append=response.code==206 && offset>0
                    if(response.code==206)require(response.header("Content-Range")=="bytes $offset-${file.sizeBytes-1}/${file.sizeBytes}") { "文件断点范围不匹配" }
                    require(response.header("Content-Encoding", "identity")=="identity")
                    var received=if(append)offset else 0L
                    val body=response.body ?: error("文件内容为空")
                    FileOutputStream(temp,append).use { out -> body.byteStream().use { input ->
                        val buffer=ByteArray(128*1024)
                        while(true){currentCoroutineContext().ensureActive();val n=input.read(buffer);if(n<0)break
                            received+=n;require(received<=file.sizeBytes){"文件超过声明大小"}
                            check(directory.usableSpace>=n+reserveBytes){"终端磁盘空间不足"}
                            out.write(buffer,0,n);progress(received)
                        }
                        out.flush();out.fd.sync()
                    } }
                    if(temp.length()!=file.sizeBytes || digest(temp)!=file.sha256){temp.delete();error("文件大小或SHA-256校验失败")}
                    promote(temp,file);progress(file.sizeBytes);return@withContext
                } } finally { cancellation.dispose() }
            } catch(e:CancellationException){throw e} catch(e:Exception){last=e;if(attempt<2)delay(1000L*(attempt+1))}
        }
        throw last ?: IllegalStateException("文件下载失败")
    }
    private fun promote(temp:File,file:MediaFile) {
        if(path(file).exists() && !valid(file))check(path(file).delete()) { "损坏缓存无法替换" }
        check(temp.renameTo(path(file))) { "文件原子提交失败" }
    }
}
