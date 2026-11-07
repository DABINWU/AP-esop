package com.ap.esop

import android.content.Context
import androidx.room.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

@Entity(tableName="player_state")
data class PlayerState(@PrimaryKey val id:Int=1,val serial:Long=0,val knownRevision:Long=0,val desiredJson:String?=null,
    val activeVersion:String?=null,val activeRevision:Long=0,val previousVersion:String?=null,val previousRevision:Long=0,
    val category:String="production",val itemIndex:Int=0,val paused:Boolean=false,val remainingMs:Long=20000,val videoPositionMs:Long=0)
@Entity(tableName="snapshots")
data class Snapshot(@PrimaryKey val versionId:String,val manifest:String,val sha256:String)
@Entity(tableName="counters") data class Counter(@PrimaryKey val name:String,val value:Long)
@Entity(tableName="outbox",indices=[Index(value=["seq"],unique=true)]) data class PendingReport(@PrimaryKey val eventId:String,val seq:Long,val payload:String,val error:String?=null)
@Dao interface PlayerDao {
    @Query("SELECT * FROM player_state WHERE id=1") suspend fun state():PlayerState?
    @Insert(onConflict=OnConflictStrategy.REPLACE) suspend fun state(value:PlayerState)
    @Query("SELECT * FROM snapshots WHERE versionId=:id") suspend fun snapshot(id:String):Snapshot?
    @Query("SELECT * FROM snapshots") suspend fun snapshots():List<Snapshot>
    @Insert(onConflict=OnConflictStrategy.IGNORE) suspend fun snapshot(value:Snapshot)
    @Query("SELECT value FROM counters WHERE name=:name") suspend fun counter(name:String):Long?
    @Insert(onConflict=OnConflictStrategy.REPLACE) suspend fun counter(value:Counter)
    @Insert suspend fun report(value:PendingReport)
    @Query("SELECT * FROM outbox ORDER BY seq LIMIT 100") suspend fun pending():List<PendingReport>
    @Query("SELECT * FROM outbox") suspend fun allPending():List<PendingReport>
    @Query("SELECT * FROM outbox WHERE seq>:after ORDER BY seq LIMIT 500") suspend fun pendingPage(after:Long):List<PendingReport>
    @Query("DELETE FROM outbox WHERE eventId=:id") suspend fun remove(id:String)
    @Query("UPDATE outbox SET error=:error WHERE eventId=:id") suspend fun block(id:String,error:String)
    @Query("SELECT count(*) FROM outbox") suspend fun pendingCount():Int
}
@Database(entities=[PlayerState::class,Snapshot::class,Counter::class,PendingReport::class],version=1,exportSchema=true)
abstract class PlayerDatabase:RoomDatabase() {
    abstract fun dao():PlayerDao
    companion object { fun open(context:Context)=Room.databaseBuilder(context,PlayerDatabase::class.java,"esop.db").setJournalMode(JournalMode.WRITE_AHEAD_LOGGING)
        .addCallback(object:RoomDatabase.Callback(){override fun onOpen(db:androidx.sqlite.db.SupportSQLiteDatabase){db.execSQL("PRAGMA synchronous=FULL");db.query("PRAGMA busy_timeout=15000").use { it.moveToFirst() }}}).build() }
}

class Repository(val db:PlayerDatabase) {
    val dao=db.dao();private val mutex=Mutex()
    suspend fun load()=dao.state() ?: PlayerState()
    suspend fun save(value:PlayerState)=mutex.withLock { db.withTransaction { val old=dao.state();if(old==null || old.serial<=value.serial)dao.state(value) } }
    suspend fun snapshot(value:Snapshot)=mutex.withLock { db.withTransaction {
        val old=dao.snapshot(value.versionId)
        require(old==null || old.sha256==value.sha256) { "不可变清单发生变化" }
        dao.snapshot(value)
    } }
    suspend fun enqueue(config:DeviceConfig,state:PlayerState,actual:Boolean,item:PlayItem?,playback:String,pending:String?,error:String?,progress:Progress?)=mutex.withLock {
        db.withTransaction {
            check(dao.pendingCount()<100000) { "待上报队列已满，请恢复中转连接" }
            val seq=(dao.counter("report_seq") ?: 0)+1
            val event=config.installId+"-"+seq
            val report=Report(event,config.deviceId,seq,Wire.utc(),if(actual) state.activeRevision else state.knownRevision,
                if(actual) state.activeVersion else null,if(actual) item?.id else null,playback,pending,error?.take(1800),progress)
            dao.report(PendingReport(event,seq,Wire.gson.toJson(report)));dao.counter(Counter("report_seq",seq))
        }
    }
}
