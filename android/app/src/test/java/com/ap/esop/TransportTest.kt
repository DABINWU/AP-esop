package com.ap.esop

import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class TransportTest {
    @get:Rule val folder=TemporaryFolder()
    private val bytes="0123456789ABCDEFGHIJ".toByteArray()
    private fun file()=MediaFile("file_test","/api/v1/files/file_test","video/mp4",bytes.size.toLong(),Wire.sha(bytes),"video",null)
    @Test fun syncWithoutAssignmentAcceptsExplicitNullManifest()=runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("""{"server_time":"2026-11-07T00:00:00Z","heartbeat_seconds":10,"assignment":null,"manifest":null,"manifest_sha256":null,"ready":false,"error":null,"report_seq_floor":0}"""))
            val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key")
            val reply=client.sync("screen-new",0)
            assertNull(reply.assignment);assertNull(reply.manifest);assertFalse(reply.ready);client.close()
        }
    }
    @Test fun syncPendingAssignmentAcceptsNullManifestThenReadyObject()=runBlocking {
        MockWebServer().use { server ->
            val assignment="""{"device_id":"screen-new","assignment_revision":1,"playlist_version_id":"ver_1","task_id":"task_1","assigned_at":"2026-11-07T00:00:00Z","relay_acked_at":null}"""
            server.enqueue(MockResponse().setBody("""{"server_time":"2026-11-07T00:00:00Z","heartbeat_seconds":10,"assignment":$assignment,"manifest":null,"ready":false,"error":"preparing","report_seq_floor":0}"""))
            server.enqueue(MockResponse().setBody("""{"server_time":"2026-11-07T00:00:00Z","heartbeat_seconds":10,"assignment":$assignment,"manifest":{"schema_version":2},"ready":true,"report_seq_floor":0}"""))
            val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key")
            val pending=client.sync("screen-new",0);assertNull(pending.manifest);assertEquals(1L,pending.assignment!!.assignmentRevision)
            val ready=client.sync("screen-new",1);assertTrue(ready.ready);assertEquals(2,ready.manifest!!.get("schema_version").asInt);client.close()
        }
    }
    @Test fun resume206ValidatesRangeAndHash()=runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(206).addHeader("Content-Range","bytes 5-19/20").setBody(String(bytes.drop(5).toByteArray())))
            val cache=MediaCache(folder.newFolder(),0);cache.partial(file()).writeBytes(bytes.take(5).toByteArray())
            val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key")
            cache.fetch(client,file()){};assertArrayEquals(bytes,cache.path(file()).readBytes())
            val request=server.takeRequest();assertEquals("bytes=5-",request.getHeader("Range"));assertEquals("\"${file().sha256}\"",request.getHeader("If-Range"));client.close()
        }
    }
    @Test fun rangeIgnored200SafelyOverwritesPartial()=runBlocking {
        MockWebServer().use { server ->server.enqueue(MockResponse().setBody(String(bytes)))
            val cache=MediaCache(folder.newFolder(),0);cache.partial(file()).writeBytes(bytes.take(8).toByteArray())
            val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key");cache.fetch(client,file()){}
            assertTrue(cache.valid(file()));assertFalse(cache.partial(file()).exists());client.close()
        }
    }
    @Test fun completePartialSurvivesRestartWithoutDownloading()=runBlocking {
        MockWebServer().use { server ->val cache=MediaCache(folder.newFolder(),0);cache.partial(file()).writeBytes(bytes)
            val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key");cache.fetch(client,file()){}
            assertTrue(cache.valid(file()));assertEquals(0,server.requestCount);client.close()
        }
    }
    @Test fun wrongChecksumNeverBecomesPlayable()=runBlocking {
        MockWebServer().use { server ->repeat(3){server.enqueue(MockResponse().setBody("XXXXXXXXXXXXXXXXXXXX"))}
            val cache=MediaCache(folder.newFolder(),0);val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key")
            try{cache.fetch(client,file()){};fail("must reject checksum")}catch(e:IllegalStateException){assertFalse(cache.valid(file()))};client.close()
        }
    }
    @Test fun smallJsonResponseDoesNotRequireFullBuffer()=runBlocking {
        MockWebServer().use { server ->server.enqueue(MockResponse().setBody("{\"allowed\":true}"))
            val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key");assertTrue(client.json("/api/v1/device/activation-check",emptyMap<String,String>()).get("allowed").asBoolean);client.close()
        }
    }
    @Test fun diskReserveProtectsExistingFiles()=runBlocking {
        val cache=MediaCache(folder.newFolder(),Long.MAX_VALUE/2)
        MockWebServer().use { server ->val client=RelayClient(server.url("").toString().trimEnd('/'),"test-key")
            try{cache.fetch(client,file()){};fail("must refuse insufficient space")}catch(e:IllegalStateException){assertFalse(cache.path(file()).exists())};client.close()
        }
    }
}
