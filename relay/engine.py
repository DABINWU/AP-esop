import json
import logging
import threading
import time

import httpx

from .db import dumps, now
from .protocol import RelayError

log = logging.getLogger('esop.relay')


class Engine:
    def __init__(self, db, store, cache, client=None):
        self.db, self.store, self.cache = db, store, cache
        self.settings = db.settings
        self.stop_event = threading.Event()
        self.threads = []
        self.beats = {}
        self.client = client or httpx.Client(headers={'Authorization':'Bearer '+self.settings.center_key},
            timeout=httpx.Timeout(self.settings.read_timeout, connect=self.settings.connect_timeout),
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=2), follow_redirects=False,
            trust_env=False)
        self.owns_client = client is None
        self.last_cleanup = 0

    def request_json(self, path, body):
        with self.client.stream('POST', self.settings.center_url.rstrip('/')+'/api/v1/'+path, json=body) as response:
            response.raise_for_status()
            data = bytearray()
            started = time.monotonic()
            for chunk in response.iter_bytes(65536):
                data.extend(chunk)
                if len(data) > self.settings.max_json_bytes or time.monotonic()-started > self.settings.download_timeout:
                    raise RelayError('中心JSON响应过大或超时', 503, 'protocol_error')
            return json.loads(data)

    def sync_once(self):
        snapshot = self.request_json('relay/sync', {'relay_id':self.settings.relay_id})
        with self.cache.lock:
            self.store.ingest(snapshot)

    def ack_once(self):
        with self.db.read() as c:
            rows = [json.loads(r[0]) for r in c.execute('SELECT payload FROM assignments')]
        ready = []
        for a in rows:
            if self.cache.ready(a['playlist_version_id']):
                self.store.promote_ready(a['playlist_version_id'])
                ready.append(dict(device_id=a['device_id'], assignment_revision=a['assignment_revision']))
        if ready:
            self.request_json('relay/acks', {'relay_id':self.settings.relay_id, 'assignments':ready})

    def flush_once(self):
        # One event per transaction upstream: a permanently rejected event cannot poison a batch.
        with self.db.read() as c:
            rows = c.execute("SELECT * FROM events WHERE delivery='pending' AND retry_at<=? ORDER BY received_at,event_id LIMIT 100", (time.time(),)).fetchall()
        started = time.monotonic()
        for row in rows:
            if self.stop_event.is_set(): break
            self.beats['reports'] = time.monotonic()
            try:
                result = self.request_json('relay/reports', {'relay_id':self.settings.relay_id, 'reports':[json.loads(row['payload'])]})
                expected = result.get('results', [])
                if len(expected) != 1 or expected[0].get('event_id') != row['event_id'] or expected[0].get('status') not in ('accepted', 'duplicate', 'stale'):
                    raise RelayError('中心回报确认格式无效', 503, 'protocol_error')
                with self.db.write() as c:
                    c.execute("UPDATE events SET delivery='sent',error=NULL WHERE event_id=?", (row['event_id'],))
            except httpx.HTTPStatusError as exc:
                permanent = exc.response.status_code in (400, 404, 409, 422)
                with self.db.write() as c:
                    c.execute('UPDATE events SET delivery=?,error=?,retry_at=? WHERE event_id=?',
                        ('blocked' if permanent else 'pending', '中心拒绝回报 HTTP '+str(exc.response.status_code),
                         time.time()+self.settings.retry_max_seconds, row['event_id']))
                if not permanent: raise
            except Exception:
                with self.db.write() as c:
                    c.execute('UPDATE events SET error=?,retry_at=? WHERE event_id=?',
                              ('中心断线或响应异常，待重传', time.time()+self.settings.poll_seconds, row['event_id']))
                raise
            if time.monotonic()-started >= self.settings.poll_seconds: break

    def download_once(self):
        needed = self.store.needed_versions()
        with self.db.read() as c:
            versions = c.execute('SELECT * FROM versions WHERE retry_at<=? ORDER BY touched_at DESC,id', (time.time(),)).fetchall()
        for version in versions:
            vid = version['id']
            if vid not in needed: continue
            if self.cache.ready(vid):
                self.store.promote_ready(vid)
                continue
            try:
                for file in json.loads(version['files_json']):
                    self.beats['download'] = time.monotonic()
                    self.cache.download(self.client, file, self.stop_event, lambda: vid in self.store.needed_versions())
                if vid in self.store.needed_versions(): self.cache.finish(vid)
            except InterruptedError:
                return
            except Exception as exc:
                message = str(exc) if isinstance(exc, RelayError) else '下载失败，请检查网络、磁盘和文件权限'
                # No URLs, headers, exception repr or response bodies reach logs/status.
                message = message[:500]
                delay = min(self.settings.poll_seconds * 2**min(version['failures'], 6), self.settings.retry_max_seconds)
                with self.db.write() as c:
                    c.execute("UPDATE versions SET status='missing',error=?,failures=failures+1,retry_at=? WHERE id=?",
                              (message, time.time()+delay, vid))
                log.warning('Version cache not ready code=%s', getattr(exc, 'code', 'storage_or_network'))
            return
        if time.monotonic()-self.last_cleanup > 60:
            self.cache.cleanup()
            self.last_cleanup = time.monotonic()

    def _sync_loop(self):
        failures = 0
        while not self.stop_event.is_set():
            self.beats['sync'] = time.monotonic()
            try:
                self.sync_once()
                self.ack_once()
                failures = 0
            except Exception as exc:
                failures += 1
                previous = self.db.meta('center', {})
                message = str(exc) if isinstance(exc, RelayError) else '中心连接或协议异常，请检查VPN、地址和配置密钥'
                self.db.set_meta('center', dict(state='offline', last_seen_at=previous.get('last_seen_at'), error=message[:500]))
                log.warning('Center synchronization unavailable type=%s', type(exc).__name__)
            self.beats['sync'] = time.monotonic()
            self.stop_event.wait(min(self.settings.poll_seconds*2**min(failures, 4), self.settings.retry_max_seconds))

    def _download_loop(self):
        while not self.stop_event.is_set():
            self.beats['download'] = time.monotonic()
            try: self.download_once()
            except Exception as exc:
                self.db.set_meta('download_error', '缓存处理失败，请检查磁盘空间、权限和数据库')
                log.warning('Cache processing unavailable type=%s', type(exc).__name__)
            self.beats['download'] = time.monotonic()
            self.stop_event.wait(1)

    def _reports_loop(self):
        while not self.stop_event.is_set():
            self.beats['reports'] = time.monotonic()
            try: self.flush_once()
            except Exception:
                log.warning('Report delivery deferred; events remain durable')
            self.beats['reports'] = time.monotonic()
            self.stop_event.wait(self.settings.poll_seconds)

    def start(self):
        self.cache.recover()
        for name, target in [('sync', self._sync_loop), ('download', self._download_loop), ('reports', self._reports_loop)]:
            self.beats[name] = time.monotonic()
            thread = threading.Thread(target=target, name='relay-'+name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def healthy(self):
        limits = {'sync': self.settings.download_timeout*2+self.settings.retry_max_seconds+30,
                  'reports': self.settings.download_timeout+self.settings.read_timeout+self.settings.poll_seconds+30,
                  'download': self.settings.retry_count*(self.settings.download_timeout+self.settings.read_timeout+10)+30}
        return all(t.is_alive() for t in self.threads) and all(time.monotonic()-stamp < limits[name] for name,stamp in self.beats.items())

    def close(self):
        self.stop_event.set()
        for thread in self.threads: thread.join(timeout=self.settings.read_timeout+2)
        if self.owns_client: self.client.close()
