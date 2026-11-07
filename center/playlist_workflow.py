import json

from .asset_delete import asset_usage, cleanup_asset
from .db import dumps, now
from .media import MediaError
from .models import ItemInput
from .service import require_row, require_active_playlist, require_active_version, expand_items


def editable_version(c, version_id):
    version = require_active_version(c, version_id)
    if version['status'] != 'draft':
        raise MediaError('已发布版本不能修改，请先复制为新版本', 409)
    return version


def attach_asset(c, asset, version_id):
    version = editable_version(c, version_id)
    metadata = json.loads(asset['metadata'])
    if metadata.get('playlist_id') != version['playlist_id']:
        raise MediaError('文件归属不匹配', 409)
    items = json.loads(version['items'])
    existing = {item['file']['id'] for item in items}
    added = expand_items(c, [ItemInput(asset_id=asset['id'], category=metadata['category'],
                                      duration_seconds=None if asset['kind']=='video' else metadata['image_duration_seconds'])],
                         version['playlist_id'])
    items.extend(item for item in added if item['file']['id'] not in existing)
    if len(items) > 2000:
        raise MediaError('任务单最多2000个播放项', 422)
    for i, item in enumerate(items): item['position'] = i
    c.execute('UPDATE versions SET items=? WHERE id=?', (dumps(items), version_id))


def scoped_assets(c, playlist_id):
    return [a for a in c.execute('SELECT * FROM assets')
            if json.loads(a['metadata']).get('playlist_id') == playlist_id]


def archive_version(db, version_id):
    candidates = set()
    with db.write() as c:
        version = require_row(c, 'versions', 'id', version_id)
        retained = version['status']=='published'
        if c.execute('SELECT 1 FROM version_archives WHERE version_id=?', (version_id,)).fetchone():
            pending = [a['id'] for a in scoped_assets(c, version['playlist_id'])
                       if json.loads(a['metadata']).get('attach_version_id')==version_id and a['status']=='deleting']
            return dict(version_id=version_id, archived=True, retained_history=retained, cleanup_pending=pending)
        require_active_playlist(c, version['playlist_id'])
        devices = [d['device_id'] for d in c.execute('SELECT * FROM devices')
                   if d['actual_version_id']==version_id or d['pending_version_id']==version_id]
        devices += [a[0] for a in c.execute('SELECT device_id FROM assignments WHERE playlist_version_id=?', (version_id,))]
        tasks = [t[0] for t in c.execute("SELECT id FROM tasks WHERE playlist_version_id=? AND status='running'", (version_id,))]
        if devices or tasks:
            raise MediaError('无法删除：版本仍被屏幕或执行中派发引用，请先改派并等待屏幕确认。\n屏幕：'
                             +'、'.join(sorted(set(devices)))+'\n派发：'+'、'.join(tasks), 409, 'version_in_use',
                             {'device_ids':sorted(set(devices)), 'task_ids':tasks})
        count = c.execute('SELECT COUNT(*) FROM versions v LEFT JOIN version_archives a ON a.version_id=v.id WHERE v.playlist_id=? AND a.version_id IS NULL', (version['playlist_id'],)).fetchone()[0]
        if count<=1:
            raise MediaError('每份资料至少保留一个版本；如需全部删除，请删除整个资料条目', 409, 'last_version')
        owned = [a for a in scoped_assets(c, version['playlist_id'])
                 if json.loads(a['metadata']).get('attach_version_id')==version_id]
        if any(a['status'] in ('queued','processing') for a in owned):
            raise MediaError('本版本仍有文件转换中，请完成后再删除',409,'version_processing')
        c.execute('INSERT INTO version_archives VALUES(?,?)',(version_id,now()))
        if not retained:
            ids = {i['file']['id'] for i in json.loads(version['items'])}
            candidates = {a['id'] for a in owned}
            candidates.update(f['asset_id'] for f in c.execute('SELECT * FROM files') if f['id'] in ids)
            c.execute("UPDATE versions SET items='[]' WHERE id=?", (version_id,))
            for asset_id in list(candidates):
                if not asset_usage(c,asset_id)['can_delete']:
                    candidates.remove(asset_id);continue
                asset = require_row(c,'assets','id',asset_id)
                metadata=json.loads(asset['metadata']);metadata['delete_requested_at']=now()
                c.execute("UPDATE assets SET status='deleting',metadata=? WHERE id=?",(dumps(metadata),asset_id))
    pending=[]
    for asset_id in sorted(candidates):
        try:cleanup_asset(db,asset_id)
        except MediaError:pending.append(asset_id)
    return dict(version_id=version_id, archived=True, retained_history=retained, cleanup_pending=pending)


def remove_file(db, version_id, asset_id):
    with db.write() as c:
        version = editable_version(c, version_id)
        asset = require_row(c, 'assets', 'id', asset_id)
        metadata = json.loads(asset['metadata'])
        ids = {f[0] for f in c.execute('SELECT id FROM files WHERE asset_id=?', (asset_id,))}
        items = json.loads(version['items'])
        if metadata.get('playlist_id') not in (None, version['playlist_id']):
            raise MediaError('文件属于其他任务单', 409)
        if not metadata.get('playlist_id') and not any(i['file']['id'] in ids for i in items):
            raise MediaError('该文件不属于当前草稿', 409)
        job = c.execute('SELECT status FROM conversion_jobs WHERE asset_id=?', (asset_id,)).fetchone()
        if asset['status'] in ('queued','processing') or (job and job['status'] in ('queued','running')):
            raise MediaError('文件仍在转换中，请完成后再删除', 409)
        category = next((i.get('category','custom') for i in items if i['file']['id'] in ids), metadata.get('category','custom'))
        items = [i for i in items if i['file']['id'] not in ids]
        for i, item in enumerate(items): item['position'] = i
        c.execute('UPDATE versions SET items=? WHERE id=?', (dumps(items), version_id))
        usage = asset_usage(c, asset_id)
        retained = bool(usage['references'])
        if not retained:
            metadata['delete_requested_at'] = now()
            metadata.update(playlist_id=version['playlist_id'], attach_version_id=version_id, category=category)
            c.execute("UPDATE assets SET status='deleting',metadata=? WHERE id=?", (dumps(metadata), asset_id))
    if not retained: cleanup_asset(db, asset_id)
    return dict(asset_id=asset_id, removed=True, retained=retained,
                message='已从当前草稿移除；其他版本仍引用，保留历史文件。' if retained else '文件及其全部分页已删除。',
                references=usage['references'])


def archive_playlist(db, playlist_id):
    candidates = set()
    with db.write() as c:
        playlist = require_row(c, 'playlists', 'id', playlist_id)
        if playlist['archived_at']:
            retained = bool(c.execute("SELECT 1 FROM versions WHERE playlist_id=? AND status='published'", (playlist_id,)).fetchone())
            pending = [a['id'] for a in scoped_assets(c, playlist_id) if a['status']=='deleting']
            return dict(playlist_id=playlist_id, archived=True, retained_history=retained, cleanup_pending=pending)
        versions = c.execute('SELECT * FROM versions WHERE playlist_id=?', (playlist_id,)).fetchall()
        vids = {v['id'] for v in versions}
        devices = [d['device_id'] for d in c.execute('SELECT * FROM devices')
                   if d['actual_version_id'] in vids or d['pending_version_id'] in vids]
        devices += [a['device_id'] for a in c.execute('SELECT * FROM assignments') if a['playlist_version_id'] in vids]
        running = [t['id'] for t in c.execute("SELECT * FROM tasks WHERE status='running'") if t['playlist_version_id'] in vids]
        owned = scoped_assets(c, playlist_id)
        if devices or running:
            raise MediaError('无法删除：该任务单仍关联屏幕或执行中任务，请先改派并等待屏幕确认。\n屏幕：'
                             + '、'.join(sorted(set(devices))) + '\n任务：' + '、'.join(running), 409,
                             'playlist_in_use', {'device_ids':sorted(set(devices)), 'task_ids':running})
        if any(a['status'] in ('queued','processing') for a in owned):
            raise MediaError('任务单内仍有文件转换中，请稍后再删除', 409)
        retained_history = any(v['status']=='published' for v in versions)
        c.execute('UPDATE playlists SET archived_at=? WHERE id=?', (now(), playlist_id))
        if not retained_history:
            files = {i['file']['id'] for v in versions for i in json.loads(v['items'])}
            candidates = {a['id'] for a in owned}
            candidates.update(f['asset_id'] for f in c.execute('SELECT * FROM files') if f['id'] in files)
            c.execute("UPDATE versions SET items='[]' WHERE playlist_id=? AND status='draft'", (playlist_id,))
            for asset_id in list(candidates):
                usage = asset_usage(c, asset_id)
                if not usage['can_delete']:
                    candidates.remove(asset_id)
                    continue
                asset = require_row(c, 'assets', 'id', asset_id)
                metadata = json.loads(asset['metadata']); metadata['delete_requested_at'] = now()
                c.execute("UPDATE assets SET status='deleting',metadata=? WHERE id=?", (dumps(metadata), asset_id))
    pending = []
    for asset_id in sorted(candidates):
        try: cleanup_asset(db, asset_id)
        except MediaError: pending.append(asset_id)
    return dict(playlist_id=playlist_id, archived=True, retained_history=retained_history, cleanup_pending=pending)
