import hashlib
import hmac
import json
import logging
import re
import shutil
import sqlite3
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from .cache import Cache
from .config import Settings
from .db import Database, age, now
from .engine import Engine
from .locking import instance_lock
from .protocol import ActivationInput, RelayError, ReportInput, SyncInput
from .store import Store
from shared.auth import Accounts


class BodyLimit:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http': return await self.app(scope, receive, send)
        total = 0
        async def limited():
            nonlocal total
            message = await receive()
            total += len(message.get('body', b''))
            if total > 65536: raise HTTPException(413, '请求体超过64KB')
            return message
        await self.app(scope, limited, send)


def create_app(settings=None, *, background=True, client=None):
    s = settings or Settings.from_env()
    db = Database(s)
    accounts=Accounts(s.data_dir,'relay',s.auth_session_hours,s.auth_cookie_secure)
    store = Store(db)
    cache = Cache(db, store)
    engine = Engine(db, store, cache, client)
    download_slots = threading.BoundedSemaphore(s.max_local_downloads)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        accounts.initialize()
        with instance_lock(s.data_dir):
            if background: engine.start()
            else: cache.recover()
            try: yield
            finally: engine.close()

    app = FastAPI(title='ESOP Windows Relay', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.store, app.state.cache, app.state.engine = db, store, cache, engine
    app.state.accounts=accounts
    accounts.mount(app)
    app.add_middleware(BodyLimit)

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers.update({'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer', 'X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
        return response

    def error(status, message, code):
        return JSONResponse(status_code=status, content={'error':dict(code=code, message=message)})

    @app.exception_handler(RelayError)
    async def relay_error(request, exc): return error(exc.status, str(exc), exc.code)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc): return error(exc.status_code, str(exc.detail), {401:'unauthorized',403:'forbidden',429:'too_many_requests',404:'not_found',405:'method_not_allowed',413:'too_large'}.get(exc.status_code,'bad_request'))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc): return error(422, '请求字段、UTC时间或类型无效，请检查接口契约', 'validation_error')

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc): return error(503, '本地存储不可用，请检查磁盘和权限', 'storage_error')

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logging.getLogger('esop.relay').error('Request failed type=%s', type(exc).__name__)
        return error(500, '中转内部错误，请检查健康状态', 'internal_error')

    def authenticate(request, expected):
        if not hmac.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer '+expected).encode()):
            raise HTTPException(401, '需要有效API Key')

    def device(request: Request): authenticate(request, s.device_key)
    def admin(request: Request):
        if request.headers.get('authorization'):authenticate(request,s.admin_key)
        else:accounts.require(request)

    @app.get('/api/v1/health')
    def health():
        with db.read() as c: c.execute('SELECT 1').fetchone()
        probe = s.data_dir/'tmp'/'health.probe'
        probe.write_bytes(b'ok'); probe.unlink(missing_ok=True)
        if background and not engine.healthy(): raise RelayError('中转后台线程不健康',503,'unavailable')
        return {'status':'ok'}

    @app.post('/api/v1/device/sync', dependencies=[Depends(device)])
    def sync(body: SyncInput):
        # Same lock serializes ready checks against target replacement and GC.
        with cache.lock:
            assignment = store.desired(body.device_id)
            if body.known_assignment_revision > (assignment['assignment_revision'] if assignment else 0):
                raise RelayError('中转目标暂时落后于终端，请等待中心同步',503,'sync_behind')
            ready = bool(assignment and cache.ready(assignment['playlist_version_id']))
            manifest, message = None, None
            if assignment:
                with db.read() as c:
                    row = c.execute('SELECT * FROM versions WHERE id=?', (assignment['playlist_version_id'],)).fetchone()
                    manifest = json.loads(row['manifest']) if ready else None
                    message = row['error'] if row else '清单尚未同步'
            # Recheck after file verification: sync ingestion uses this lock as well.
            with db.read() as c:
                seq=c.execute('SELECT report_seq FROM devices WHERE device_id=?',(body.device_id,)).fetchone()[0]
            return dict(server_time=now(), heartbeat_seconds=db.meta('heartbeat_seconds', s.heartbeat_seconds),
                        assignment=assignment, manifest=manifest, ready=ready, error=None if ready else message,
                        manifest_sha256=row['sha256'] if ready else None,
                        report_seq_floor=seq)

    @app.post('/api/v1/device/activation-check', dependencies=[Depends(device)])
    def activation(body: ActivationInput):
        with cache.lock:
            assignment = store.desired(body.device_id)
            if not assignment or assignment['assignment_revision'] != body.assignment_revision or assignment['playlist_version_id'] != body.playlist_version_id:
                raise RelayError('目标已更新，禁止激活旧安排，请重新同步')
            if not cache.ready(body.playlist_version_id):
                raise RelayError('目标文件尚未全部准备好',503,'not_ready')
            return dict(allowed=True, assignment=assignment)

    @app.post('/api/v1/device/reports', dependencies=[Depends(device)])
    def report(body: ReportInput): return store.accept_report(body.report)

    @app.api_route('/api/v1/files/{file_id}', methods=['GET','HEAD'], dependencies=[Depends(device)])
    def download(file_id: str, request: Request):
        with cache.lock:
            with db.read() as c:
                row = c.execute('SELECT * FROM files WHERE id=?', (file_id,)).fetchone()
            if row is None: raise RelayError('文件不存在',404,'not_found')
            if row['status'] != 'ready' or not cache.valid_file(row):
                raise RelayError('文件尚未准备好或校验失败',503,'not_ready')
            file = json.loads(row['descriptor'])
            total = file['size_bytes']
            etag = '"'+file['sha256']+'"'
            headers = {'Accept-Ranges':'bytes', 'ETag':etag}
            start, end, status = 0, total-1, 200
            range_header = request.headers.get('range')
            if range_header and len(range_header) > 128:
                return Response(status_code=416, headers={**headers, 'Content-Range':f'bytes */{total}'})
            if range_header and request.headers.get('if-range', etag) == etag:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header.strip())
                if match and any(match.groups()):
                    first, last = match.groups()
                    if first:
                        start = int(first); end = min(int(last), total-1) if last else total-1
                    else:
                        length = int(last); start = max(0,total-length)
                        if length == 0: start = total
                    status = 206
                else: start = total
                if start >= total or end < start:
                    return Response(status_code=416, headers={**headers, 'Content-Range':f'bytes */{total}'})
                headers['Content-Range'] = f'bytes {start}-{end}/{total}'
            headers['Content-Length'] = str(end-start+1)
            if request.method == 'HEAD': return Response(status_code=status, headers=headers, media_type=file['media_type'])
            if not download_slots.acquire(blocking=False):
                raise RelayError('并发下载已满，请稍后重试',503,'download_busy')
            try: stream = cache.path(file).open('rb')
            except BaseException:
                download_slots.release()
                raise
            cache.leases[file_id] = cache.leases.get(file_id,0)+1
        close_lock = threading.Lock()
        closed = False
        def release():
            nonlocal closed
            with close_lock:
                if closed: return
                closed = True
                stream.close()
                download_slots.release()
                with cache.lock:
                    cache.leases[file_id] -= 1
        def chunks():
            try:
                stream.seek(start)
                left = end-start+1
                while left:
                    chunk = stream.read(min(left,256*1024))
                    if not chunk: break
                    left -= len(chunk)
                    yield chunk
            finally: release()
        return StreamingResponse(chunks(), status_code=status, media_type=file['media_type'], headers=headers, background=BackgroundTask(release))

    @app.get('/api/v1/local/status', dependencies=[Depends(admin)])
    def status():
        center = db.meta('center', dict(state='unknown',last_seen_at=None,error=None))
        if center.get('last_seen_at') and age(center['last_seen_at']) > db.meta('offline_seconds',s.offline_seconds): center['state']='offline'
        disk = shutil.disk_usage(s.data_dir)
        devices = []
        with db.read() as c:
            for row in c.execute('SELECT * FROM devices WHERE registered=1 ORDER BY device_id'):
                r = json.loads(row['report']) if row['report'] else {}
                assignment = c.execute('SELECT payload FROM assignments WHERE device_id=?',(row['device_id'],)).fetchone()
                desired = json.loads(assignment[0]) if assignment else None
                version = c.execute('SELECT error FROM versions WHERE id=?',(desired['playlist_version_id'],)).fetchone() if desired else None
                blocked = c.execute("SELECT error FROM events WHERE device_id=? AND delivery='blocked' ORDER BY received_at DESC LIMIT 1", (row['device_id'],)).fetchone()
                devices.append(dict(device_id=row['device_id'],name=row['name'],last_contact_at=row['last_contact_at'],
                    online_state='unknown' if not row['last_contact_at'] else ('online' if age(row['last_contact_at']) < db.meta('offline_seconds',s.offline_seconds) else 'offline'),
                    desired=desired, actual_version_id=r.get('actual_version_id'), current_item_id=r.get('current_item_id'),
                    playback_state=r.get('playback_state','idle'),pending_version_id=r.get('pending_version_id'),
                    report_seq=row['report_seq'],reported_assignment_revision=r.get('assignment_revision',0),error=r.get('error'),
                    sync_error='；'.join(v for v in ((version[0] if version else None),(blocked[0] if blocked else None)) if v) or None,
                    download_progress=r.get('download_progress')))
            downloads = [dict(file_id=r['id'],status=r['status'],received_bytes=r['received_bytes'],
                              size_bytes=json.loads(r['descriptor'])['size_bytes'],error=r['error']) for r in c.execute("SELECT * FROM files WHERE status!='ready'")]
            outbox = {state:c.execute('SELECT count(*) FROM events WHERE delivery=?',(state,)).fetchone()[0] for state in ('pending','blocked')}
        return dict(server_time=now(),relay_id=s.relay_id,center=center,
            configuration=dict(configured=True,heartbeat_seconds=db.meta('heartbeat_seconds',s.heartbeat_seconds),offline_seconds=db.meta('offline_seconds',s.offline_seconds)),
            disk=dict(total_bytes=disk.total,free_bytes=disk.free,reserve_bytes=s.reserve_bytes),
            downloads=downloads,outbox=outbox,devices=devices)

    static = Path(__file__).parent/'static'
    @app.get('/')
    def index(): return FileResponse(static/'index.html', media_type='text/html')
    app.mount('/static', StaticFiles(directory=static), name='static')
    app.mount('/auth-ui',StaticFiles(directory=Path(__file__).parents[1]/'shared'/'web'),name='auth-ui')
    return app
