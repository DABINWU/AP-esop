import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path

from .db import age, dumps, now
from .protocol import RelayError, identifier


class Cache:
    def __init__(self, db, store):
        self.db, self.store = db, store
        self.root = db.settings.data_dir
        self.lock = threading.RLock()
        self.leases = {}

    def path(self, file):
        identifier(file['id'])
        name = file['id']+'.'+file['sha256']+'.bin'
        path = self.root/'cache'/name
        if path.is_symlink() or not path.resolve().is_relative_to((self.root/'cache').resolve()):
            raise RelayError('缓存路径无效', 503, 'storage_error')
        return path

    def manifest_path(self, version_id):
        return self.root/'manifests'/(identifier(version_id)+'.json')

    def valid_file(self, row, force=False):
        file = json.loads(row['descriptor'])
        path = self.path(file)
        if not path.is_file() or path.stat().st_size != file['size_bytes']:
            return False
        stamp = path.stat().st_mtime_ns
        if force or row['status'] != 'ready' or row['verified_mtime'] != stamp:
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != file['sha256']:
                    return False
            with self.db.write() as c:
                c.execute("UPDATE files SET status='ready',verified_mtime=?,received_bytes=?,error=NULL WHERE id=?",
                          (stamp, file['size_bytes'], file['id']))
        return True

    def ready(self, version_id):
        with self.lock:
            with self.db.read() as c:
                version = c.execute('SELECT * FROM versions WHERE id=?', (version_id,)).fetchone()
                rows = c.execute('SELECT f.* FROM files f JOIN version_files vf ON vf.file_id=f.id WHERE vf.version_id=?', (version_id,)).fetchall()
            if version is None or version['status'] != 'ready':
                return False
            path = self.manifest_path(version_id)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != version['sha256'] or not all(self.valid_file(r) for r in rows):
                with self.db.write() as c:
                    c.execute("UPDATE versions SET status='missing',error='缓存校验失效，等待重新下载',retry_at=0 WHERE id=?", (version_id,))
                return False
            return True

    def recover(self):
        with self.lock:
            for path in (self.root/'tmp').glob('*.partial'):
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to((self.root/'tmp').resolve()):
                    path.unlink()
            with self.db.read() as c:
                rows = c.execute('SELECT * FROM files').fetchall()
            for row in rows:
                if not self.valid_file(row, force=True):
                    with self.db.write() as c:
                        c.execute("UPDATE files SET status='missing',received_bytes=0,verified_mtime=NULL WHERE id=?", (row['id'],))
            with self.db.write() as c:
                c.execute('UPDATE versions SET retry_at=0')
            with self.db.read() as c:
                versions = [r[0] for r in c.execute("SELECT id FROM versions WHERE status='ready'")]
            for version in versions: self.ready(version)

    def cleanup(self, pressure=False):
        with self.lock, self.db.write() as c:
            protected = self.store.protected_versions(c)
            for row in c.execute('SELECT id,touched_at FROM versions ORDER BY touched_at').fetchall():
                if row['id'] in protected or (not pressure and age(row['touched_at']) < self.db.settings.cache_retention_hours*3600):
                    continue
                self.manifest_path(row['id']).unlink(missing_ok=True)
                c.execute("UPDATE versions SET status='missing' WHERE id=?", (row['id'],))
            keep_files = {r[0] for r in c.execute("SELECT vf.file_id FROM version_files vf JOIN versions v ON v.id=vf.version_id WHERE v.status='ready'")}
            for vid in protected:
                keep_files.update(r[0] for r in c.execute('SELECT file_id FROM version_files WHERE version_id=?', (vid,)))
            for row in c.execute('SELECT * FROM files').fetchall():
                if row['id'] in keep_files or row['status'] == 'downloading' or self.leases.get(row['id'], 0):
                    continue
                self.path(json.loads(row['descriptor'])).unlink(missing_ok=True)
                c.execute("UPDATE files SET status='missing',received_bytes=0,verified_mtime=NULL WHERE id=?", (row['id'],))

    def ensure_space(self, required):
        if shutil.disk_usage(self.root).free < required + self.db.settings.reserve_bytes:
            self.cleanup(pressure=True)
        if shutil.disk_usage(self.root).free < required + self.db.settings.reserve_bytes:
            raise RelayError('磁盘空间不足，已保留当前及受保护内容；请释放空间', 503, 'disk_full')

    def download(self, client, file, stop, needed):
        settings = self.db.settings
        with self.lock:
            with self.db.read() as c:
                row = c.execute('SELECT * FROM files WHERE id=?', (file['id'],)).fetchone()
            if self.valid_file(row): return
            self.ensure_space(file['size_bytes'])
            with self.db.write() as c:
                c.execute("UPDATE files SET status='downloading',received_bytes=0,error=NULL WHERE id=?", (file['id'],))
        temporary = self.root/'tmp'/(identifier(file['id'])+'.partial')
        if temporary.is_symlink(): raise RelayError('临时缓存路径无效', 503, 'storage_error')
        error = None
        try:
            for attempt in range(settings.retry_count):
                received, digest = 0, hashlib.sha256()
                deadline = time.monotonic() + settings.download_timeout
                try:
                    with client.stream('GET', settings.center_url.rstrip('/')+file['download_path'], headers={'Accept-Encoding':'identity'}) as response:
                        response.raise_for_status()
                        if response.status_code != 200 or response.headers.get('content-encoding', 'identity') != 'identity':
                            raise RelayError('中心文件响应无效', 503, 'download_error')
                        with temporary.open('wb') as stream:
                            for chunk in response.iter_bytes(256*1024):
                                if stop.is_set() or not needed():
                                    raise InterruptedError('安排已更新或服务停止，旧下载不再激活')
                                if time.monotonic() > deadline:
                                    raise TimeoutError('文件总下载超时')
                                received += len(chunk)
                                if received > file['size_bytes']:
                                    raise RelayError('下载文件超出声明大小', 503, 'checksum_mismatch')
                                if shutil.disk_usage(self.root).free < len(chunk) + settings.reserve_bytes:
                                    raise RelayError('下载期间磁盘不足', 503, 'disk_full')
                                stream.write(chunk)
                                digest.update(chunk)
                                with self.db.write() as c:
                                    c.execute('UPDATE files SET received_bytes=? WHERE id=?', (received, file['id']))
                            stream.flush()
                            os.fsync(stream.fileno())
                    if received != file['size_bytes'] or digest.hexdigest() != file['sha256']:
                        raise RelayError('文件大小或SHA-256校验失败', 503, 'checksum_mismatch')
                    with self.lock:
                        if not needed(): raise InterruptedError('目标已替换')
                        os.replace(temporary, self.path(file))
                        stamp = self.path(file).stat().st_mtime_ns
                        with self.db.write() as c:
                            c.execute("UPDATE files SET status='ready',verified_mtime=?,error=NULL WHERE id=?", (stamp, file['id']))
                    return
                except (InterruptedError, RelayError) as exc:
                    if isinstance(exc, InterruptedError) or exc.code == 'disk_full': raise
                    error = exc
                except Exception as exc:
                    error = exc
                temporary.unlink(missing_ok=True)
                if attempt+1 < settings.retry_count and stop.wait(min(2**attempt, settings.retry_max_seconds)):
                    raise InterruptedError('服务停止')
            raise RelayError('文件下载失败，请检查中心连接、文件校验和磁盘空间', 503, 'download_error') from error
        finally:
            temporary.unlink(missing_ok=True)
            with self.db.write() as c:
                c.execute("UPDATE files SET status='missing',error=? WHERE id=? AND status='downloading'",
                          ('下载未完成，等待安全重试', file['id']))

    def finish(self, version_id):
        with self.lock:
            with self.db.read() as c:
                version = c.execute('SELECT * FROM versions WHERE id=?', (version_id,)).fetchone()
                rows = c.execute('SELECT f.* FROM files f JOIN version_files vf ON f.id=vf.file_id WHERE vf.version_id=?', (version_id,)).fetchall()
            if not rows or not all(self.valid_file(r) for r in rows):
                raise RelayError('版本文件尚未全部通过校验', 503, 'not_ready')
            self.ensure_space(len(version['manifest'].encode('utf-8'))+65536)
            target = self.manifest_path(version_id)
            temporary = self.root/'tmp'/(identifier(version_id)+'.partial')
            with temporary.open('wb') as stream:
                stream.write(version['manifest'].encode('utf-8')); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, target)
            with self.db.write() as c:
                c.execute("UPDATE versions SET status='ready',error=NULL,failures=0,retry_at=0,touched_at=? WHERE id=?", (now(), version_id))
            self.store.promote_ready(version_id)
