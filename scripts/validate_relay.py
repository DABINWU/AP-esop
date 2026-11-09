"""Real HTTP center -> Windows relay process integration, isolated from user data.

Run from repository root using the Windows .venv Python and Docker Desktop.
Does not install an OS service, modify firewall, or emulate Android playback.
Retains evidence and isolated data, stops test processes/containers on completion.
"""
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from relay.db import dumps


def wait(check, timeout=180):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            result=check()
            if result:return result
        except (httpx.RequestError, httpx.HTTPStatusError):pass
        time.sleep(.5)
    raise AssertionError('Timed out waiting for integration check')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def main():
    root=Path(__file__).resolve().parents[1]
    run=root/'test-output'/'relay-http'/str(time.time_ns());run.mkdir(parents=True)
    center_port,relay_port=free_port(),free_port()
    center_url=f'http://127.0.0.1:{center_port}'
    relay_url=f'http://127.0.0.1:{relay_port}'
    center_admin='integration-admin-'+secrets.token_hex(24)
    center_key='integration-relay-'+secrets.token_hex(24)
    device_key='integration-device-'+secrets.token_hex(24)
    local_key='integration-local-'+secrets.token_hex(24)
    project='esop-relay-'+run.name[-10:]
    env=run/'center.env'
    env.write_text(f'ADMIN_API_KEY={center_admin}\nRELAY_API_KEY={center_key}\nESOP_BIND_IP=127.0.0.1\nESOP_PORT={center_port}\nESOP_DATA_DIR={(run/"center-data").as_posix()}\n',encoding='utf-8')
    config=run/'relay.env'
    config_text=(f'CENTER_URL={center_url}\nCENTER_API_KEY={center_key}\nDEVICE_API_KEY={device_key}\nLOCAL_ADMIN_API_KEY={local_key}\n'
        f'RELAY_ID=workshop-1\nRELAY_HOST=127.0.0.1\nRELAY_PORT={relay_port}\nRELAY_DATA_DIR={(run/"relay-data").as_posix()}\n'
        'POLL_SECONDS=1\nCONNECT_TIMEOUT_SECONDS=1\nREAD_TIMEOUT_SECONDS=2\nDOWNLOAD_TIMEOUT_SECONDS=30\nDISK_RESERVE_MB=0\nRETRY_MAX_SECONDS=2\n')
    config.write_text(config_text,encoding='utf-8')
    compose=['docker','compose','--env-file',str(env),'-p',project]
    def docker(*args):
        subprocess.run([*compose,*args],cwd=root,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    process=None
    def start():
        nonlocal process
        process=subprocess.Popen([sys.executable,'-m','relay','--config',str(config)],cwd=root,
            stdout=(run/'relay-process.log').open('ab'),stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        wait(lambda:httpx.get(relay_url+'/api/v1/health',timeout=2).status_code==200,30)
    def kill():
        nonlocal process
        if process and process.poll() is None:process.kill();process.wait(timeout=15)
        process=None
    checks=[]
    def record(name, **details):
        checks.append(dict(check=name,**details));print(name,flush=True)
    samples_candidates=sorted((p for p in (root/'test-output/media').glob('*/samples/basic-layout.xlsx')
        if (p.parent/'short.mp4').is_file() and (p.parent.parent/'result.json').is_file()),key=lambda p:p.stat().st_mtime,reverse=True)
    assert samples_candidates,'Run scripts.validate_media in Docker first to generate non-customer samples'
    samples=samples_candidates[0].parent
    try:
        docker('up','-d','--no-build')
        with httpx.Client(base_url=center_url,headers={'Authorization':'Bearer '+center_admin},timeout=30) as center, \
             httpx.Client(base_url=relay_url,headers={'Authorization':'Bearer '+device_key},timeout=30) as local:
            wait(lambda:center.get('/api/v1/health').status_code==200)
            def post(path,body=None):
                r=center.post('/api/v1/'+path,json=body);r.raise_for_status();return r.json()
            assets=[]
            for filename in ('basic-layout.xlsx','short.mp4'):
                with (samples/filename).open('rb') as stream:
                    r=center.post('/api/v1/assets',files={'file':(filename,stream)})
                r.raise_for_status();assets.append(r.json()['id'])
            def converted():
                r=center.get('/api/v1/assets');r.raise_for_status()
                selected=[a for a in r.json()['assets'] if a['id'] in assets]
                assert all(a['status']!='failed' for a in selected)
                return len(selected)==2 and all(a['status']=='ready' for a in selected)
            wait(converted)
            playlist=post('playlists',{'name':'真实中心中转联调'})
            version=playlist['id']
            r=center.put('/api/v1/versions/'+version+'/items',json={'items':[{'asset_id':assets[0],'category':'production','duration_seconds':20},{'asset_id':assets[1],'category':'check_video'}]});r.raise_for_status()
            published=post('versions/'+version+'/publish')
            manifest=published['manifest']
            assert len([i for i in manifest['items'] if i['type']=='page'])>=2
            for device in ('screen-a','screen-b'):post('devices',{'device_id':device,'name':device})
            task=post('deployments',{'request_id':'real-relay-first','playlist_version_id':version,'device_ids':['screen-a','screen-b'],'timeout_seconds':10})
            revisions={t['device_id']:t['assignment_revision'] for t in task['targets']}
            start()
            def account_login(url,folder):
                browser=httpx.Client(base_url=url,timeout=15)
                creds=dict(line.split('=',1) for line in (folder/'initial-login.txt').read_text(encoding='utf-8').splitlines())
                response=browser.post('/api/v1/auth/login',json=creds,headers={'X-ESOP-Login':'1'});response.raise_for_status()
                assert 'HttpOnly' in response.headers['set-cookie']
                session=response.json()
                assert browser.get('/api/v1/auth/session').json()['csrf_token']==session['csrf_token']
                return browser,creds,session
            center_browser,center_credentials,center_session=account_login(center_url,run/'center-data')
            relay_browser,relay_credentials,relay_session=account_login(relay_url,run/'relay-data')
            def sync(device='screen-a',known=0):
                r=local.post('/api/v1/device/sync',json={'device_id':device,'known_assignment_revision':known});r.raise_for_status();return r.json()
            wait(lambda:sync()['ready'] and sync('screen-b')['ready'])
            snap=sync()
            assert snap['manifest']==manifest
            manifest_digest=hashlib.sha256(dumps(manifest).encode()).hexdigest()
            relay_db=__import__('sqlite3').connect(run/'relay-data/relay.sqlite3')
            row=relay_db.execute('SELECT sha256,files_json FROM versions WHERE id=?',(version,)).fetchone()
            assert row[0]==manifest_digest
            files=json.loads(row[1]);relay_db.close()
            assert any(f['role']=='pdf' for f in files)
            for file in files:
                r=local.get(file['download_path']);r.raise_for_status()
                assert len(r.content)==file['size_bytes'] and hashlib.sha256(r.content).hexdigest()==file['sha256']
            video=next(i['file'] for i in manifest['items'] if i['type']=='video')
            r=local.get(video['download_path'],headers={'Range':'bytes=5-31'})
            assert r.status_code==206 and r.headers['content-range']==f'bytes 5-31/{video["size_bytes"]}'
            assert r.content==(samples/'short.mp4').read_bytes()[5:32]
            assert local.get(video['download_path'],headers={'Range':'bytes=999999-'}).status_code==416
            record('real multi-page Excel, PDF, video, manifest SHA and two-screen targets',files=len(files),pages=sum(i['type']=='page' for i in manifest['items']))
            old=(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat(timespec='milliseconds').replace('+00:00','Z')
            def report(device,seq,revision,vid,stamp):
                selected=manifest if vid==version else versions[vid]
                return dict(event_id=device+'-'+str(seq),device_id=device,report_seq=seq,observed_at=stamp,assignment_revision=revision,
                    actual_version_id=vid,current_item_id=selected['items'][0]['id'],playback_state='playing',pending_version_id=None,error=None)
            payload=report('screen-a',1,revisions['screen-a'],version,old)
            r=local.post('/api/v1/device/reports',json={'report':payload});r.raise_for_status()
            def devices():
                response=center.get('/api/v1/devices');response.raise_for_status();return response.json()['devices']
            wait(lambda:next(d for d in devices() if d['device_id']=='screen-a')['report_seq']==1)
            assert next(d for d in devices() if d['device_id']=='screen-a')['last_contact_at']==old
            docker('stop','web')
            wait(lambda:local.get('/api/v1/local/status',headers={'Authorization':'Bearer '+local_key}).json()['center']['state']=='offline',30)
            delayed=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(timespec='milliseconds').replace('+00:00','Z')
            payload=report('screen-a',2,revisions['screen-a'],version,delayed)
            r=local.post('/api/v1/device/reports',json={'report':payload});assert r.json()['status']=='accepted'
            assert local.get(video['download_path']).status_code==200 and sync()['ready']
            kill()
            (run/'relay-data/tmp/interrupted.partial').write_bytes(b'incomplete injected recovery fixture')
            start()
            assert relay_browser.get('/api/v1/auth/session').json()['csrf_token']==relay_session['csrf_token']
            assert not (run/'relay-data/tmp/interrupted.partial').exists()
            assert local.post('/api/v1/device/reports',json={'report':payload}).json()['status']=='duplicate'
            status=local.get('/api/v1/local/status',headers={'Authorization':'Bearer '+local_key}).json()
            assert status['outbox']['pending']==1 and sync()['ready']
            docker('start','web')
            wait(lambda:center_browser.get('/api/v1/auth/session').status_code==200)
            assert center_browser.get('/api/v1/auth/session').json()['csrf_token']==center_session['csrf_token']
            wait(lambda:next(d for d in devices() if d['device_id']=='screen-a')['report_seq']==2)
            assert next(d for d in devices() if d['device_id']=='screen-a')['last_contact_at']==delayed
            record('center offline: cached service, durable outbox, hard relay restart, duplicate report, original observed_at')
            wait(lambda:center.get('/api/v1/deployments').json()['tasks'][0]['status']=='timed_out',20)
            assert sync('screen-b')['assignment']['playlist_version_id']==version
            versions={version:manifest}
            def next_version():
                v=post('playlists',{'name':'中转改派验证 '+str(len(versions))})['id']
                r=center.put('/api/v1/versions/'+v+'/items',json={'items':[{'asset_id':assets[0],'category':'production','duration_seconds':20},{'asset_id':assets[1],'category':'check_video'}]});r.raise_for_status()
                m=post('versions/'+v+'/publish')['manifest'];versions[v]=m;return v
            v2=next_version()
            post('deployments',{'request_id':'real-relay-second','playlist_version_id':v2,'device_ids':['screen-b'],'timeout_seconds':10})
            wait(lambda:center.get('/api/v1/deployments').json()['tasks'][0]['status']=='timed_out',20)
            v3=next_version()
            task3=post('deployments',{'request_id':'real-relay-third','playlist_version_id':v3,'device_ids':['screen-b'],'timeout_seconds':30})
            latest=task3['targets'][0]['assignment_revision']
            def latest_ready():
                state=sync('screen-b')
                return state['ready'] and state['assignment']['playlist_version_id']==v3
            wait(latest_ready)
            assert sync('screen-a')['assignment']['playlist_version_id']==version
            stale=local.post('/api/v1/device/activation-check',json={'device_id':'screen-b','assignment_revision':revisions['screen-b'],'playlist_version_id':version})
            assert stale.status_code==409
            fresh=local.post('/api/v1/device/activation-check',json={'device_id':'screen-b','assignment_revision':latest,'playlist_version_id':v3})
            fresh.raise_for_status()
            assert fresh.json()['allowed']
            r=local.post('/api/v1/device/reports',json={'report':report('screen-b',1,latest,v3,datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))});r.raise_for_status()
            wait(lambda:center.get('/api/v1/deployments').json()['tasks'][0]['status']=='succeeded')
            record('offline terminal gets latest only; timed-out task keeps target; independent per-screen revisions; stale activation rejected')
            kill()
            reserve_mb=shutil.disk_usage(run).free//1024**2+1024
            config.write_text(config_text.replace('DISK_RESERVE_MB=0',f'DISK_RESERVE_MB={reserve_mb}'),encoding='utf-8')
            v4=next_version()
            post('deployments',{'request_id':'real-relay-disk','playlist_version_id':v4,'device_ids':['screen-b'],'timeout_seconds':10})
            start()
            wait(lambda:not sync('screen-b')['ready'] and '磁盘' in (sync('screen-b')['error'] or ''),30)
            assert local.get(video['download_path']).status_code==200
            record('configured disk reserve exhaustion: explicit error and protected cache retained')
            kill();config.write_text(config_text,encoding='utf-8');start()
            wait(lambda:sync('screen-b')['ready'])
            assert post('versions/'+version+'/publish')['manifest']==manifest
            record('space restored: safe redownload/reprepare; original published manifest unchanged')
            # Real Excel retention: preserve an offline old target, then reclaim it only after confirmation.
            with (samples/'basic-layout.xlsx').open('rb') as stream:r=center.post('/api/v1/assets',files={'file':('retention-layout.xlsx',stream)})
            r.raise_for_status();retention_asset=r.json()['id']
            def retention_ready():
                row=next(a for a in center.get('/api/v1/assets').json()['assets'] if a['id']==retention_asset)
                assert row['status']!='failed';return row if row['status']=='ready' else None
            original_asset=wait(retention_ready)
            p1=post('playlists',{'name':'两版本保留验证'})['id']
            r=center.put('/api/v1/versions/'+p1+'/items',json={'items':[{'asset_id':retention_asset}]});r.raise_for_status()
            m1=post('versions/'+p1+'/publish')['manifest'];versions[p1]=m1
            post('devices',{'device_id':'retention-screen','name':'保留策略屏幕'})
            wait(lambda:not any(t['status']=='running' for t in center.get('/api/v1/deployments').json()['tasks']),30)
            t1=post('deployments',{'request_id':'retention-old','playlist_version_id':p1,'device_ids':['retention-screen'],'timeout_seconds':10})
            wait(lambda:not any(t['status']=='running' for t in center.get('/api/v1/deployments').json()['tasks']),20)
            def policy_version(source,asset_id):
                vid=post('versions/'+source+'/clone')['id']
                response=center.put('/api/v1/versions/'+vid+'/items',json={'items':[{'asset_id':asset_id}]});response.raise_for_status()
                content=post('versions/'+vid+'/publish')['manifest'];versions[vid]=content;return vid
            p2=policy_version(p1,assets[1]);p3=policy_version(p2,assets[0])
            rows={v['id']:v for v in center.get('/api/v1/playlists').json()['versions']}
            assert rows[p2]['retired_at'] and not rows[p1]['retired_at']
            assert center.get(original_asset['files'][0]['download_path']).status_code==200
            record('two-publication retention preserves offline old Excel target and expires unused intermediate version')
            t3=post('deployments',{'request_id':'retention-new','playlist_version_id':p3,'device_ids':['retention-screen'],'timeout_seconds':30})
            def latest_policy_ready():
                response=local.post('/api/v1/device/sync',json={'device_id':'retention-screen','known_assignment_revision':0});response.raise_for_status()
                return response.json()['ready'] and response.json()['assignment']['playlist_version_id']==p3
            wait(latest_policy_ready)
            rr=report('retention-screen',1,t3['targets'][0]['assignment_revision'],p3,datetime.now(timezone.utc).isoformat().replace('+00:00','Z'))
            response=local.post('/api/v1/device/reports',json={'report':rr});response.raise_for_status()
            wait(lambda:next(d for d in center.get('/api/v1/devices').json()['devices'] if d['device_id']=='retention-screen')['actual_version_id']==p3)
            p4=policy_version(p3,assets[0])
            assert all(center.get(f['download_path']).status_code==410 for f in original_asset['files'])
            import sqlite3
            connection=sqlite3.connect('file:'+str(run/'center-data/center.sqlite3')+'?mode=ro',uri=True)
            paths=[row[0] for row in connection.execute('SELECT path FROM files WHERE asset_id=?',(retention_asset,))];connection.close()
            assert paths and not any((run/'center-data'/path).exists() for path in paths)
            assert center.get(manifest['items'][0]['file']['download_path']).status_code==200
            record('after actual confirmation, exclusive Excel original/PDF/pages reclaimed; shared media and immutable history survive')
            wait(lambda:not any(t['status']=='running' for t in center.get('/api/v1/deployments').json()['tasks']),30)
            post('deployments',{'request_id':'retention-pending','playlist_version_id':p4,'device_ids':['retention-screen'],'timeout_seconds':10})
            p5=post('versions/'+p4+'/clone')['id']
            blocked=center.post('/api/v1/versions/'+p5+'/publish')
            assert blocked.status_code==409 and blocked.json()['error']['code']=='version_retention_blocked'
            record('two distinct desired/actual old versions block third publication instead of deleting active files')
            for browser,creds,session in ((center_browser,center_credentials,center_session),(relay_browser,relay_credentials,relay_session)):
                previous=httpx.Cookies(browser.cookies)
                r=browser.post('/api/v1/auth/password',json={'current_password':creds['password'],'new_password':'integration-new-password-'+secrets.token_hex(12)},headers={'X-CSRF-Token':session['csrf_token']})
                r.raise_for_status();new_session=r.json()
                assert browser.get('/api/v1/auth/session').status_code==200
                with httpx.Client(base_url=str(browser.base_url),cookies=previous) as expired:
                    assert expired.get('/api/v1/auth/session').status_code==401
                assert browser.post('/api/v1/auth/logout',headers={'X-CSRF-Token':new_session['csrf_token']}).status_code==200
                assert browser.get('/api/v1/auth/session').status_code==401
                browser.close()
            record('real center/relay accounts: password login, refresh, process restart keeps session, password change revokes old cookies, logout')
            status=local.get('/api/v1/local/status',headers={'Authorization':'Bearer '+local_key}).json()
            (run/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
            result=dict(kind='real Docker center -> Windows relay over loopback HTTP; terminal requests simulated, no Android hardware',
                checks=checks,artifacts=str(run),center_url=center_url,relay_url=relay_url,project=project)
            (run/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        kill()
        docker('down')


if __name__=='__main__':main()
