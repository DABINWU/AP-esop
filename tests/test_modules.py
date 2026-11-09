import json
import sqlite3
from pathlib import Path

import pytest

from center.db import Database, SCHEMA
from center.categories import MODULES
from scripts.pdf_samples import sample_pdf
from test_center import env, png_bytes, published, register, deploy


def task(env, name='模块任务'):
    r=env[0].post('/api/v1/playlists',headers=env[2],json={'name':name,'description':'四类指导内容'})
    assert r.status_code==200,r.text
    return r.json()


def upload(env, version, category='production', name='page.png', content=None):
    return env[0].post(f"/api/v1/versions/{version['id']}/modules/{category}/assets",headers=env[2],
                       data={'duration_seconds':'15'},files={'file':(name,content if content is not None else png_bytes())})


def get_version(env, version):
    return next(v for v in env[0].get('/api/v1/playlists',headers=env[2]).json()['versions'] if v['id']==version['id'])


def test_direct_module_upload_auto_attaches_and_manifest_groups(env):
    v=task(env)
    for category in ('production','inspection','custom'):
        r=upload(env,v,category); assert r.status_code==200,r.text
        assert r.json()['metadata']['playlist_id']==v['playlist_id']
    got=get_version(env,v)
    assert [i['category'] for i in got['items']]==['production','inspection','custom']
    assert all(i['duration_seconds']==15 for i in got['items'])
    response=env[0].post('/api/v1/versions/'+v['id']+'/publish',headers=env[2])
    assert response.status_code==200,response.text
    manifest=response.json()['manifest']
    assert manifest['schema_version']==2 and manifest['display']['fit']=='contain'
    assert manifest['display']['width']==1920 and manifest['display']['height']==1080
    assert [m['id'] for m in manifest['modules']]==list(MODULES)
    assert [len(m['items']) for m in manifest['modules']]==[1,1,0,1]
    data=env[0].get('/api/v1/playlists',headers=env[2]).json()
    assert next(p for p in data['playlists'] if p['id']==v['playlist_id'])['task_number']==v['task_number']
    assert upload(env,v).status_code==409


def test_module_validation_auth_and_cross_task_scope(env):
    v=task(env);other=task(env,'另一个任务')
    assert upload(env,v,'unknown').status_code==422
    assert upload(env,v,'check_video').status_code==422
    r=env[0].post(f"/api/v1/versions/{v['id']}/modules/production/assets",files={'file':('test.png',png_bytes())})
    assert r.status_code==401
    asset=upload(env,v).json()
    r=env[0].put('/api/v1/versions/'+other['id']+'/items',headers=env[2],json={'items':[{'asset_id':asset['id']}]})
    assert r.status_code==409 and '其他任务单' in r.json()['error']['message']
    r=env[0].put('/api/v1/versions/'+v['id']+'/items',headers=env[2],json={'items':[{'asset_id':asset['id'],'category':'inspection'}]})
    assert r.status_code==422


def test_scoped_file_delete_removes_all_pages_and_keeps_historical_reference(env):
    v=task(env);asset=upload(env,v).json()
    clone=env[0].post('/api/v1/versions/'+v['id']+'/clone',headers=env[2]).json()
    r=env[0].delete(f"/api/v1/versions/{clone['id']}/assets/{asset['id']}",headers=env[2])
    assert r.status_code==200 and r.json()['retained']
    assert get_version(env,clone)['items']==[]
    assert env[0].get(asset['files'][0]['download_path'],headers=env[2]).status_code==200
    r=env[0].delete(f"/api/v1/versions/{v['id']}/assets/{asset['id']}",headers=env[2])
    assert r.status_code==200 and not r.json()['retained']
    assert env[0].get(asset['files'][0]['download_path'],headers=env[2]).status_code==404


def test_delete_task_cleans_private_files_and_blocks_future_edits(env):
    v=task(env);asset=upload(env,v).json()
    r=env[0].delete('/api/v1/playlists/'+v['playlist_id'],headers=env[2])
    assert r.status_code==200 and not r.json()['retained_history']
    assert not env[0].delete('/api/v1/playlists/'+v['playlist_id'],headers=env[2]).json()['retained_history']
    assert env[0].get(asset['files'][0]['download_path'],headers=env[2]).status_code==404
    assert all(p['id']!=v['playlist_id'] for p in env[0].get('/api/v1/playlists',headers=env[2]).json()['playlists'])
    assert upload(env,v).status_code==409
    assert env[0].post('/api/v1/versions/'+v['id']+'/clone',headers=env[2]).status_code==409
    assert env[0].post('/api/v1/versions/'+v['id']+'/publish',headers=env[2]).status_code==409


def test_archive_published_task_retains_bytes_and_manifest(env):
    v=published(env)
    response=env[0].delete('/api/v1/playlists/'+v['playlist_id'],headers=env[2])
    assert response.status_code==200 and response.json()['retained_history']
    assert get_version(env,v)['manifest']==v['manifest']
    assert env[0].get(v['items'][0]['file']['download_path'],headers=env[2]).status_code==200
    register(env)
    assert deploy(env,v).status_code==409


def test_delete_assigned_task_is_blocked_even_after_timeout(env):
    from test_center import timeout
    v=published(env);register(env); t=deploy(env,v).json();timeout(env[1],t['id'])
    response=env[0].delete('/api/v1/playlists/'+v['playlist_id'],headers=env[2])
    assert response.status_code==409 and 'screen-a' in response.json()['error']['message']
    assert get_version(env,v)['manifest']==v['manifest']


def test_pending_conversion_blocks_publish_archive_and_remove(env):
    v=task(env);upload(env,v)
    asset=upload(env,v,'inspection','guide.pdf',sample_pdf()).json()
    assert asset['status']=='queued' and asset['metadata']['render_mode']=='screen_16_9'
    assert env[0].post('/api/v1/versions/'+v['id']+'/publish',headers=env[2]).status_code==409
    assert env[0].delete('/api/v1/playlists/'+v['playlist_id'],headers=env[2]).status_code==409
    assert env[0].delete(f"/api/v1/versions/{v['id']}/assets/{asset['id']}",headers=env[2]).status_code==409


def test_database_v1_migration_preserves_old_manifest(env,tmp_path):
    from dataclasses import replace
    old=tmp_path/'old';old.mkdir()
    c=sqlite3.connect(old/'center.sqlite3');c.executescript(SCHEMA)
    c.execute('INSERT INTO playlists VALUES(?,?)',('legacy','旧任务'))
    manifest='{"schema_version":1,"items":[]}'
    c.execute("INSERT INTO versions(id,playlist_id,name,version,status,created_at,manifest) VALUES('old','legacy','旧任务',1,'published','2026-01-01T00:00:00Z',?)",(manifest,))
    c.commit();c.close()
    db=Database(replace(env[1].settings,data_dir=old));db.initialize();db.initialize()
    with db.read() as c:
        assert c.execute('SELECT version FROM schema_version').fetchone()[0]==5
        assert c.execute('SELECT manifest FROM versions').fetchone()[0]==manifest
        assert c.execute('SELECT archived_at FROM playlists').fetchone()[0] is None


def test_scoped_delete_failure_keeps_retry_visible_for_legacy_file(env,monkeypatch):
    from test_center import image_asset
    v=task(env);asset=image_asset(env)
    r=env[0].put('/api/v1/versions/'+v['id']+'/items',headers=env[2],json={'items':[{'asset_id':asset['id']}]})
    assert r.status_code==200
    original=Path.unlink
    def occupied(path,*args,**kwargs):
        if path.stem==asset['id']:raise PermissionError('fixture locked')
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'unlink',occupied)
        assert env[0].delete(f"/api/v1/versions/{v['id']}/assets/{asset['id']}",headers=env[2]).status_code==503
    pending=env[0].get('/api/v1/assets',headers=env[2]).json()['assets'][0]
    assert pending['status']=='deleting' and pending['metadata']['attach_version_id']==v['id']
    assert pending['metadata']['category']=='custom'
    assert get_version(env,v)['items']==[]
    assert env[0].delete(f"/api/v1/versions/{v['id']}/assets/{asset['id']}",headers=env[2]).status_code==200
