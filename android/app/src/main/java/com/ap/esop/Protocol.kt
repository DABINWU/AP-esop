package com.ap.esop

import com.google.gson.*
import java.math.BigDecimal
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.*

object Wire {
    val gson: Gson = GsonBuilder().setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES).disableHtmlEscaping().create()
    private val idPattern = Regex("[A-Za-z0-9_-]{1,128}")
    fun id(value: String): String { require(idPattern.matches(value)) { "文件或版本编号无效" }; return value }
    fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    fun utc(): String = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(Date())
    private fun quoted(text: String): String = buildString {
        append('"');text.forEach { c -> when(c) {
            '"' -> append("\\\""); '\\' -> append("\\\\"); '\n' -> append("\\n"); '\r' -> append("\\r"); '\t' -> append("\\t")
            '\b' -> append("\\b"); '\u000c' -> append("\\f")
            else -> if(c.code < 32) append("\\u%04x".format(c.code)) else append(c)
        } };append('"')
    }
    fun canonical(e: JsonElement): String = when {
        e.isJsonNull -> "null"
        e.isJsonObject -> e.asJsonObject.entrySet().sortedBy { it.key }.joinToString(",", "{", "}") { quoted(it.key)+":"+canonical(it.value) }
        e.isJsonArray -> e.asJsonArray.joinToString(",", "[", "]") { canonical(it) }
        e.asJsonPrimitive.isString -> quoted(e.asString)
        else -> e.toString()
    }
    fun manifest(raw: JsonObject, expectedHash: String, versionId: String): Manifest {
        require(Regex("[0-9a-f]{64}").matches(expectedHash) && sha(canonical(raw).toByteArray(Charsets.UTF_8)) == expectedHash) { "播放清单校验失败" }
        require(raw.get("schema_version").asInt in 1..2 && raw.get("loop").asBoolean) { "不支持的播放清单" }
        require(raw.get("playlist_version_id").asString == id(versionId)) { "版本编号不一致" }
        val items = raw.getAsJsonArray("items");require(items.size() in 1..2000) { "播放清单为空或过长" }
        val seen = mutableSetOf<String>();val files = mutableMapOf<String,String>()
        items.forEachIndexed { index, value ->
            val item = value.asJsonObject;require(item.get("position").asBigDecimal == BigDecimal(index)) { "播放顺序错误" }
            require(seen.add(id(item.get("id").asString))) { "播放项重复" }
            val kind = item.get("type").asString;require(kind in setOf("page", "image", "video"))
            val file = item.getAsJsonObject("file");val fileId = id(file.get("id").asString)
            require(file.get("download_path").asString == "/api/v1/files/$fileId") { "禁止访问中转之外的文件地址" }
            require(file.get("size_bytes").asBigDecimal.longValueExact() in 1..(2L*1024*1024*1024)) { "文件大小异常" }
            require(Regex("[0-9a-f]{64}").matches(file.get("sha256").asString))
            require(file.get("role").asString == kind)
            val media = file.get("media_type").asString
            require(if(kind == "video") media == "video/mp4" else media in setOf("image/png", "image/jpeg"))
            val identity=canonical(file);require(files.put(fileId,identity).let { it == null || it == identity }) { "同一文件编号内容冲突" }
            if(kind == "video") require(item.get("duration_seconds").isJsonNull && item.get("playback").asString == "once")
            else require(item.get("duration_seconds").asDouble in 1.0..3600.0 && item.get("playback").asString == "timed")
        }
        if(raw.get("schema_version").asInt == 2) {
            val display=raw.getAsJsonObject("display")
            require(display.get("width").asInt==1920 && display.get("height").asInt==1080 && display.get("fit").asString=="contain" && display.get("aspect_ratio").asString=="16:9")
            val modules=raw.getAsJsonArray("modules")
            require(modules.map { it.asJsonObject.get("id").asString } == listOf("production","inspection","check_video","custom"))
            modules.forEach { m -> val module=m.asJsonObject;require(module.get("loop").asBoolean)
                val expected=JsonArray();items.forEach { i -> if(i.asJsonObject.get("category")?.asString.orEmpty().ifBlank { "custom" } == module.get("id").asString) expected.add(i) }
                require(canonical(expected)==canonical(module.get("items"))) { "模块与播放清单不一致" }
            }
        }
        return gson.fromJson(raw,Manifest::class.java)
    }
}

data class MediaFile(val id:String,val downloadPath:String,val mediaType:String,val sizeBytes:Long,val sha256:String,val role:String,val pageNumber:Int?)
data class PlayItem(val id:String,val position:Int,val type:String,val durationSeconds:Double?,val playback:String,val file:MediaFile,val category:String?)
data class PlayModule(val id:String,val label:String,val items:List<PlayItem>,val loop:Boolean)
data class Manifest(val schemaVersion:Int,val playlistVersionId:String,val name:String,val version:Int,val publishedAt:String,val loop:Boolean,val items:List<PlayItem>,val modules:List<PlayModule>?) {
    fun moduleItems(category:String)=if(schemaVersion==1) { if(category=="custom") items else emptyList() } else items.filter { (it.category ?: "custom")==category }
    fun firstCategory()=listOf("production","inspection","check_video","custom").first { moduleItems(it).isNotEmpty() }
}
data class Assignment(val deviceId:String,val assignmentRevision:Long,val playlistVersionId:String,val taskId:String,val assignedAt:String,val relayAckedAt:String?)
data class SyncReply(val serverTime:String,val heartbeatSeconds:Int,val assignment:Assignment?,val manifest:JsonObject?,val ready:Boolean,val error:String?,val manifestSha256:String?,val reportSeqFloor:Long,val deviceName:String?=null,val offlineSeconds:Int?=null)
data class Progress(val playlistVersionId:String,val receivedBytes:Long,val totalBytes:Long,val completedFiles:Int,val totalFiles:Int)
data class Report(val eventId:String,val deviceId:String,val reportSeq:Long,val observedAt:String,val assignmentRevision:Long,val actualVersionId:String?,val currentItemId:String?,val playbackState:String,val pendingVersionId:String?,val error:String?,val downloadProgress:Progress?)
