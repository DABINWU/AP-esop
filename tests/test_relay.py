import hashlib
import json
import shutil
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from relay.app import create_app
from relay.config import Settings
from relay.db import dumps, now
from relay.protocol import RelayError


def settings(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path/'relay', center_url='http://center.test', center_key='center-'+'a'*30,
                    device_key='device-'+'b'*30, admin_key='admin-'+'c'*30, reserve_bytes=0, retry_count=1, **kwargs)


def snapshot(version='v1', revision=1, data=b'media contents', schema=2):
    file = dict(id='file_'+version, download_path='/api/v1/files/file_'+version, media_type='video/mp4',
                size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), role='video', page_number=None)
    item = dict(id='item_'+version, position=0, type='video', duration_seconds=None, playback='once', file=file)
    if schema == 2: item['category']='check_video'
    manifest = dict(schema_version=schema, playlist_version_id=version, name='测试', version=1, published_at='2026-11-06T00:00:00.000Z', loop=True, items=[item])
    if schema == 2:
        manifest['display']=dict(width=1920,height=1080,aspect_ratio='16:9',fit='contain')
        manifest['modules']=[dict(id=c,label=c,items=[item] if c=='check_video' else [],loop=True) for c in ('production','inspection','check_video','custom')]
    a = dict(device_id='screen-a',assignment_revision=revision,playlist_version_id=version,task_id='task_'+str(revision),assigned_at='2026-11-06T00:00:00.000Z',relay_acked_at=None)
    return dict(server_time=now(),heartbeat_seconds=10,offline_seconds=40,assignments=[a],assignment_history=[a],
        versions=[dict(id=version,manifest=manifest,manifest_sha256=hashlib.sha256(dumps(manifest).encode()).hexdigest(),files=[file])],
        tasks=[],devices=[dict(device_id='screen-a',name='1号屏',report_seq=0,last_contact_at=None)])


@pytest.fixture
def env(tmp_path):
    data=b'0123456789'*100
    upstream={'snapshot':snapshot(data=data),'offline':False,'content':data,'reports':[], 'acks':[],'error':None}
    def handler(request):
        if upstream['offline']: raise httpx.ConnectError('test connection unavailable',request=request)
        if request.url.path.endswith('/relay/sync'): return httpx.Response(200,json=upstream['snapshot'])
        if request.url.path.endswith('/relay/acks'):
            upstream['acks'].append(json.loads(request.content));return httpx.Response(200,json={'accepted':1})
        if request.url.path.endswith('/relay/reports'):
            body=json.loads(request.content);upstream['reports'].extend(body['reports'])
            if upstream['error']: return httpx.Response(upstream['error'],json={'error':{'code':'invalid'}})
            return httpx.Response(200,json={'results':[{'event_id':body['reports'][0]['event_id'],'status':'accepted'}]})
        return httpx.Response(200,content=upstream['content'])
    remote=httpx.Client(transport=httpx.MockTransport(handler))
    app=create_app(settings(tmp_path),background=False,client=remote)
    with TestClient(app) as client:
        engine=app.state.engine
        engine.sync_once()
        yield SimpleNamespace(client=client,app=app,engine=engine,upstream=upstream,data=data,
                              device={'Authorization':'Bearer '+app.state.db.settings.device_key},
                              admin={'Authorization':'Bearer '+app.state.db.settings.admin_key})
    remote.close()


def report(seq=1, revision=1, version='v1', stamp=None, **kwargs):
    return dict(event_id='screen-a-'+str(seq),device_id='screen-a',report_seq=seq,observed_at=stamp or now(),
        assignment_revision=revision,actual_version_id=version,current_item_id='item_'+version if version else None,
        playback_state='playing' if version else 'idle',pending_version_id=None,error=None,**kwargs)


def sync(env, known=0):
    return env.client.post('/api/v1/device/sync',headers=env.device,json={'device_id':'screen-a','known_assignment_revision':known})


def test_download_verify_ready_range_and_auth(env):
    assert sync(env).json()['ready'] is False
    assert env.client.get('/api/v1/files/file_v1',headers=env.device).status_code==503
    env.engine.download_once();env.engine.ack_once()
    result=sync(env).json();assert result['ready'] and result['manifest']['schema_version']==2
    assert env.upstream['acks'][0]['assignments'][0]['assignment_revision']==1
    path='/api/v1/files/file_v1'
    assert env.client.get(path).status_code==401
    assert env.client.get(path,headers=env.admin).status_code==401
    r=env.client.get(path,headers={**env.device,'Range':'bytes=10-29'})
    assert r.status_code==206 and r.content==env.data[10:30]
    assert r.headers['content-range']=='bytes 10-29/1000'
    assert r.headers['accept-ranges']=='bytes'
    for value, expected in [('bytes=990-',env.data[990:]),('bytes=-12',env.data[-12:]),('bytes=0-999999',env.data)]:
        assert env.client.get(path,headers={**env.device,'Range':value}).content==expected
    for value in ('bytes=1000-','bytes=50-2','bytes=-0','bytes=1-2,5-6','invalid'):
        r=env.client.get(path,headers={**env.device,'Range':value})
        assert r.status_code==416 and r.headers['content-range']=='bytes */1000'
    assert env.client.head(path,headers={**env.device,'Range':'bytes=0-4'}).headers['content-length']=='5'
    assert env.client.get(path,headers={**env.device,'Range':'bytes=1-2','If-Range':'wrong'}).status_code==200
    assert env.client.post('/api/v1/device/activation-check',headers=env.device,json={'device_id':'screen-a','assignment_revision':1,'playlist_version_id':'v1'}).json()['allowed']


def test_latest_target_blocks_old_activation_and_survives_offline(env):
    env.engine.download_once()
    env.upstream['snapshot']=snapshot('v2',2,data=env.data)
    env.engine.sync_once()
    assert sync(env).json()['assignment']['assignment_revision']==2
    assert sync(env).json()['manifest'] is None
    assert env.client.post('/api/v1/device/activation-check',headers=env.device,json={'device_id':'screen-a','assignment_revision':1,'playlist_version_id':'v1'}).status_code==409
    assert sync(env,3).status_code==503
    env.engine.download_once()
    env.upstream['offline']=True
    with pytest.raises(httpx.ConnectError):env.engine.sync_once()
    assert sync(env).json()['manifest']['playlist_version_id']=='v2'
    assert env.client.get('/api/v1/files/file_v1',headers=env.device).content==env.data
    assert env.client.get('/api/v1/health').status_code==200


def test_persistent_outbox_dedupe_stale_and_original_contact(env):
    old=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat().replace('+00:00','Z')
    payload=report(stamp=old)
    r=env.client.post('/api/v1/device/reports',headers=env.device,json={'report':payload});assert r.json()['status']=='accepted'
    assert env.client.post('/api/v1/device/reports',headers=env.device,json={'report':payload}).json()['status']=='duplicate'
    changed={**payload,'playback_state':'paused'}
    assert env.client.post('/api/v1/device/reports',headers=env.device,json={'report':changed}).status_code==409
    env.upstream['offline']=True
    with pytest.raises(httpx.ConnectError):env.engine.flush_once()
    status=env.client.get('/api/v1/local/status',headers=env.admin).json()
    assert status['outbox']['pending']==1 and status['devices'][0]['online_state']=='offline'
    assert status['devices'][0]['last_contact_at']==datetime.fromisoformat(old.replace('Z','+00:00')).isoformat(timespec='milliseconds').replace('+00:00','Z')
    with env.app.state.db.write() as c:c.execute('UPDATE events SET retry_at=0')
    env.upstream['offline']=False;env.engine.flush_once()
    assert env.upstream['reports'][0]['observed_at'].startswith(old[:19])
    stale={**payload,'event_id':'other-event'}
    assert env.client.post('/api/v1/device/reports',headers=env.device,json={'report':stale}).json()['status']=='stale'
    env.engine.sync_once() # Older center sequence must not revert local actual state.
    assert env.client.get('/api/v1/local/status',headers=env.admin).json()['devices'][0]['report_seq']==1


def test_checksum_failure_never_ready_and_retry(env):
    env.upstream['content']=b'x'*len(env.data)
    env.engine.download_once()
    assert sync(env).json()['ready'] is False
    assert not list((env.app.state.db.settings.data_dir/'tmp').glob('*.partial'))
    assert env.client.get('/api/v1/files/file_v1',headers=env.device).status_code==503
    env.upstream['content']=env.data
    with env.app.state.db.write() as c:c.execute('UPDATE versions SET retry_at=0')
    env.engine.download_once();assert sync(env).json()['ready']


def test_manifest_identity_paths_and_revision_rollback(env):
    env.engine.download_once()
    changed=snapshot('v1',1,data=env.data);changed['versions'][0]['manifest']['name']='tampered'
    env.upstream['snapshot']=changed
    with pytest.raises(RelayError,match='SHA-256'):env.engine.sync_once()
    changed['versions'][0]['manifest_sha256']=hashlib.sha256(dumps(changed['versions'][0]['manifest']).encode()).hexdigest()
    with pytest.raises(RelayError,match='不可变版本'):env.engine.sync_once()
    env.upstream['snapshot']=snapshot('v2',2,data=env.data);env.engine.sync_once()
    env.upstream['snapshot']=snapshot('v1',1,data=env.data);env.engine.sync_once()
    assert sync(env).json()['assignment']['assignment_revision']==2


def test_disk_full_preserves_old_cache(env,monkeypatch):
    env.engine.download_once()
    env.upstream['snapshot']=snapshot('v2',2,data=env.data);env.engine.sync_once()
    monkeypatch.setattr('relay.cache.shutil.disk_usage',lambda path:SimpleNamespace(total=100,free=0))
    env.engine.download_once()
    assert sync(env).json()['ready'] is False
    assert '磁盘' in sync(env).json()['error']
    assert env.client.get('/api/v1/files/file_v1',headers=env.device).content==env.data


def test_corruption_and_partial_recovery(env):
    env.engine.download_once()
    with env.app.state.db.read() as c:row=c.execute('SELECT * FROM files').fetchone()
    target=env.app.state.cache.path(json.loads(row['descriptor']))
    target.write_bytes(b'bad')
    partial=env.app.state.db.settings.data_dir/'tmp/file_v1.partial';partial.write_bytes(b'half')
    env.app.state.cache.recover()
    assert not partial.exists() and not sync(env).json()['ready']
    env.engine.download_once();assert sync(env).json()['ready']


def test_cleanup_protects_current_previous_actual_and_outbox(env):
    env.engine.download_once()
    env.client.post('/api/v1/device/reports',headers=env.device,json={'report':report()})
    for i in range(2,5):
        env.upstream['snapshot']=snapshot('v'+str(i),i,data=env.data);env.engine.sync_once();env.engine.download_once()
    env.app.state.cache.cleanup(pressure=True)
    assert env.client.get('/api/v1/files/file_v1',headers=env.device).status_code==200
    assert env.client.get('/api/v1/files/file_v2',headers=env.device).status_code==503
    for version in ('v3','v4'):assert env.client.get('/api/v1/files/file_'+version,headers=env.device).status_code==200


def test_blocked_report_does_not_block_other_events(env):
    env.client.post('/api/v1/device/reports',headers=env.device,json={'report':report()})
    env.upstream['error']=409;env.engine.flush_once()
    env.upstream['error']=None
    env.client.post('/api/v1/device/reports',headers=env.device,json={'report':report(2)})
    env.engine.flush_once()
    status=env.client.get('/api/v1/local/status',headers=env.admin).json()
    assert status['outbox']==dict(pending=0,blocked=1)


def test_no_local_task_mutation_and_keys_not_exposed(env):
    assert env.client.post('/api/v1/deployments',headers=env.admin,json={}).status_code==404
    assert env.client.get('/api/v1/local/status',headers=env.device).status_code==401
    response=env.client.get('/api/v1/local/status',headers=env.admin)
    for secret in (env.app.state.db.settings.center_key,env.app.state.db.settings.admin_key,env.app.state.db.settings.device_key):
        assert secret not in response.text and secret not in env.client.get('/').text


def test_legacy_manifest_and_invalid_module_lists(env):
    env.upstream['snapshot']=snapshot('legacy',2,data=env.data,schema=1);env.engine.sync_once();env.engine.download_once()
    assert sync(env).json()['manifest']['schema_version']==1
    bad=snapshot('bad',3,data=env.data)
    bad['versions'][0]['manifest']['modules'][0]['items']=bad['versions'][0]['manifest']['items']
    bad['versions'][0]['manifest_sha256']=hashlib.sha256(dumps(bad['versions'][0]['manifest']).encode()).hexdigest()
    env.upstream['snapshot']=bad
    with pytest.raises(RelayError,match='模块播放项'):env.engine.sync_once()


def test_reassignment_during_stream_cancels_obsolete_download(env):
    large=b'x'*(600*1024)
    env.upstream['snapshot']=snapshot('large',2,data=large)
    env.engine.sync_once()
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield large[:256*1024]
            env.upstream['snapshot']=snapshot('newest',3,data=large)
            env.engine.sync_once()
            yield large[256*1024:]
    def handler(request):
        if request.url.path.endswith('/relay/sync'):return httpx.Response(200,json=env.upstream['snapshot'])
        return httpx.Response(200,stream=Chunks())
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        previous=env.engine.client;env.engine.client=client
        try:env.engine.download_once()
        finally:env.engine.client=previous
    result=sync(env).json()
    assert result['assignment']['assignment_revision']==3 and not result['ready']
    assert env.client.get('/api/v1/files/file_large',headers=env.device).status_code==503
    assert not list((env.app.state.db.settings.data_dir/'tmp').glob('*.partial'))


def test_bootstrap_original_center_state_and_late_report(env):
    old=snapshot('v1',1,data=env.data)
    latest=snapshot('v2',2,data=env.data)
    latest['versions']+=old['versions'];latest['assignment_history']+=old['assignment_history']
    stamp=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    latest['devices'][0].update(report_seq=42,last_contact_at=stamp,actual_version_id='v1',current_item_id='item_v1',
                                reported_assignment_revision=1,playback_state='paused',pending_version_id='v2')
    env.upstream['snapshot']=latest;env.engine.sync_once()
    status=env.client.get('/api/v1/local/status',headers=env.admin).json()['devices'][0]
    assert status['report_seq']==42 and status['last_contact_at']==stamp and status['online_state']=='offline'
    assert status['playback_state']=='paused' and status['actual_version_id']=='v1'
    payload=report(43,1,'v1');payload['pending_version_id']='v2'
    assert env.client.post('/api/v1/device/reports',headers=env.device,json={'report':payload}).json()['status']=='accepted'
    assert sync(env).json()['assignment']['assignment_revision']==2


@pytest.mark.parametrize('bad_path',['https://other.test/api/v1/files/file_bad','/api/v1/files/../secret','/api/v1/files/file_bad?key=secret'])
def test_external_or_ambiguous_download_paths_fail_closed(env,bad_path):
    bad=snapshot('bad',2,data=env.data)
    file=bad['versions'][0]['files'][0];file['download_path']=bad_path
    # The same dictionary is used by root/module items, matching an internally consistent hostile response.
    bad['versions'][0]['manifest_sha256']=hashlib.sha256(dumps(bad['versions'][0]['manifest']).encode()).hexdigest()
    env.upstream['snapshot']=bad
    with pytest.raises(RelayError,match='路径'):env.engine.sync_once()
    assert sync(env).json()['assignment']['assignment_revision']==1


def test_outbox_full_refuses_acknowledgement_without_dropping_event(env):
    env.app.state.db.settings=replace(env.app.state.db.settings,max_outbox_events=1)
    assert env.client.post('/api/v1/device/reports',headers=env.device,json={'report':report()}).status_code==200
    r=env.client.post('/api/v1/device/reports',headers=env.device,json={'report':report(2)})
    assert r.status_code==503 and r.json()['error']['code']=='outbox_full'
    with env.app.state.db.read() as c:
        assert c.execute('SELECT count(*) FROM events').fetchone()[0]==1
        assert c.execute('SELECT report_seq FROM devices').fetchone()[0]==1
