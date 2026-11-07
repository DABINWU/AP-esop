import io
import json
import time
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from center.app import create_app
from center.media import MediaError, excel_warnings, run_command
from test_center import env, png_bytes, published, register, deploy, report, send, timeout


def test_file_tamper_prevents_publish(env):
    c, db, admin, relay = env
    a = c.post('/api/v1/assets',headers=admin,files={'file':('source.png',png_bytes())}).json()
    v = c.post('/api/v1/playlists',headers=admin,json={'name':'完整性'}).json()
    c.put(f"/api/v1/versions/{v['id']}/items",headers=admin,json={'items':[{'asset_id':a['id']}]})
    with db.read() as conn:
        path = conn.execute('SELECT path FROM files WHERE id=?',(a['files'][0]['id'],)).fetchone()[0]
    (db.settings.data_dir/path).write_bytes(b'tampered')
    response=c.post(f"/api/v1/versions/{v['id']}/publish",headers=admin)
    assert response.status_code==409 and '校验失败' in response.json()['error']['message']


def test_superseded_report_does_not_confirm_new_task(env):
    c, db, admin, relay=env
    old,new=published(env),published(env); register(env)
    first=deploy(env,old).json(); timeout(db,first['id'])
    second=deploy(env,new,'second-request').json()
    assert send(env,[report(old,first['targets'][0])]).status_code==200
    state=c.get('/api/v1/devices',headers=admin).json()['devices'][0]
    assert state['actual_version_id']==old['id'] and state['desired']['playlist_version_id']==new['id']
    assert state['pending']
    task=next(t for t in c.get('/api/v1/deployments',headers=admin).json()['tasks'] if t['id']==second['id'])
    assert task['status']=='running' and task['targets'][0]['status']=='pending'
    assert send(env,[report(new,second['targets'][0],2)]).status_code==200


def test_failed_target_terminal_and_paused_not_success(env):
    c, db, admin, relay=env
    v=published(env);register(env);task=deploy(env,v).json()
    assert send(env,[report(v,task['targets'][0],playback_state='paused')]).status_code==200
    assert c.get('/api/v1/deployments',headers=admin).json()['tasks'][0]['targets'][0]['status']=='pending'
    failure=report(v,task['targets'][0],2,playback_state='error',actual_version_id=None,current_item_id=None,error='下载校验失败')
    assert send(env,[failure,report(v,task['targets'][1])]).status_code==200
    assert c.get('/api/v1/deployments',headers=admin).json()['tasks'][0]['status']=='failed'
    assert send(env,[report(v,task['targets'][0],3)]).status_code==200
    assert c.get('/api/v1/deployments',headers=admin).json()['tasks'][0]['status']=='failed'


def test_validation_and_unknown_devices_do_not_acquire_task_lock(env):
    c,db,admin,relay=env
    v=published(env);register(env)
    assert deploy(env,v,ids=('not-registered',)).status_code==404
    assert c.get('/api/v1/deployments',headers=admin).json()['tasks']==[]
    assert c.post('/api/v1/devices',headers=admin,json={'device_id':'screen-a','name':'dup'}).status_code==409
    assert c.post('/api/v1/devices',headers=admin,json={'device_id':'../bad','name':'bad'}).status_code==422
    assert c.post('/api/v1/relay/sync',headers=relay,json={'relay_id':'other'}).status_code==404
    assert c.post('/api/v1/playlists',headers=admin,json={'name':'  '}).status_code==422
    assert c.post('/api/v1/relay/sync',headers=relay,json={'relay_id':'workshop-1','unexpected':True}).status_code==422
    assert deploy(env,v).status_code==200


def test_request_stream_limit_for_chunked_upload(env):
    c,db,admin,relay=env
    settings=replace(db.settings,upload_max_bytes=32,video_max_bytes=32)
    def content():
        yield b'--test\r\nContent-Disposition: form-data; name="file"; filename="large.png"\r\nContent-Type: image/png\r\n\r\n'
        for i in range(20): yield b'X'*4096
        yield b'\r\n--test--\r\n'
    with TestClient(create_app(settings)) as limited:
        r=limited.post('/api/v1/assets',headers=admin|{'Content-Type':'multipart/form-data; boundary=test'},content=content())
        assert r.status_code==413,r.text
    assert not list((db.settings.data_dir/'originals').iterdir())


def test_conversion_command_timeout_kills_subprocess():
    import sys
    start=time.monotonic()
    with pytest.raises(MediaError,match='超时'):
        run_command([sys.executable,'-c','import time; time.sleep(15)'],.1)
    assert time.monotonic()-start < 5


def test_excel_preflight_warnings_dont_mutate_original(tmp_path):
    from openpyxl import Workbook
    w=Workbook();sheet=w.active;sheet['A1']='长表';sheet.page_setup.fitToHeight=1
    path=tmp_path/'source.xlsx';w.save(path); original=path.read_bytes()
    warnings=excel_warnings(path)
    assert any('未设置打印区域' in value for value in warnings)
    assert any('单页高度' in value for value in warnings)
    assert path.read_bytes()==original
