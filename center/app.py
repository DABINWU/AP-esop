import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from .version import CENTER_VERSION
from .retention import enforce as retention_enforce, retire as retention_retire, run as retention_run
from .asset_delete import cleanup_deleting_assets
from pydantic import BaseModel, ConfigDict, Field
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings
from shared.auth import Accounts
from .asset_delete import asset_usage, delete_asset
from .categories import Category, MODULES, manifest_modules
from .playlist_workflow import editable_version, attach_asset, scoped_assets, remove_file, archive_playlist, archive_version
from .db import Database, now, uid, dumps, expire_tasks, next_task_number
from .media import MediaError, excel_warnings, verify_image, verify_video, file_record, insert_file, public_file
from .models import PlaylistCreate, DeviceCreate, ItemsInput, DeploymentCreate, RelayInput, AcksInput, ReportsInput
from .service import (require_row, asset_view, version_view, task_view, expand_items,
                      create_deployment, device_status, touch_relay, accept_reports, require_active_playlist, require_active_version)


def error_response(status, message, code=None, details=None):
    codes = {400:"bad_request",401:"unauthorized",403:'forbidden',429:'too_many_requests',404:"not_found",405:"method_not_allowed",409:"conflict",413:"too_large",
             415:"unsupported_media",422:"validation_error",503:"unavailable"}
    error = {"code":code or codes.get(status,"server_error"),"message":message}
    if details is not None:
        error['details'] = details
    return JSONResponse(status_code=status, content={"error":error})


class UploadLimit:
    def __init__(self, app, limit, admin_key, accounts):
        self.app, self.limit, self.admin_key, self.accounts = app, limit, admin_key.encode(), accounts

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not (scope['path']=='/api/v1/assets' or re.fullmatch(r'/api/v1/versions/[^/]+/modules/[^/]+/assets', scope['path'])):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        # Authenticate before multipart parsing/spooling, not after receiving an entire file.
        if not hmac.compare_digest(headers.get(b"authorization", b""), b"Bearer " + self.admin_key):
            try:
                if b'authorization' in headers: raise HTTPException(401,'需要管理员权限')
                self.accounts.require(Request(scope))
            except StarletteHTTPException as exc:
                return await error_response(exc.status_code,str(exc.detail))(scope, receive, send)
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await error_response(400, "Content-Length 无效")(scope, receive, send)
        if length > self.limit:
            return await error_response(413, "上传请求超过文件大小上限")(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > self.limit:
                raise HTTPException(413, "上传请求超过文件大小上限")
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings=None):
    s = settings or Settings.from_env()
    db = Database(s)
    accounts=Accounts(s.data_dir,'center',s.auth_session_hours,s.auth_cookie_secure)

    @asynccontextmanager
    async def lifespan(app):
        db.initialize()
        accounts.initialize()
        yield

    app = FastAPI(title="工厂 ESOP 中心 V1", version=CENTER_VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.settings = db, s
    app.state.accounts=accounts
    accounts.mount(app)
    app.add_middleware(UploadLimit, limit=max(s.upload_max_bytes, s.video_max_bytes) + 65536, admin_key=s.admin_key, accounts=accounts)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options":"nosniff", "Referrer-Policy":"no-referrer",
            "Cache-Control":"no-store", "X-Frame-Options":"DENY",
            "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; frame-src blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"})
        return response

    @app.exception_handler(MediaError)
    async def media_error(request, exc):
        return error_response(exc.status, str(exc), exc.code, exc.details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        message = "; ".join(".".join(str(v) for v in e["loc"]) + ": " + e["msg"] for e in exc.errors())
        return error_response(422, message)

    @app.exception_handler(sqlite3.OperationalError)
    async def db_error(request, exc):
        return error_response(503, "数据库暂不可用，请稍后重试")

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return error_response(503, "持久化存储暂不可用，请检查空间和目录权限")

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger("esop.web").error("Unhandled exception type=%s", type(exc).__name__)
        return error_response(500, "中心内部错误，请检查服务状态")

    def role(request: Request):
        header = request.headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if hmac.compare_digest(token.encode(), s.admin_key.encode()):
            return "admin"
        if hmac.compare_digest(token.encode(), s.relay_key.encode()):
            return "relay"
        if not header:
            accounts.require(request)
            return 'admin'
        raise HTTPException(401, "缺少或无效的 API Key")

    def admin(auth=Depends(role)):
        if auth != "admin":
            raise HTTPException(401, "此接口需要管理员密钥")

    def relay(auth=Depends(role)):
        if auth != "relay":
            raise HTTPException(401, "此接口需要中转密钥")

    @app.get("/api/v1/health")
    def health():
        try:
            with db.read() as c:
                c.execute("SELECT 1").fetchone()
            probe = s.data_dir / "tmp" / uid("health")
            probe.write_bytes(b"ok")
            probe.unlink()
        except (OSError, sqlite3.Error):
            return error_response(503, "数据库或持久化目录不可用")
        return {"status":"ok", "time":now()}

    @app.get("/api/v1/settings", dependencies=[Depends(admin)])
    def settings_view():
        return dict(heartbeat_seconds=s.heartbeat_seconds, offline_seconds=s.offline_seconds,
                    default_image_seconds=20, video_max_seconds=s.video_max_seconds,
                    upload_max_bytes=s.upload_max_bytes, video_max_bytes=s.video_max_bytes)

    async def store_upload(file, render_mode, scope=None):
        name = (file.filename or "").replace("\\", "/").split("/")[-1][:240]
        extension = Path(name).suffix.lower()
        if extension not in (".xlsx", ".xls", ".pdf", ".png", ".jpg", ".jpeg", ".mp4"):
            raise MediaError("只支持 xlsx/xls/pdf/png/jpg/jpeg/mp4", 415)
        kind = "excel" if extension in (".xlsx", ".xls") else "pdf" if extension == ".pdf" else "video" if extension == ".mp4" else "image"
        if scope and scope['category']=='check_video' and kind != 'video':
            raise MediaError('点检视频模块仅支持MP4文件', 422)
        render_mode = render_mode or ('screen_16_9' if kind == 'pdf' else 'print')
        document = kind in ('excel', 'pdf')
        if not document and render_mode != 'print':
            raise MediaError('16:9页面适配仅适用于Excel/PDF文件', 422)
        asset_id = uid("asset")
        target = s.data_dir / "originals" / (asset_id + extension)
        limit = s.video_max_bytes if kind == "video" else s.upload_max_bytes
        size = 0
        try:
            with target.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise MediaError(f"文件超过大小上限 {limit} bytes", 413)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if not size:
                raise MediaError("不允许上传空文件")
            warnings, metadata = [], {}
            if kind == "excel":
                metadata = {'render_mode': render_mode}
                warnings = await run_in_threadpool(excel_warnings, target)
                media_type = "application/vnd.ms-excel" if extension == ".xls" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif kind == 'pdf':
                with target.open('rb') as stream:
                    if stream.read(5) != b'%PDF-':
                        raise MediaError('不是有效的PDF文件', 415)
                metadata = {'render_mode': render_mode}
                warnings = ['请先在WPS中确认PDF分页、图形和文字；系统保留已有页数，每页单独播放，不会将多页自动合并。']
                media_type = 'application/pdf'
            elif kind == "video":
                metadata = await run_in_threadpool(verify_video, target, s)
                media_type = "video/mp4"
            else:
                metadata, media_type = await run_in_threadpool(verify_image, target)
            if scope:
                metadata.update(scope)
            record = await run_in_threadpool(file_record, target, s.data_dir, asset_id,
                                             "original" if document else kind, media_type)
            with db.write() as c:
                if scope:
                    editable_version(c, scope['attach_version_id'])
                c.execute("INSERT INTO assets(id,kind,original_name,status,created_at,warnings,error,metadata) VALUES(?,?,?,?,?,?,NULL,?)",
                          (asset_id, kind, name, "queued" if document else "ready", now(), dumps(warnings), dumps(metadata)))
                insert_file(c, record)
                if document:
                    c.execute("INSERT INTO conversion_jobs(id,asset_id,status,created_at) VALUES(?,?,'queued',?)", (uid("job"), asset_id, now()))
                elif scope:
                    attach_asset(c, require_row(c, 'assets', 'id', asset_id), scope['attach_version_id'])
                result = asset_view(c, require_row(c, "assets", "id", asset_id))
            return result
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.post('/api/v1/assets', dependencies=[Depends(admin)])
    async def upload(file: UploadFile = File(...), render_mode: Literal['print','screen_16_9'] | None = Form(None)):
        return await store_upload(file, render_mode)

    @app.post('/api/v1/versions/{version_id}/modules/{category}/assets', dependencies=[Depends(admin)])
    async def upload_module(version_id: str, category: Category, file: UploadFile=File(...),
                            duration_seconds: float=Form(20, ge=1, le=3600)):
        with db.read() as c:
            version = editable_version(c, version_id)
            playlist_id = version['playlist_id']
        document = Path(file.filename or '').suffix.lower() in ('.xlsx','.xls','.pdf')
        return await store_upload(file, 'screen_16_9' if document else 'print',
            dict(playlist_id=playlist_id, category=category, attach_version_id=version_id,
                 image_duration_seconds=duration_seconds))

    @app.delete('/api/v1/versions/{version_id}/assets/{asset_id}', dependencies=[Depends(admin)])
    def delete_module_file(version_id: str, asset_id: str):
        return remove_file(db, version_id, asset_id)

    @app.get("/api/v1/assets", dependencies=[Depends(admin)])
    def assets():
        with db.read() as c:
            return {"assets":[asset_view(c, a) for a in c.execute("SELECT * FROM assets WHERE status NOT IN ('retiring','expired') ORDER BY created_at DESC,id")]}

    @app.get('/api/v1/assets/{asset_id}/usage', dependencies=[Depends(admin)])
    def usage(asset_id: str):
        with db.read() as c:
            return asset_usage(c, asset_id)

    @app.delete('/api/v1/assets/{asset_id}', dependencies=[Depends(admin)])
    def remove_asset(asset_id: str):
        return delete_asset(db, asset_id)

    @app.get("/api/v1/files/{file_id}", dependencies=[Depends(role)])
    def download(file_id: str):
        with db.read() as c:
            retired=c.execute("SELECT 1 FROM files f JOIN assets a ON a.id=f.asset_id WHERE f.id=? AND a.status IN ('retiring','expired')",(file_id,)).fetchone()
            if retired:raise MediaError('该媒体已按旧版本保留策略过期，请同步最新资料',410,'media_expired')
            row = c.execute("SELECT files.* FROM files JOIN assets ON assets.id=files.asset_id WHERE files.id=? AND assets.status!='deleting'", (file_id,)).fetchone()
            if row is None:
                raise MediaError('媒体文件已删除、正在删除或不存在，请刷新资料列表', 404, 'asset_not_found')
            f = dict(row)
        path = (s.data_dir / f["path"]).resolve()
        if not path.is_relative_to(s.data_dir.resolve()) or not path.is_file():
            raise MediaError("持久化文件缺失，请检查备份和存储", 503)
        return FileResponse(path, media_type=f["media_type"], headers={"ETag":'"' + f["sha256"] + '"'}, filename=path.name,
                            content_disposition_type="inline")

    @app.post("/api/v1/playlists", dependencies=[Depends(admin)])
    def create_playlist(body: PlaylistCreate):
        with db.write() as c:
            playlist_id, version_id = uid("playlist"), uid("ver")
            task_number = next_task_number(c)
            c.execute("INSERT INTO playlists(id,name,task_number,description) VALUES(?,?,?,?)", (playlist_id, body.name, task_number, body.description))
            c.execute("INSERT INTO versions(id,playlist_id,name,version,created_at) VALUES(?,?,?,1,?)", (version_id, playlist_id, body.name, now()))
            result = version_view(require_row(c, 'versions', 'id', version_id))
            result['task_number'] = task_number
            return result

    @app.get("/api/v1/playlists", dependencies=[Depends(admin)])
    def playlists():
        with db.read() as c:
            return {'playlists':[dict(p) for p in c.execute('SELECT * FROM playlists WHERE archived_at IS NULL ORDER BY rowid DESC')],
                    'modules':MODULES, "versions":[version_view(v) for v in c.execute("SELECT v.*,a.archived_at,r.retired_at FROM versions v LEFT JOIN version_archives a ON a.version_id=v.id LEFT JOIN version_retirements r ON r.version_id=v.id ORDER BY v.created_at DESC,v.id")]}

    class RetentionInput(BaseModel):
        model_config=ConfigDict(extra='forbid')
        dry_run: bool=Field(default=True,strict=True)

    @app.get('/api/v1/retention',dependencies=[Depends(admin)])
    def retention_preview():return retention_run(db,True)

    @app.post('/api/v1/retention',dependencies=[Depends(admin)])
    def retention_apply(body: RetentionInput):return retention_run(db,body.dry_run)

    @app.delete('/api/v1/versions/{version_id}', dependencies=[Depends(admin)])
    def delete_version(version_id: str):
        return archive_version(db, version_id)

    @app.delete('/api/v1/playlists/{playlist_id}', dependencies=[Depends(admin)])
    def delete_playlist(playlist_id: str):
        return archive_playlist(db, playlist_id)

    @app.post("/api/v1/versions/{version_id}/clone", dependencies=[Depends(admin)])
    def clone(version_id: str):
        with db.write() as c:
            source = require_active_version(c, version_id)
            number = c.execute("SELECT MAX(version)+1 FROM versions WHERE playlist_id=?", (source["playlist_id"],)).fetchone()[0]
            new_id = uid("ver")
            c.execute("INSERT INTO versions(id,playlist_id,name,version,created_at,items) VALUES(?,?,?,?,?,?)",
                      (new_id, source["playlist_id"], source["name"], number, now(), source["items"]))
            return version_view(require_row(c, "versions", "id", new_id))

    @app.put("/api/v1/versions/{version_id}/items", dependencies=[Depends(admin)])
    def update_items(version_id: str, body: ItemsInput):
        with db.write() as c:
            version = require_active_version(c, version_id)
            if version["status"] != "draft":
                raise MediaError("发布版本不可修改，请创建新版本", 409)
            items = expand_items(c, body.items, version['playlist_id'])
            c.execute("UPDATE versions SET items=? WHERE id=?", (dumps(items), version_id))
            return version_view(require_row(c, "versions", "id", version_id))

    @app.post("/api/v1/versions/{version_id}/publish", dependencies=[Depends(admin)])
    def publish(version_id: str):
        with db.write() as c:
            row = require_active_version(c, version_id)
            if row["status"] == "published":
                return version_view(row)
            expire_tasks(c)
            retention=retention_enforce(c,row['playlist_id'],version_id)
            items = json.loads(row["items"])
            if not items:
                raise MediaError("播放列表不能为空", 409)
            pending_uploads = [a['original_name'] for a in scoped_assets(c, row['playlist_id'])
                               if json.loads(a['metadata']).get('attach_version_id') == version_id and a['status'] != 'ready']
            if pending_uploads:
                raise MediaError('存在转换中、失败或未删除完成的文件，请先处理：'+'、'.join(pending_uploads), 409)
            checked_files = set()
            for item in items:
                if item["file"]["id"] in checked_files:
                    continue
                f = require_row(c, "files", "id", item["file"]["id"])
                asset = require_row(c, "assets", "id", f["asset_id"])
                path = s.data_dir / f["path"]
                if asset["status"] != "ready" or not path.is_file():
                    raise MediaError("媒体未就绪或已丢失，不能发布", 409)
                # Detect storage tampering before making the immutable manifest public.
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != f["sha256"] or path.stat().st_size != f["size_bytes"]:
                    raise MediaError("媒体校验失败，不能发布", 409)
                checked_files.add(f["id"])
            published_at = now()
            manifest = dict(schema_version=2, playlist_version_id=version_id, name=row["name"], version=row["version"],
                            published_at=published_at, loop=True, items=items, modules=manifest_modules(items),
                            display=dict(width=1920,height=1080,aspect_ratio='16:9',fit='contain'))
            text = dumps(manifest)
            target = s.data_dir / "manifests" / (version_id + ".json")
            temporary = target.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            c.execute("UPDATE versions SET status='published',published_at=?,manifest=? WHERE id=?", (published_at, text, version_id))
            retention_retire(c,retention['retire_version_ids'])
            result=version_view(require_row(c, "versions", "id", version_id))
        cleanup_deleting_assets(db)
        return result

    @app.post("/api/v1/devices", dependencies=[Depends(admin)])
    def register(body: DeviceCreate):
        with db.write() as c:
            if c.execute("SELECT 1 FROM devices WHERE device_id=?", (body.device_id,)).fetchone():
                raise MediaError("device_id 已登记", 409)
            c.execute("INSERT INTO devices(device_id,name,created_at) VALUES(?,?,?)", (body.device_id, body.name, now()))
            return dict(require_row(c, "devices", "device_id", body.device_id))

    @app.get("/api/v1/devices", dependencies=[Depends(admin)])
    def devices():
        return device_status(db)

    @app.post("/api/v1/deployments", dependencies=[Depends(admin)])
    def deploy(body: DeploymentCreate):
        return create_deployment(db, body)

    @app.get("/api/v1/deployments", dependencies=[Depends(admin)])
    def tasks():
        with db.write() as c:
            expire_tasks(c)
            return {"tasks":[task_view(c, t) for t in c.execute("SELECT * FROM tasks ORDER BY created_at DESC,id")]}

    @app.post("/api/v1/relay/sync", dependencies=[Depends(relay)])
    def sync(body: RelayInput):
        with db.write() as c:
            touch_relay(c, s, body.relay_id)
            expire_tasks(c)
            assignments = [dict(a) for a in c.execute("SELECT * FROM assignments ORDER BY device_id")]
            devices = [dict(d) for d in c.execute('SELECT * FROM devices ORDER BY device_id')]
            from .service import device_progress
            for device in devices:
                device['download_progress']=device_progress(c,device['device_id'],device['report_seq'])
            ids = sorted({a['playlist_version_id'] for a in assignments} |
                         {d[k] for d in devices for k in ('actual_version_id', 'pending_version_id') if d[k]})
            versions = []
            for i in ids:
                manifest = json.loads(require_row(c, 'versions', 'id', i)['manifest'])
                files = {item['file']['id']: item['file'] for item in manifest['items']}
                for item in manifest['items']:
                    source_file = require_row(c, 'files', 'id', item['file']['id'])
                    for pdf in c.execute("SELECT * FROM files WHERE asset_id=? AND role='pdf'", (source_file['asset_id'],)):
                        from .media import public_file
                        files[pdf['id']] = public_file(pdf)
                versions.append(dict(id=i, manifest=manifest,
                    manifest_sha256=hashlib.sha256(dumps(manifest).encode('utf-8')).hexdigest(), files=list(files.values())))
            tasks = [task_view(c, t) for t in c.execute("SELECT * FROM tasks ORDER BY created_at DESC,id LIMIT 100")]
            history = [dict(h) for h in c.execute('''SELECT tt.device_id,tt.assignment_revision,
                t.playlist_version_id,t.id AS task_id,t.created_at AS assigned_at,s.received_at AS relay_acked_at
                FROM task_targets tt JOIN tasks t ON t.id=tt.task_id LEFT JOIN sync_receipts s
                ON s.device_id=tt.device_id AND s.assignment_revision=tt.assignment_revision
                ORDER BY tt.assignment_revision''')]
            return dict(server_time=now(), heartbeat_seconds=s.heartbeat_seconds, offline_seconds=s.offline_seconds,
                        assignments=assignments, versions=versions, tasks=tasks, devices=devices, assignment_history=history)

    @app.post("/api/v1/relay/acks", dependencies=[Depends(relay)])
    def ack(body: AcksInput):
        with db.write() as c:
            touch_relay(c, s, body.relay_id)
            accepted = 0
            for a in body.assignments:
                current = require_row(c, "assignments", "device_id", a.device_id)
                if a.assignment_revision > current["assignment_revision"]:
                    raise MediaError("不能确认未来安排", 409)
                if a.assignment_revision == current["assignment_revision"]:
                    stamp = now()
                    c.execute("INSERT OR IGNORE INTO sync_receipts VALUES(?,?,?)", (a.device_id, a.assignment_revision, stamp))
                    c.execute("UPDATE assignments SET relay_acked_at=COALESCE(relay_acked_at,?) WHERE device_id=?", (stamp, a.device_id))
                    accepted += 1
            return dict(accepted=accepted)

    @app.post("/api/v1/relay/reports", dependencies=[Depends(relay)])
    def reports(body: ReportsInput):
        return accept_reports(db, body)

    static = Path(__file__).parent / "static"

    @app.get("/")
    def index():
        return HTMLResponse((static / "index.html").read_text(encoding="utf-8").replace("__CENTER_VERSION__", CENTER_VERSION))

    app.mount("/static", StaticFiles(directory=static), name="static")
    app.mount('/auth-ui',StaticFiles(directory=Path(__file__).parents[1]/'shared'/'web'),name='auth-ui')
    return app
