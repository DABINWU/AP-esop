"""Reference-protected deletion with a durable non-playable state before disk cleanup."""
import json
import logging
import re
import shutil
from pathlib import Path

from .db import dumps, now
from .media import MediaError

log = logging.getLogger('esop.assets')


def asset_usage(c, asset_id):
    asset = c.execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
    if asset is None:
        raise MediaError('媒体资料已删除或不存在，请刷新资料列表', 404, 'asset_not_found')
    file_ids = {r[0] for r in c.execute('SELECT id FROM files WHERE asset_id=?', (asset_id,))}
    references = []
    for version in c.execute('SELECT v.* FROM versions v LEFT JOIN version_retirements r ON r.version_id=v.id WHERE r.version_id IS NULL ORDER BY v.name,v.version,v.id'):
        items = json.loads(version['items'])
        manifest = json.loads(version['manifest']) if version['manifest'] else {}
        if not any(item.get('file', {}).get('id') in file_ids for item in items + manifest.get('items', [])):
            continue
        vid = version['id']
        references.append(dict(playlist_version_id=vid, name=version['name'], version=version['version'],
            status=version['status'],
            desired_device_ids=[r[0] for r in c.execute('SELECT device_id FROM assignments WHERE playlist_version_id=? ORDER BY device_id', (vid,))],
            actual_device_ids=[r[0] for r in c.execute('SELECT device_id FROM devices WHERE actual_version_id=? ORDER BY device_id', (vid,))],
            pending_device_ids=[r[0] for r in c.execute('SELECT device_id FROM devices WHERE pending_version_id=? ORDER BY device_id', (vid,))],
            task_count=c.execute('SELECT COUNT(*) FROM tasks WHERE playlist_version_id=?', (vid,)).fetchone()[0]))
    blockers = []
    if asset['status'] in ('retiring','expired'):
        blockers.append('过期媒体的描述记录用于历史追溯，不能手动删除记录。')
    job = c.execute('SELECT status FROM conversion_jobs WHERE asset_id=?', (asset_id,)).fetchone()
    if asset['status'] in ('queued', 'processing') or (job and job['status'] in ('queued', 'running')):
        blockers.append('资料仍在排队或转换中，请等待完成后再删除。')
    if references:
        blockers.append('被以下播放列表引用，请先从草稿移除并保存；已发布及历史版本的资料不能删除。')
        for ref in references:
            status = '已发布' if ref['status'] == 'published' else '草稿'
            text = f"{ref['name']} · v{ref['version']}（{status}）"
            screens = sorted(set(ref['desired_device_ids'] + ref['actual_device_ids'] + ref['pending_device_ids']))
            if screens:
                text += '；相关屏幕：' + '、'.join(screens)
            if ref['task_count']:
                text += f"；下发任务记录 {ref['task_count']} 条"
            blockers.append(text)
    return dict(asset_id=asset_id, can_delete=not blockers, blockers=blockers, references=references)


def cleanup_asset(db, asset_id):
    """May retry after partial unlink/crash; never restore the asset to playable state."""
    try:
        with db.write() as c:
            asset = c.execute('SELECT status FROM assets WHERE id=?', (asset_id,)).fetchone()
            if asset is None:
                return
            if asset['status']=='expired':
                return  # A concurrent worker/web cleanup already preserved the tombstone.
            if asset['status'] not in ('deleting','retiring'):
                raise RuntimeError('Asset must be marked deleting before cleanup')
            ids={r[0] for r in c.execute('SELECT id FROM files WHERE asset_id=?',(asset_id,))}
            preserve_records=asset['status']=='retiring' or any(
                any(i['file']['id'] in ids for i in json.loads(v[0]))
                for v in c.execute('SELECT v.items FROM versions v JOIN version_retirements r ON r.version_id=v.id'))
            root = db.settings.data_dir.resolve()
            if not re.fullmatch(r'asset_[0-9a-f]{32}', asset_id):
                raise OSError('Invalid storage identifier')
            paths = []
            for row in c.execute('SELECT path FROM files WHERE asset_id=?', (asset_id,)):
                relative = Path(row['path'])
                parts = relative.parts
                valid = ((len(parts) == 2 and parts[0] == 'originals' and Path(parts[1]).stem == asset_id)
                         or (len(parts) == 3 and parts[0] == 'derived' and parts[1] == asset_id))
                path = root / relative
                if (not valid or relative.is_absolute() or path.is_symlink()
                        or path.resolve() != path or not path.resolve().is_relative_to(root)):
                    raise OSError('Unsafe media storage path')
                paths.append(path)
            derived = root / 'derived' / asset_id
            # Validate the absolute recursive target before removing any bytes.
            if derived.is_symlink() or derived.resolve() != derived or derived.parent != root / 'derived':
                raise OSError('Unsafe derived storage path')
            trees = [derived]
            job = c.execute('SELECT id FROM conversion_jobs WHERE asset_id=?', (asset_id,)).fetchone()
            if job:
                if not re.fullmatch(r'job_[0-9a-f]{32}', job['id']):
                    raise OSError('Invalid conversion storage identifier')
                temporary = root / 'tmp' / job['id']
                if temporary.is_symlink() or temporary.resolve() != temporary or temporary.parent != root / 'tmp':
                    raise OSError('Unsafe conversion storage path')
                trees.append(temporary)
            for path in paths:
                path.unlink(missing_ok=True)
            for tree in trees:
                if tree.exists():
                    shutil.rmtree(tree)
            if preserve_records:
                c.execute("UPDATE assets SET status='expired',error=NULL WHERE id=?",(asset_id,))
            else:
                c.execute('DELETE FROM conversion_jobs WHERE asset_id=?', (asset_id,))
                c.execute('DELETE FROM files WHERE asset_id=?', (asset_id,))
                c.execute('DELETE FROM assets WHERE id=?', (asset_id,))
    except OSError as exc:
        message = '文件删除未完成，请检查数据目录权限或文件占用后重试；系统也会自动重试。'
        with db.write() as c:
            c.execute("UPDATE assets SET error=? WHERE id=? AND status IN ('deleting','retiring')", (message, asset_id))
        log.warning('media cleanup pending asset=%s error_type=%s', asset_id, type(exc).__name__)
        raise MediaError(message, 503, 'asset_delete_failed', {'asset_id': asset_id, 'status': 'deleting'}) from exc


def delete_asset(db, asset_id):
    with db.write() as c:
        usage = asset_usage(c, asset_id)
        if not usage['can_delete']:
            code = 'asset_in_use' if usage['references'] else 'asset_processing'
            raise MediaError('无法删除：\n' + '\n'.join(usage['blockers']), 409, code, usage)
        row = c.execute('SELECT metadata FROM assets WHERE id=?', (asset_id,)).fetchone()
        metadata = json.loads(row['metadata'])
        metadata.setdefault('delete_requested_at', now())
        c.execute("UPDATE assets SET status='deleting',metadata=?,error=NULL WHERE id=?", (dumps(metadata), asset_id))
    cleanup_asset(db, asset_id)
    return {'asset_id': asset_id, 'deleted': True}


def cleanup_deleting_assets(db):
    with db.read() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM assets WHERE status IN ('deleting','retiring')")]
    for asset_id in ids:
        try:
            cleanup_asset(db, asset_id)
        except MediaError:
            pass  # Error is persisted for the administrator; retry after the next interval.
