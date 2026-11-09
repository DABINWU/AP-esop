import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from center.asset_delete import cleanup_deleting_assets, cleanup_asset
from center.db import Database, now
from test_center import env, image_asset, published, register, deploy, timeout, report, send


def draft(env, source, asset=None):
    c,db,admin,_=env
    r=c.post('/api/v1/versions/'+source['id']+'/clone',headers=admin);assert r.status_code==200,r.text
    v=r.json()
    if asset:
        r=c.put('/api/v1/versions/'+v['id']+'/items',headers=admin,json={'items':[{'asset_id':asset['id']}]});assert r.status_code==200,r.text
    return v


def publish(env, v):
    return env[0].post('/api/v1/versions/'+v['id']+'/publish',headers=env[2])


def versions(env):
    return env[0].get('/api/v1/playlists',headers=env[2]).json()['versions']


def test_two_publications_release_exclusive_bytes_keep_history_and_descriptors(env):
    c,db,admin,_=env
    a=image_asset(env);v1=published(env,a)
    manifest=db.settings.data_dir/'manifests'/(v1['id']+'.json');original=manifest.read_bytes()
    v2=draft(env,v1,image_asset(env));assert publish(env,v2).status_code==200
    v3=draft(env,v2,image_asset(env));assert publish(env,v3).status_code==200
    rows=versions(env);assert len([v for v in rows if v['status']=='published' and not v['retired_at']])==2
    assert next(v for v in rows if v['id']==v1['id'])['retired_at']
    assert manifest.read_bytes()==original
    assert c.get(a['files'][0]['download_path'],headers=admin).status_code==410
    assert c.get(a['files'][0]['download_path']).status_code==401
    assert c.post('/api/v1/versions/'+v1['id']+'/clone',headers=admin).status_code==410
    assert deploy(env,v1).status_code==410
    with db.read() as conn:
        row=conn.execute('SELECT * FROM files WHERE asset_id=?',(a['id'],)).fetchone()
        assert row and not (db.settings.data_dir/row['path']).exists()
        assert conn.execute('SELECT status FROM assets WHERE id=?',(a['id'],)).fetchone()[0]=='expired'
    cleanup_asset(db,a['id'])
    assert publish(env,v3).status_code==200


def test_one_offline_old_target_is_retained_instead_of_unused_recent_version(env):
    c,db,admin,_=env
    a=image_asset(env);v1=published(env,a);register(env)
    t=deploy(env,v1).json();timeout(db,t['id'])
    v2=draft(env,v1,image_asset(env));assert publish(env,v2).status_code==200
    v3=draft(env,v2,image_asset(env));assert publish(env,v3).status_code==200
    rows={v['id']:v for v in versions(env)}
    assert not rows[v1['id']]['retired_at'] and rows[v2['id']]['retired_at']
    assert c.get(a['files'][0]['download_path'],headers=admin).status_code==200
    assert all(d['desired']['playlist_version_id']==v1['id'] for d in c.get('/api/v1/devices',headers=admin).json()['devices'])


def test_two_distinct_in_use_old_versions_block_atomically_until_actual_confirmation(env):
    c,db,admin,_=env
    v1=published(env);register(env,('screen-a',))
    t1=deploy(env,v1,ids=('screen-a',)).json()
    assert send(env,[report(v1,t1['targets'][0])]).status_code==200
    v2=draft(env,v1,image_asset(env));assert publish(env,v2).status_code==200
    t2=deploy(env,v2,'newer-task',ids=('screen-a',)).json()
    v3=draft(env,v2,image_asset(env));blocked=publish(env,v3)
    assert blocked.status_code==409 and blocked.json()['error']['code']=='version_retention_blocked'
    assert blocked.json()['error']['details']['device_ids']==['screen-a']
    assert not (db.settings.data_dir/'manifests'/(v3['id']+'.json')).exists()
    assert not any(v['retired_at'] for v in versions(env))
    v2=next(v for v in versions(env) if v['id']==v2['id'])
    assert send(env,[report(v2,t2['targets'][0],2)]).status_code==200
    assert publish(env,v3).status_code==200


def test_shared_media_and_draft_refs_survive_then_become_history_tombstones(env):
    c,db,admin,_=env
    a=image_asset(env);v1=published(env,a);held=draft(env,v1)
    v2=draft(env,v1,image_asset(env));assert publish(env,v2).status_code==200
    v3=draft(env,v2,image_asset(env));assert publish(env,v3).status_code==200
    assert c.get(a['files'][0]['download_path'],headers=admin).status_code==200
    assert c.delete('/api/v1/versions/'+held['id'],headers=admin).status_code==200
    assert c.get(a['files'][0]['download_path'],headers=admin).status_code==410
    with db.read() as conn:assert conn.execute('SELECT 1 FROM files WHERE asset_id=?',(a['id'],)).fetchone()


def test_shared_file_in_retained_versions_not_duplicated_or_removed(env):
    c,db,admin,_=env
    a=image_asset(env);v1=published(env,a)
    v2=draft(env,v1);assert publish(env,v2).status_code==200
    v3=draft(env,v2);assert publish(env,v3).status_code==200
    assert c.get(a['files'][0]['download_path'],headers=admin).status_code==200
    assert len(c.get('/api/v1/assets',headers=admin).json()['assets'])==1


def test_retiring_cleanup_resumes_after_storage_error_and_database_restart(env,monkeypatch):
    c,db,admin,_=env
    a=image_asset(env);v1=published(env,a)
    with db.read() as conn:target=db.settings.data_dir/conn.execute('SELECT path FROM files WHERE asset_id=?',(a['id'],)).fetchone()[0]
    v2=draft(env,v1,image_asset(env));assert publish(env,v2).status_code==200
    v3=draft(env,v2,image_asset(env));unlink=Path.unlink
    def fail(self,*args,**kwargs):
        if self==target:raise PermissionError('test storage occupied')
        return unlink(self,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail)
    assert publish(env,v3).status_code==200
    with db.read() as conn:assert conn.execute('SELECT status FROM assets WHERE id=?',(a['id'],)).fetchone()[0]=='retiring'
    monkeypatch.undo();fresh=Database(db.settings);fresh.initialize();cleanup_deleting_assets(fresh)
    assert not target.exists()
    with fresh.read() as conn:assert conn.execute('SELECT status FROM assets WHERE id=?',(a['id'],)).fetchone()[0]=='expired'


def test_late_report_of_expired_version_keeps_history_and_does_not_break_relay_sync(env):
    c,db,admin,relay=env
    v1=published(env);register(env,('screen-a',));t1=deploy(env,v1,ids=('screen-a',)).json();timeout(db,t1['id'])
    v2=draft(env,v1,image_asset(env));assert publish(env,v2).status_code==200
    t2=deploy(env,v2,'second-target',ids=('screen-a',)).json();timeout(db,t2['id'])
    v3=draft(env,v2,image_asset(env));assert publish(env,v3).status_code==200
    assert send(env,[report(v1,t1['targets'][0])]).status_code==200
    sync=c.post('/api/v1/relay/sync',headers=relay,json={'relay_id':'workshop-1'})
    assert sync.status_code==200 and {v['id'] for v in sync.json()['versions']}=={v1['id'],v2['id']}
    assert sync.json()['assignments'][0]['playlist_version_id']==v2['id']


def test_existing_publications_dry_run_and_apply_do_not_change_manifests(env):
    c,db,admin,_=env
    v1=published(env);v2=draft(env,v1);v3=draft(env,v1)
    # Seed pre-policy publications, as on a schema4 deployment upgrade.
    with db.write() as conn:
        for v in (v2,v3):
            manifest=dict(v1['manifest'],playlist_version_id=v['id'],version=v['version'])
            conn.execute("UPDATE versions SET status='published',published_at=?,manifest=? WHERE id=?",(now(),json.dumps(manifest),v['id']))
    preview=c.post('/api/v1/retention',headers=admin,json={}).json()
    assert preview['dry_run'] and preview['playlists'][0]['retire_version_ids']==[v1['id']]
    assert not any(v['retired_at'] for v in versions(env))
    assert c.post('/api/v1/retention',headers=admin,json={'dry_run':False}).status_code==200
    assert len([v for v in versions(env) if not v['retired_at']])==2
    assert c.post('/api/v1/retention',headers=admin,json={'dry_run':'false'}).status_code==422
    assert c.post('/api/v1/retention',json={'dry_run':False}).status_code==401


def test_parallel_publications_never_leave_more_than_two_available(env):
    v1=published(env);drafts=[draft(env,v1) for _ in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:assert list(pool.map(lambda v:publish(env,v).status_code,drafts))==[200]*3
    assert len([v for v in versions(env) if not v['retired_at'] and v['status']=='published'])==2
