package com.ap.esop

import android.content.Context
import android.os.SystemClock
import android.view.MotionEvent
import android.widget.TextView
import androidx.room.Room
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.util.UUID
import java.util.concurrent.atomic.AtomicReference

class PlayerIntegrationTest {
    private val instrumentation=InstrumentationRegistry.getInstrumentation()
    @Test fun roomTransactionsPersistReportsAndSequence()=runBlocking {
        val context=instrumentation.targetContext
        val name="test-${UUID.randomUUID()}.db"
        val db=Room.databaseBuilder(context,PlayerDatabase::class.java,name).build()
        val repo=Repository(db)
        val config=DeviceConfig("http://127.0.0.1","unit-device","test-only","test-install","salt","hash","none")
        repeat(2){repo.enqueue(config,PlayerState(),false,null,"idle",null,null,null)}
        assertEquals(2L,repo.dao.counter("report_seq"));db.close()
        val reopened=Room.databaseBuilder(context,PlayerDatabase::class.java,name).build()
        assertEquals(2,reopened.dao().allPending().size);assertEquals(2L,reopened.dao().counter("report_seq"));reopened.close();context.deleteDatabase(name);Unit
    }
    @Test fun realRelayPlaybackControlsAndVideoFirstFrame() {
        val args=InstrumentationRegistry.getArguments()
        val url=args.getString("relay_url");assumeTrue("Host integration relay not supplied",url!=null)
        val app=instrumentation.targetContext.applicationContext as PlayerApplication
        val config=app.settings.create(url!!,args.getString("device_id")!!,args.getString("device_key")!!,"12345678",null,false)
        app.settings.save(config)
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            fun value(tag:String):String {val result=AtomicReference("");scenario.onActivity {result.set(it.window.decorView.findViewWithTag<TextView>(tag)?.text?.toString().orEmpty())};return result.get()}
            fun click(tag:String){scenario.onActivity {check(it.window.decorView.findViewWithTag<android.view.View>(tag)?.performClick()==true)}}
            fun waitFor(timeout:Long=60000,check:()->Boolean){val until=SystemClock.elapsedRealtime()+timeout;while(SystemClock.elapsedRealtime()<until){if(check())return;Thread.sleep(150)};assertTrue("Timed out: ${value("status")}",check())}
            waitFor {value("title").contains("安卓联调") && value("status").contains("安排已核对")}
            waitFor {value("communication")=="中转通信正常"}
            assertTrue(value("device-info").contains("1号装配屏"))
            assertTrue(value("date-info").contains("目标下发：") && !value("date-info").contains("—"))
            assertTrue(value("file-info").contains("视频"))
            scenario.onActivity { activity ->
                for(tag in listOf("file-info","item-label","status"))assertEquals(android.view.View.GONE,activity.window.decorView.findViewWithTag<android.view.View>(tag).visibility)
            }
            scenario.onActivity { activity ->
                val root=activity.window.decorView.findViewWithTag<android.view.View>("player-root")
                val media=activity.window.decorView.findViewWithTag<android.view.View>("media-frame")
                val rail=activity.window.decorView.findViewWithTag<android.view.View>("control-scroll")
                assertEquals(0,media.top);assertEquals(root.height,media.height)
                assertEquals(media.right,rail.left);assertTrue(media.width>rail.width)
                val railBounds=android.graphics.Rect();rail.getGlobalVisibleRect(railBounds)
                for(tag in listOf("title","status","item-label","previous","next","pause","replay","zoom-exit","module-production","module-inspection","module-check_video","module-custom")) {
                    val view=activity.window.decorView.findViewWithTag<android.view.View>(tag)
                    if(view.visibility==android.view.View.GONE)continue
                    val position=IntArray(2);view.getLocationOnScreen(position)
                    assertTrue("$tag outside right control rail",position[0]>=railBounds.left && position[0]+view.width<=railBounds.right)
                }
            }
            if(value("pause")=="暂停")click("pause")
            assertEquals("继续",value("pause"));val held=value("item-label");Thread.sleep(4500);assertEquals(held,value("item-label"))
            click("next");waitFor {value("item-label")!=held};assertEquals("继续",value("pause"))
            click("previous");waitFor {value("item-label")==held}
            click("replay");assertEquals("继续",value("pause"))
            click("pause");waitFor(10000){value("item-label")!=held};assertEquals("暂停",value("pause"))
            click("module-check_video");waitFor {value("item-label").contains("点检视频")}
            waitFor(30000){var position=0L;scenario.onActivity{position=it.videoPosition()};position>300}
            click("pause");var before=0L;scenario.onActivity{before=it.videoPosition()};Thread.sleep(600);var after=0L;scenario.onActivity{after=it.videoPosition()};assertTrue(kotlin.math.abs(after-before)<150)
            click("replay");Thread.sleep(500);scenario.onActivity{assertTrue(it.videoPosition()<150)};assertEquals("继续",value("pause"))
            click("pause");waitFor(10000){var position=0L;scenario.onActivity{position=it.videoPosition()};position>250}
            click("module-inspection");waitFor {value("item-label").contains("SIP")}
            scenario.onActivity { activity ->
                val image=activity.window.decorView.findViewWithTag<TouchImageView>("image")
                val start=SystemClock.uptimeMillis()
                val properties=Array(2){MotionEvent.PointerProperties().apply {id=it;toolType=MotionEvent.TOOL_TYPE_FINGER}}
                fun event(action:Int,count:Int,span:Float,time:Long):MotionEvent {
                    val points=Array(count){i->MotionEvent.PointerCoords().apply {x=image.width/2f+(if(i==0)-span else span);y=image.height/2f;pressure=1f;size=1f}}
                    return MotionEvent.obtain(start,time,action,count,properties,points,0,0,1f,1f,0,0,0,0)
                }
                listOf(event(MotionEvent.ACTION_DOWN,1,60f,start),event(MotionEvent.ACTION_POINTER_DOWN or (1 shl MotionEvent.ACTION_POINTER_INDEX_SHIFT),2,60f,start+10),event(MotionEvent.ACTION_MOVE,2,100f,start+30),event(MotionEvent.ACTION_MOVE,2,140f,start+60),event(MotionEvent.ACTION_POINTER_UP or (1 shl MotionEvent.ACTION_POINTER_INDEX_SHIFT),2,140f,start+80),event(MotionEvent.ACTION_UP,1,140f,start+90)).forEach {image.onTouchEvent(it);it.recycle()}
            }
            waitFor {value("pause")=="继续"};click("zoom-exit");assertEquals("继续",value("pause"))
            click("module-production");click("pause")
            waitFor(30000){value("item-label").contains("7 / 7")}
            waitFor(10000){var position=0L;scenario.onActivity{position=it.videoPosition()};position>250}
            waitFor(10000){value("item-label").contains("1 / 7")}
            click("pause");assertEquals("继续",value("pause"))
            File(app.filesDir,"validation.phase").writeText("paused_heartbeat")
            Thread.sleep(12000);assertEquals("继续",value("pause"))
            File(app.filesDir,"validation.phase").writeText("controls_passed")
        }
    }
}

class UnassignedConnectionTest {
    @Test fun registeredScreenCanConnectBeforeAnyDeployment()=runBlocking {
        val args=InstrumentationRegistry.getArguments();assumeTrue(args.getString("relay_url")!=null)
        val client=RelayClient(args.getString("relay_url")!!,args.getString("device_key")!!)
        try {
            var reply:SyncReply?=null
            repeat(30){if(reply==null)try {reply=client.sync(args.getString("device_id")!!,0)}catch(e:RelayFailure){if(e.status!=404)throw e;kotlinx.coroutines.delay(500)}}
            assertNotNull(reply);assertNull(reply!!.assignment);assertNull(reply!!.manifest);assertFalse(reply!!.ready)
        } finally {client.close()}
        Unit
    }
}

class SecondScreenSetupTest {
    @Test fun configureAndStartSecondEmulatedScreen() {
        val args=InstrumentationRegistry.getArguments();assumeTrue(args.getString("device_id")!=null)
        val app=InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as PlayerApplication
        app.settings.save(app.settings.create(args.getString("relay_url")!!,args.getString("device_id")!!,args.getString("device_key")!!,"12345678",null,false))
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            val until=SystemClock.elapsedRealtime()+60000;var ready=false
            while(SystemClock.elapsedRealtime()<until){scenario.onActivity{ready=it.window.decorView.findViewWithTag<TextView>("title")?.text?.contains("安卓联调")==true};if(ready)break;Thread.sleep(200)}
            assertTrue("Second emulator did not display prepared content",ready)
        }
    }
}
