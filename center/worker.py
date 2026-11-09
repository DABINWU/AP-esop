import json
import logging
import os
import re
import shutil
import time
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path

from .config import Settings
from .db import Database, now, dumps, expire_tasks
from .media import MediaError, run_command, file_record, insert_file, verify_image
from .wps_images import load_cell_images
from .drawing_compat import load_ignored_drawings
from .screen_render import fit_screen_page
from .asset_delete import cleanup_deleting_assets
from .retention import run as apply_retention
from .playlist_workflow import attach_asset

log = logging.getLogger("esop.worker")


@contextmanager
def worker_lock(root):
    """OS lock is released after crashes; excludes two workers even across containers."""
    with (root / "worker.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def recover_jobs(db):
    with db.write() as c:
        jobs = c.execute("SELECT * FROM conversion_jobs WHERE status='running'").fetchall()
        for job in jobs:
            retry = job["attempts"] < 2
            state = "queued" if retry else "failed"
            error = "转换被进程重启中断，已自动回队一次" if retry else "转换再次被中断，恢复次数已用完"
            c.execute("UPDATE conversion_jobs SET status=?,error=?,finished_at=? WHERE id=?",
                      (state, error, None if retry else now(), job["id"]))
            c.execute("UPDATE assets SET status=?,error=? WHERE id=?", (state, error, job["asset_id"]))
        expire_tasks(c)


def checked_directory(root, category, identifier):
    if not re.fullmatch(r"(?:job|asset)_[0-9a-f]{32}", identifier):
        raise RuntimeError("Invalid internal storage identifier")
    base = (root / category).resolve()
    path = (base / identifier).resolve()
    if not base.is_relative_to(root.resolve()) or path == base or not path.is_relative_to(base):
        raise RuntimeError("Storage path escapes its intended directory")
    return path


def convert_excel(s, source, pdf, work, render_mode):
    wps_images = load_cell_images(source)
    ignored = load_ignored_drawings(source)
    checked_images = set()
    for image in wps_images:
        if image.package_path not in checked_images:
            verify_image(BytesIO(image.data))
            checked_images.add(image.package_path)
    result = run_command([s.uno_python, Path(__file__).with_name('lo_convert.py'), source, pdf,
                          work / 'lo-profile', s.soffice, '--render-mode', render_mode],
                         s.conversion_timeout_seconds)
    compatibility = json.loads(result.decode('utf-8'))
    if compatibility.get('wps_cell_images_restored') != len(wps_images):
        raise RuntimeError('WPS图片还原数量不完整，不能发布')
    if compatibility.get('ignored_degenerate_shapes') != len(ignored):
        raise RuntimeError('零尺寸残留图形处理数量不符，不能发布')
    return compatibility


def process_one(db):
    s = db.settings
    with db.write() as c:
        expire_tasks(c)
        job = c.execute("SELECT * FROM conversion_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if job is None:
            return False
        c.execute("UPDATE conversion_jobs SET status='running',attempts=attempts+1,started_at=?,error=NULL WHERE id=?", (now(), job["id"]))
        c.execute("UPDATE assets SET status='processing',error=NULL WHERE id=?", (job["asset_id"],))
        original = c.execute("SELECT * FROM files WHERE asset_id=? AND role='original'", (job["asset_id"],)).fetchone()
        asset = c.execute("SELECT original_name,metadata,kind FROM assets WHERE id=?", (job["asset_id"],)).fetchone()
        original_name = asset['original_name']
        kind = asset['kind']
        metadata = json.loads(asset['metadata'])
    work = checked_directory(s.data_dir, "tmp", job["id"])
    destination = checked_directory(s.data_dir, "derived", job["asset_id"])
    # Both paths are generated DB IDs within DATA_DIR, never user-supplied names.
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(destination, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        render_mode = metadata.get('render_mode', 'print')
        if render_mode not in ('print', 'screen_16_9'):
            raise RuntimeError('未知文档渲染模式')
        screen = render_mode == 'screen_16_9'
        # &F header/footer fields should show the uploaded name, not our internal asset UUID.
        # Use a bounded, safe basename in the isolated work directory; retain original bytes.
        safe_name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", original_name).rstrip(". ")
        extension = Path(original["path"]).suffix
        stem = Path(safe_name).stem or "document"
        while len((stem + extension).encode("utf-8")) > 240:
            stem = stem[:-1]
        source_dir = work / "source"
        source_dir.mkdir()
        source = (source_dir / (stem + extension)).resolve()
        if not source.is_relative_to(source_dir.resolve()):
            raise RuntimeError("Invalid conversion source filename")
        shutil.copyfile(s.data_dir / original["path"], source)
        pdf = work / "document.pdf"
        if kind == 'pdf':
            shutil.copyfile(source, pdf)
            compatibility = {}
        elif kind == 'excel':
            compatibility = convert_excel(s, source, pdf, work, render_mode)
        else:
            raise RuntimeError('不支持的文档类型')
        if not pdf.is_file() or not pdf.stat().st_size:
            raise RuntimeError("未生成有效PDF")
        try:
            info = run_command([s.pdfinfo, pdf], 20).decode("utf-8", errors="replace")
        except MediaError as e:
            if kind == 'pdf' and re.search('password|encrypt', str(e), re.IGNORECASE):
                raise RuntimeError('不支持加密PDF，请在WPS中导出不带密码的PDF后上传') from e
            raise
        if re.search(r'^Encrypted:\s+yes', info, re.MULTILINE | re.IGNORECASE):
            raise RuntimeError('不支持加密PDF，请导出不带密码的PDF后上传')
        match = re.search(r"^Pages:\s+(\d+)", info, re.MULTILINE)
        pages = int(match.group(1)) if match else 0
        if pages < 1 or pages > s.max_pages:
            raise RuntimeError(f"分页异常：{pages}页；允许1–{s.max_pages}页，请检查原文件分页")
        if screen and kind == 'excel':
            visible = compatibility.get('visible_sheet_pages')
            if (pages != compatibility.get('sheet_count') or not isinstance(visible, list)
                    or not visible or visible != sorted(set(visible))
                    or any(type(p) is not int or not 1 <= p <= pages for p in visible)):
                raise RuntimeError('整表单页导出页数与工作表数量不符，不能发布')
            if len(visible) != pages:
                # LO whole-sheet mode exports hidden sheets and ignores PageRange.
                # Filter vector PDF pages before any asset files become downloadable.
                split = work / 'sheet-pdfs'
                split.mkdir()
                run_command([Path(s.pdfinfo).with_name('pdfseparate'), pdf, split / 'sheet-%d.pdf'],
                            s.conversion_timeout_seconds)
                filtered = work / 'visible.pdf'
                run_command([Path(s.pdfinfo).with_name('pdfunite'),
                             *(split / f'sheet-{p}.pdf' for p in visible), filtered], s.conversion_timeout_seconds)
                filtered.replace(pdf)
                check = run_command([s.pdfinfo, pdf], 20).decode('utf-8', errors='replace')
                count = re.search(r'^Pages:\s+(\d+)', check, re.MULTILINE)
                if not count or int(count.group(1)) != len(visible):
                    raise RuntimeError('隐藏工作表过滤失败，不能发布')
                pages = len(visible)
        render = work / "render"
        render.mkdir()
        # Bound raster memory even if a very long sheet creates a huge PDF page.
        raster_options = ['-scale-to', 3840] if screen else ['-r', s.render_dpi]
        run_command([s.pdftoppm, "-png", *raster_options, "-f", "1", "-l", pages,
                     pdf, render / "page"], s.conversion_timeout_seconds)
        images = sorted(render.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        if len(images) != pages:
            raise RuntimeError("PDF分页图片数量不完整")
        destination.mkdir()
        shutil.move(str(pdf), destination / "document.pdf")
        records = [file_record(destination / "document.pdf", s.data_dir, job["asset_id"], "pdf", "application/pdf")]
        for number, image in enumerate(images, 1):
            if screen:
                fit_screen_page(image)
            target = destination / f"page-{number:04}.png"
            shutil.move(str(image), target)
            records.append(file_record(target, s.data_dir, job["asset_id"], "page", "image/png", number))
        with db.write() as c:
            asset = c.execute("SELECT * FROM assets WHERE id=?", (job["asset_id"],)).fetchone()
            warnings = json.loads(asset["warnings"])
            if pages > s.page_warning_count:
                warnings.append(f"页数较多（{pages}页），请检查是否存在空白/意外分页。")
            if screen:
                warnings.append('已按PDF已有页序逐页适配1920×1080，不合并页面；请检查文字清晰度。' if kind == 'pdf' else
                                '已按每张可见工作表一页适配1920×1080，忽略原打印区域；整表文字可能缩小，请预览检查。')
            elif pages == 1 and kind == 'excel':
                warnings.append("仅生成1页，请确认长表没有被原文件缩放过小或遗漏打印区域外内容。")
            restored = compatibility.get('wps_cell_images_restored', 0)
            ignored_count = compatibility.get('ignored_degenerate_shapes', 0)
            if ignored_count:
                warnings.append(f'渲染时已忽略 {ignored_count} 个零尺寸填充残留图形及相关孤立连接线；原文件未修改，请核对预览。')
            if restored:
                warnings.append(f'已兼容还原 {restored} 张WPS单元格图片，请逐页核对图片位置与完整性。')
            for record in records:
                insert_file(c, record)
            result_metadata = {**metadata, 'render_mode': render_mode,
                               'output_width': 1920 if screen else None,
                               'output_height': 1080 if screen else None,
                               'page_count': pages, 'render_dpi': None if screen else s.render_dpi}
            if kind == 'excel':
                result_metadata['wps_cell_images_restored'] = restored
                result_metadata['ignored_degenerate_shapes'] = ignored_count
            c.execute("UPDATE assets SET status='ready',warnings=?,metadata=?,error=NULL WHERE id=?",
                      (dumps(warnings), dumps(result_metadata), job["asset_id"]))
            if metadata.get('attach_version_id'):
                attach_asset(c, c.execute('SELECT * FROM assets WHERE id=?', (job['asset_id'],)).fetchone(), metadata['attach_version_id'])
            c.execute("UPDATE conversion_jobs SET status='succeeded',finished_at=?,error=NULL WHERE id=?", (now(), job["id"]))
        log.info("conversion succeeded job=%s pages=%s", job["id"], pages)
    except Exception as e:
        error = str(e)[:2000]
        shutil.rmtree(destination, ignore_errors=True)
        with db.write() as c:
            c.execute("UPDATE assets SET status='failed',error=? WHERE id=?", (error, job["asset_id"]))
            c.execute("UPDATE conversion_jobs SET status='failed',finished_at=?,error=? WHERE id=?", (now(), error, job["id"]))
        log.warning("conversion failed job=%s", job["id"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db = Database(Settings.from_env())
    db.initialize()
    with worker_lock(db.settings.data_dir):
        recover_jobs(db)
        next_cleanup = 0
        while True:
            (db.settings.data_dir / "worker-heartbeat").touch()
            if time.monotonic() >= next_cleanup:
                apply_retention(db,False)
                next_cleanup = time.monotonic() + 30
            if not process_one(db):
                time.sleep(1)


if __name__ == "__main__":
    main()
