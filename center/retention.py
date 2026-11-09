"""Two usable publications per playlist; immutable history and referenced bytes survive."""
import json

from .db import now, dumps, expire_tasks
from .media import MediaError
from .asset_delete import asset_usage, cleanup_deleting_assets


def plan(c, playlist_id, incoming=None):
    published=list(c.execute('''SELECT v.id,v.version FROM versions v LEFT JOIN version_retirements r ON r.version_id=v.id
        WHERE v.playlist_id=? AND v.status='published' AND r.version_id IS NULL ORDER BY v.version DESC''',(playlist_id,)))
    primary=incoming or (published[0]['id'] if published else None)
    old=[r['id'] for r in published if r['id']!=primary]
    pins={};task_ids=[];device_ids=[]
    for vid in old:
        devices=[dict(r) for r in c.execute('''SELECT device_id,name FROM devices WHERE actual_version_id=? OR pending_version_id=?
            OR device_id IN (SELECT device_id FROM assignments WHERE playlist_version_id=?)''',(vid,vid,vid))]
        tasks=[r[0] for r in c.execute("SELECT id FROM tasks WHERE playlist_version_id=? AND status='running' AND expires_at>?",(vid,now()))]
        if devices or tasks:
            pins[vid]=dict(devices=devices,task_ids=tasks)
            device_ids.extend(d['device_id'] for d in devices);task_ids.extend(tasks)
    keep=[primary] if primary else []
    keep.extend(list(pins) or old[:1])
    return dict(playlist_id=playlist_id,keep_version_ids=keep,retire_version_ids=[vid for vid in old if vid not in keep],
                blocked=len(pins)>1,device_ids=sorted(set(device_ids)),task_ids=sorted(set(task_ids)),version_ids=list(pins),references=pins)


def enforce(c, playlist_id, incoming):
    result=plan(c,playlist_id,incoming)
    if result['blocked']:
        names=sorted({d['name']+'（'+d['device_id']+'）' for ref in result['references'].values() for d in ref['devices']})
        raise MediaError('无法发布：最多保留新版和一个旧版，但当前有多个不同旧版本仍被引用。请先改派到目前已发布版本并等待屏幕实际确认。屏幕：'
                         +'、'.join(names)+'；执行任务：'+'、'.join(result['task_ids']),409,'version_retention_blocked',result)
    return result


def retire(c, version_ids):
    candidates=set()
    for vid in version_ids:
        row=c.execute('SELECT items FROM versions WHERE id=?',(vid,)).fetchone()
        for item in json.loads(row['items']):
            file=c.execute('SELECT asset_id FROM files WHERE id=?',(item['file']['id'],)).fetchone()
            if file:candidates.add(file['asset_id'])
        c.execute('INSERT OR IGNORE INTO version_retirements VALUES(?,?)',(vid,now()))
        c.execute('INSERT OR IGNORE INTO version_archives VALUES(?,?)',(vid,now()))
    for aid in candidates:
        asset=c.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone()
        if asset['status']=='ready' and asset_usage(c,aid)['can_delete']:
            metadata=json.loads(asset['metadata']);metadata['retired_requested_at']=now()
            c.execute("UPDATE assets SET status='retiring',metadata=? WHERE id=?",(dumps(metadata),aid))


def run(db, dry_run=True):
    with db.write() as c:
        expire_tasks(c)
        plans=[]
        for row in c.execute('SELECT id,name FROM playlists').fetchall():
            result=plan(c,row['id']);result['name']=row['name'];plans.append(result)
            if not dry_run and not result['blocked']:retire(c,result['retire_version_ids'])
    if not dry_run:cleanup_deleting_assets(db)
    with db.read() as c:
        pending=[dict(r) for r in c.execute("SELECT id,error FROM assets WHERE status='retiring'")]
    return dict(published_version_limit=2,dry_run=dry_run,playlists=plans,cleanup_pending=pending)
