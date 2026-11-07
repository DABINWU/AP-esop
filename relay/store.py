"""Transactional target snapshots, durable reports, and activation guards."""
import json
import time

from .db import age, dumps, now
from .protocol import Assignment, RelayError, identifier, utc, validate_version
from shared.reports import report_data


class Store:
    def __init__(self, db):
        self.db = db

    def ingest(self, snapshot):
        s = self.db.settings
        utc(snapshot['server_time'])
        heartbeat, offline = snapshot['heartbeat_seconds'], snapshot['offline_seconds']
        if type(heartbeat) is not int or type(offline) is not int or not 0 < heartbeat < offline <= 86400:
            raise RelayError('中心心跳配置无效')
        versions = [validate_version(v, s) for v in snapshot['versions']]
        if len({v[0] for v in versions}) != len(versions):
            raise RelayError('版本ID重复')
        assignments = [Assignment.model_validate(a) for a in snapshot['assignments']]
        history = [Assignment.model_validate(a) for a in snapshot['assignment_history']]
        devices = snapshot['devices']
        device_ids = {identifier(d['device_id']) for d in devices}
        if len(device_ids) != len(devices) or len({a.device_id for a in assignments}) != len(assignments):
            raise RelayError('设备或安排重复')
        for a in assignments + history:
            identifier(a.playlist_version_id)
            identifier(a.task_id)
            utc(a.assigned_at)
            if a.device_id not in device_ids:
                raise RelayError('安排引用未登记屏幕')
        version_ids = {v[0] for v in versions}
        if any(a.playlist_version_id not in version_ids for a in assignments):
            raise RelayError('目标缺少清单')
        with self.db.write() as c:
            # Validate all immutable identities before committing any new target.
            for vid, manifest, digest, files in versions:
                row = c.execute('SELECT * FROM versions WHERE id=?', (vid,)).fetchone()
                if row and (row['sha256'] != digest or row['files_json'] != dumps(sorted(files, key=lambda f:f['id']))):
                    raise RelayError('不可变版本被改变')
                c.execute('INSERT OR IGNORE INTO versions(id,manifest,sha256,files_json,touched_at) VALUES(?,?,?,?,?)',
                          (vid, dumps(manifest), digest, dumps(sorted(files, key=lambda f:f['id'])), now()))
                for file in files:
                    existing = c.execute('SELECT descriptor FROM files WHERE id=?', (file['id'],)).fetchone()
                    if existing and existing[0] != dumps(file):
                        raise RelayError('不可变文件ID被改变')
                    c.execute('INSERT OR IGNORE INTO files(id,descriptor) VALUES(?,?)', (file['id'], dumps(file)))
                    c.execute('INSERT OR IGNORE INTO version_files VALUES(?,?)', (vid, file['id']))
            c.execute('UPDATE devices SET registered=0')
            for device in devices:
                existing = c.execute('SELECT * FROM devices WHERE device_id=?', (device['device_id'],)).fetchone()
                # Never replace a newer locally accepted report with a delayed center snapshot.
                seq = device.get('report_seq', 0)
                if type(seq) is not int or seq < 0:
                    raise RelayError('设备序号无效')
                contact = device.get('last_contact_at')
                if contact: utc(contact)
                report = {k: device.get(k) for k in ('actual_version_id', 'current_item_id', 'playback_state', 'pending_version_id', 'error','download_progress')}
                report['assignment_revision'] = device.get('reported_assignment_revision', 0)
                if existing is None:
                    c.execute('INSERT INTO devices(device_id,name,report,report_seq,last_contact_at) VALUES(?,?,?,?,?)',
                              (device['device_id'], device['name'], dumps(report), seq, contact))
                elif seq > existing['report_seq']:
                    c.execute('UPDATE devices SET report=?,report_seq=?,last_contact_at=? WHERE device_id=?',
                              (dumps(report), seq, max(contact or '', existing['last_contact_at'] or '') or None, device['device_id']))
                c.execute('UPDATE devices SET name=?,registered=1 WHERE device_id=?', (device['name'], device['device_id']))
            for a in history + assignments:
                previous = c.execute('SELECT * FROM assignment_history WHERE device_id=? AND revision=?',
                                     (a.device_id, a.assignment_revision)).fetchone()
                payload = a.model_dump()
                # relay_acked_at is a receipt, not part of immutable target identity.
                identity = {k:v for k,v in payload.items() if k != 'relay_acked_at'}
                if previous and {k:v for k,v in json.loads(previous['payload']).items() if k != 'relay_acked_at'} != identity:
                    raise RelayError('相同revision对应不同安排')
                c.execute('INSERT OR IGNORE INTO assignment_history VALUES(?,?,?,?)',
                          (a.device_id, a.assignment_revision, a.playlist_version_id, dumps(payload)))
            for a in assignments:
                current = c.execute('SELECT revision FROM assignments WHERE device_id=?', (a.device_id,)).fetchone()
                if current is None or a.assignment_revision >= current[0]:
                    c.execute('INSERT INTO assignments VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload',
                              (a.device_id, a.assignment_revision, dumps(a.model_dump())))
            for key, value in [('heartbeat_seconds', heartbeat), ('offline_seconds', offline),
                               ('center', dict(state='online', last_seen_at=now(), error=None))]:
                c.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dumps(value)))

    def desired(self, device_id):
        with self.db.read() as c:
            device = c.execute('SELECT * FROM devices WHERE device_id=? AND registered=1', (device_id,)).fetchone()
            if device is None:
                raise RelayError('屏幕未在中心登记或尚未同步', 404, 'not_found')
            a = c.execute('SELECT payload FROM assignments WHERE device_id=?', (device_id,)).fetchone()
            return json.loads(a[0]) if a else None

    def accept_report(self, report):
        payload = dumps(report_data(report))
        with self.db.write() as c:
            existing = c.execute('SELECT * FROM events WHERE event_id=?', (report.event_id,)).fetchone()
            if existing:
                if existing['payload'] != payload:
                    raise RelayError('event_id已用于不同回报')
                return dict(event_id=report.event_id, status='duplicate')
            device = c.execute('SELECT * FROM devices WHERE device_id=? AND registered=1', (report.device_id,)).fetchone()
            if device is None:
                raise RelayError('屏幕未登记', 404, 'not_found')
            if c.execute("SELECT count(*) FROM events WHERE delivery!='sent'").fetchone()[0] >= self.db.settings.max_outbox_events:
                raise RelayError('待上报队列已满，请检查中心连接', 503, 'outbox_full')
            outcome = 'stale' if report.report_seq <= device['report_seq'] else 'accepted'
            if outcome == 'accepted':
                target = c.execute('SELECT version_id FROM assignment_history WHERE device_id=? AND revision=?',
                                   (report.device_id, report.assignment_revision)).fetchone() if report.assignment_revision else None
                if report.assignment_revision and target is None:
                    raise RelayError('未知或不属于该屏幕的revision，请先同步')
                if report.actual_version_id:
                    version = c.execute('SELECT manifest FROM versions WHERE id=?', (report.actual_version_id,)).fetchone()
                    if target is None or target[0] != report.actual_version_id or version is None:
                        raise RelayError('实际版本和revision不匹配')
                    if report.current_item_id and report.current_item_id not in {i['id'] for i in json.loads(version[0])['items']}:
                        raise RelayError('当前播放项不属于实际版本', 422, 'validation_error')
                elif report.current_item_id or report.playback_state in ('playing', 'paused'):
                    raise RelayError('播放或暂停必须有实际版本和当前项', 422, 'validation_error')
                if report.playback_state in ('playing', 'paused') and not report.current_item_id:
                    raise RelayError('缺少当前播放项', 422, 'validation_error')
                if report.pending_version_id and not c.execute('SELECT 1 FROM versions WHERE id=?', (report.pending_version_id,)).fetchone():
                    raise RelayError('未知待更新版本')
                contact = report.observed_at.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
                c.execute('UPDATE devices SET report=?,report_seq=?,last_contact_at=? WHERE device_id=?',
                          (payload, report.report_seq, max(contact, device['last_contact_at'] or contact), report.device_id))
            c.execute('INSERT INTO events(event_id,device_id,report_seq,payload,outcome,received_at) VALUES(?,?,?,?,?,?)',
                      (report.event_id, report.device_id, report.report_seq, payload, outcome, now()))
        return dict(event_id=report.event_id, status=outcome)

    def needed_versions(self):
        with self.db.read() as c:
            ids = {json.loads(a[0])['playlist_version_id'] for a in c.execute('SELECT payload FROM assignments')}
            for row in c.execute('SELECT report FROM devices WHERE report IS NOT NULL'):
                r = json.loads(row[0])
                ids.update(r[k] for k in ('actual_version_id', 'pending_version_id') if r.get(k))
            return ids

    def promote_ready(self, version_id):
        with self.db.write() as c:
            for row in c.execute('SELECT device_id,payload FROM assignments').fetchall():
                if json.loads(row['payload'])['playlist_version_id'] != version_id:
                    continue
                head = c.execute('SELECT * FROM heads WHERE device_id=?', (row['device_id'],)).fetchone()
                if head is None:
                    c.execute('INSERT INTO heads VALUES(?,?,NULL)', (row['device_id'], version_id))
                elif head['current_version'] != version_id:
                    c.execute('UPDATE heads SET current_version=?,previous_version=? WHERE device_id=?',
                              (version_id, head['current_version'], row['device_id']))

    def protected_versions(self, c):
        ids = {json.loads(a[0])['playlist_version_id'] for a in c.execute('SELECT payload FROM assignments')}
        for row in c.execute('SELECT current_version,previous_version FROM heads'):
            ids.update(v for v in row if v)
        for row in c.execute('SELECT report FROM devices WHERE report IS NOT NULL'):
            report = json.loads(row[0])
            ids.update(report[k] for k in ('actual_version_id', 'pending_version_id') if report.get(k))
        for row in c.execute("SELECT payload FROM events WHERE delivery!='sent'"):
            report = json.loads(row[0])
            ids.update(report[k] for k in ('actual_version_id', 'pending_version_id') if report.get(k))
        return ids
