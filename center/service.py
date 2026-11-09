import hashlib
import json
from datetime import datetime, timezone

from .db import uid, now, after, dumps, expire_tasks
from .media import public_file, MediaError
from shared.reports import report_data


def require_active_playlist(c, playlist_id):
    row = require_row(c, 'playlists', 'id', playlist_id)
    if row['archived_at']:
        raise MediaError('任务单已删除归档，不能继续编辑、发布或下发', 409)
    return row


def require_active_version(c, version_id):
    row = require_row(c, 'versions', 'id', version_id)
    require_active_playlist(c, row['playlist_id'])
    if c.execute('SELECT 1 FROM version_archives WHERE version_id=?', (version_id,)).fetchone():
        raise MediaError('该版本已删除归档，请刷新并选择其他版本', 409, 'version_archived')
    return row


def require_row(c, table, key, value):
    # table/key are only hardcoded by server callers, never input.
    row = c.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
    if row is None:
        raise MediaError("记录不存在", 404)
    return row


def asset_view(c, row):
    result = dict(row)
    for field in ("warnings", "metadata"):
        result[field] = json.loads(result[field])
    result["files"] = [public_file(f) for f in c.execute(
        "SELECT * FROM files WHERE asset_id=? ORDER BY role,page_number,id", (row["id"],))]
    job = c.execute("SELECT * FROM conversion_jobs WHERE asset_id=?", (row["id"],)).fetchone()
    result["conversion_job"] = dict(job) if job else None
    return result


def version_view(row):
    result = dict(row)
    result.setdefault('archived_at', None)
    result["items"] = json.loads(result["items"])
    result["manifest"] = json.loads(result["manifest"]) if result["manifest"] else None
    return result


def task_view(c, row):
    result = {k: row[k] for k in row.keys() if k != "fingerprint"}
    result["targets"] = [dict(t) for t in c.execute(
        "SELECT device_id,assignment_revision,status,error FROM task_targets WHERE task_id=? ORDER BY device_id", (row["id"],))]
    return result


def expand_items(c, items, playlist_id=None):
    expanded = []
    for item in items:
        if item.asset_id:
            asset = c.execute('SELECT * FROM assets WHERE id=?', (item.asset_id,)).fetchone()
            if asset is None:
                raise MediaError('所选媒体资料已删除或不存在，请刷新资料列表后重新选择', 404, 'asset_not_found')
            if asset["status"] != "ready":
                raise MediaError("资料未就绪或正在删除，不能编排或发布", 409)
            roles = {"excel": "page", "pdf": "page", "image": "image", "video": "video"}
            files = c.execute("SELECT * FROM files WHERE asset_id=? AND role=? ORDER BY page_number,id",
                              (asset["id"], roles[asset["kind"]])).fetchall()
        else:
            f = c.execute('SELECT * FROM files WHERE id=?', (item.file_id,)).fetchone()
            if f is None:
                raise MediaError('所选媒体文件已删除或不存在，请刷新资料列表后重新选择', 404, 'asset_not_found')
            asset = require_row(c, "assets", "id", f["asset_id"])
            if asset["status"] != "ready" or f["role"] not in ("page", "image", "video"):
                raise MediaError("该文件不是可发布媒体", 409)
            files = [f]
        if not files:
            raise MediaError("资产没有可播放文件", 409)
        metadata = json.loads(asset['metadata'])
        if playlist_id and metadata.get('playlist_id') and metadata['playlist_id'] != playlist_id:
            raise MediaError('文件属于其他任务单，不能加入当前任务单', 409)
        category = item.category or metadata.get('category', 'custom')
        if metadata.get('category') and category != metadata['category']:
            raise MediaError('文件分类与上传模块不一致，请在对应模块上传', 422)
        if category == 'check_video' and asset['kind'] != 'video':
            raise MediaError('点检视频模块仅支持MP4视频', 422)
        for f in files:
            video = f["role"] == "video"
            if video and item.duration_seconds is not None:
                raise MediaError("视频必须完整播放一次，不设置图片停留时长")
            expanded.append(dict(id=uid("item"), position=len(expanded), type=f["role"], category=category, asset_id=asset['id'],
                                 duration_seconds=None if video else (item.duration_seconds or 20),
                                 playback="once" if video else "timed", file=public_file(f)))
    if len(expanded) > 2000:
        raise MediaError("展开后最多2000个播放项")
    return expanded


def create_deployment(db, body):
    fingerprint = hashlib.sha256(dumps(body.model_dump(exclude={"request_id"})).encode()).hexdigest()
    with db.write() as c:
        expire_tasks(c)
        existing = c.execute("SELECT * FROM tasks WHERE request_id=?", (body.request_id,)).fetchone()
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise MediaError("request_id 已用于不同内容", 409)
            return task_view(c, existing)
        running = c.execute("SELECT * FROM tasks WHERE status='running'").fetchone()
        if running:
            raise MediaError(f"车间已有执行中的下发任务，截止时间（UTC）：{running['expires_at']}。屏幕离线时等待任务超时即可重新下发，无需等屏幕上线。", 409)
        version = require_active_version(c, body.playlist_version_id)
        if version["status"] != "published":
            raise MediaError("只能下发已发布版本", 409)
        for device_id in body.device_ids:
            require_row(c, "devices", "device_id", device_id)
        task_id, created = uid("task"), now()
        c.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,NULL)",
                  (task_id, body.request_id, fingerprint, version["id"], "running", created, after(body.timeout_seconds)))
        for device_id in body.device_ids:
            c.execute("UPDATE counters SET value=value+1 WHERE name='assignment_revision'")
            revision = c.execute("SELECT value FROM counters WHERE name='assignment_revision'").fetchone()[0]
            c.execute("INSERT INTO task_targets(task_id,device_id,assignment_revision) VALUES(?,?,?)", (task_id, device_id, revision))
            c.execute("""INSERT INTO assignments VALUES(?,?,?,?,?,NULL)
                ON CONFLICT(device_id) DO UPDATE SET assignment_revision=excluded.assignment_revision,
                playlist_version_id=excluded.playlist_version_id,task_id=excluded.task_id,
                assigned_at=excluded.assigned_at,relay_acked_at=NULL""", (device_id, revision, version["id"], task_id, created))
        return task_view(c, require_row(c, "tasks", "id", task_id))


def touch_relay(c, settings, relay_id):
    if relay_id != settings.relay_id:
        raise MediaError("未配置的中转 ID", 404)
    c.execute("INSERT INTO relay_state VALUES(?,?) ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at", (relay_id, now()))


def age_seconds(timestamp):
    return (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds()


def device_status(db):
    with db.read() as c:
        relay = c.execute("SELECT * FROM relay_state WHERE id=?", (db.settings.relay_id,)).fetchone()
        relay_seen = relay["last_seen_at"] if relay else None
        relay_online = bool(relay_seen and age_seconds(relay_seen) < db.settings.offline_seconds)
        summary = dict(registered=0, online=0, offline=0, unknown=0, pending=0)
        devices = []
        for row in c.execute("SELECT * FROM devices ORDER BY device_id"):
            device = dict(row)
            device['download_progress']=device_progress(c,row['device_id'],row['report_seq'])
            assignment = c.execute("SELECT * FROM assignments WHERE device_id=?", (row["device_id"],)).fetchone()
            device["desired"] = dict(assignment) if assignment else None
            state = "unknown" if not relay_online else (
                "online" if row["last_contact_at"] and age_seconds(row["last_contact_at"]) < db.settings.offline_seconds else "offline")
            pending = bool(assignment and (assignment["assignment_revision"] != row["reported_assignment_revision"] or
                           assignment["playlist_version_id"] != row["actual_version_id"]))
            device.update(online_state=state, pending=pending)
            summary["registered"] += 1
            summary[state] += 1
            summary["pending"] += int(pending)
            devices.append(device)
        return dict(summary=summary, relay=dict(state="online" if relay_online else "unknown", last_seen_at=relay_seen), devices=devices)


def accept_reports(db, body):
    results = []
    with db.write() as c:
        expire_tasks(c)
        touch_relay(c, db.settings, body.relay_id)
        for report in body.reports:
            payload = dumps(report_data(report))
            existing = c.execute("SELECT * FROM events WHERE event_id=?", (report.event_id,)).fetchone()
            if existing:
                if existing["payload"] != payload:
                    raise MediaError("event_id 已用于不同回报", 409)
                results.append(dict(event_id=report.event_id, status="duplicate"))
                continue
            device = require_row(c, "devices", "device_id", report.device_id)
            if report.report_seq <= device["report_seq"]:
                outcome = "stale"
            else:
                if report.actual_version_id:
                    version = require_row(c, "versions", "id", report.actual_version_id)
                    if version["status"] != "published":
                        raise MediaError("实际版本必须已发布")
                    if report.current_item_id and report.current_item_id not in {i["id"] for i in json.loads(version["items"])}:
                        raise MediaError("当前播放项不属于实际版本")
                elif report.current_item_id or report.playback_state in ("playing", "paused"):
                    raise MediaError("播放/暂停状态必须有实际版本，播放项需属于该版本")
                if report.playback_state in ("playing", "paused") and not report.current_item_id:
                    raise MediaError("播放/暂停时必须回报当前项")
                if report.pending_version_id:
                    pending = require_row(c, "versions", "id", report.pending_version_id)
                    if pending["status"] != "published":
                        raise MediaError("待更新版本必须已发布")
                target = None
                if report.assignment_revision:
                    target = c.execute("""SELECT tt.*,t.playlist_version_id,t.status AS task_status
                         FROM task_targets tt JOIN tasks t ON t.id=tt.task_id
                         WHERE tt.device_id=? AND tt.assignment_revision=?""", (report.device_id, report.assignment_revision)).fetchone()
                    if target is None:
                        raise MediaError("未知或不属于此设备的 assignment_revision", 409)
                if report.actual_version_id and (target is None or target["playlist_version_id"] != report.actual_version_id):
                    raise MediaError("实际播放版本和已应用 revision 不一致", 409)
                observed = report.observed_at.isoformat(timespec="milliseconds").replace("+00:00", "Z")
                # A delayed outbox delivery never makes an old device contact appear fresh.
                contact = max(observed, device["last_contact_at"] or observed)
                c.execute("""UPDATE devices SET actual_version_id=?,current_item_id=?,playback_state=?,
                      pending_version_id=?,reported_assignment_revision=?,report_seq=?,last_contact_at=?,error=? WHERE device_id=?""",
                          (report.actual_version_id, report.current_item_id, report.playback_state,
                           report.pending_version_id, report.assignment_revision, report.report_seq, contact, report.error, report.device_id))
                if target and target["task_status"] == "running" and target["status"] == "pending":
                    state = None
                    if report.actual_version_id == target["playlist_version_id"] and report.playback_state == "playing" and not report.error:
                        state = "succeeded"
                    elif report.playback_state == "error" and report.error:
                        state = "failed"
                    if state:
                        c.execute("UPDATE task_targets SET status=?,error=? WHERE task_id=? AND device_id=?",
                                  (state, report.error, target["task_id"], report.device_id))
                        pending = c.execute("SELECT 1 FROM task_targets WHERE task_id=? AND status='pending'", (target["task_id"],)).fetchone()
                        if not pending:
                            failed = c.execute("SELECT 1 FROM task_targets WHERE task_id=? AND status='failed'", (target["task_id"],)).fetchone()
                            c.execute("UPDATE tasks SET status=?,finished_at=? WHERE id=? AND status='running'",
                                      ("failed" if failed else "succeeded", now(), target["task_id"]))
                outcome = "accepted"
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?)", (report.event_id, report.device_id, report.report_seq, payload, now(), outcome))
            results.append(dict(event_id=report.event_id, status=outcome))
    return dict(results=results)


def device_progress(c,device_id,seq):
    row=c.execute("SELECT payload FROM events WHERE device_id=? AND report_seq=? AND outcome='accepted' LIMIT 1",(device_id,seq)).fetchone()
    return json.loads(row[0]).get('download_progress') if row else None
