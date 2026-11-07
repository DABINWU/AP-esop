"""Local Docker Compose acceptance: actual process restarts, simulated relay HTTP only.

Creates its own project, secrets and bind directories under test-output; never uses production .env.
No relay client, Android hardware, VPN or NAS is represented by this test.
"""
import argparse
import hashlib
import json
import secrets
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx

from center.db import now
from scripts.generate_samples import generate


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=18080)
    parser.add_argument('--keep-running',action='store_true')
    args=parser.parse_args()
    root=Path('test-output').resolve()/('compose-'+str(time.time_ns()))
    root.mkdir(parents=True)
    data=root/'data'; data.mkdir()
    admin,relay=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    env=root/'test.env'
    project='esop-check-'+str(time.time_ns())[-9:]
    result={'scope':'Docker center + actual process recovery; simulated relay HTTP, no hardware', 'checks':[], 'artifacts':str(root)}

    def write_env(directory):
        env.write_text('\n'.join([f'ADMIN_API_KEY={admin}',f'RELAY_API_KEY={relay}',
            f'ESOP_DATA_DIR={directory.as_posix()}',f'ESOP_PORT={args.port}','ESOP_BIND_IP=127.0.0.1',
            'HEARTBEAT_SECONDS=1','OFFLINE_SECONDS=4'])+'\n',encoding='utf-8')
        try: env.chmod(0o600)
        except OSError: pass

    def compose(*command):
        process=subprocess.run(['docker','compose','-p',project,'--env-file',str(env),*command],
                               capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        if process.returncode:
            raise RuntimeError('Compose '+command[0]+' failed: '+process.stderr[-3000:])
        return process.stdout

    client=httpx.Client(base_url=f'http://127.0.0.1:{args.port}',timeout=45)
    def request(method,path,body=None,relay_role=False,files=None):
        response=client.request(method,'/api/v1'+path,json=body,files=files,
            headers={'Authorization':'Bearer '+(relay if relay_role else admin)})
        assert response.status_code==200,(path,response.status_code,response.text)
        return response.json()

    def wait_for(fn,seconds=120,interval=.2):
        until=time.monotonic()+seconds
        last=None
        while time.monotonic()<until:
            try:
                last=fn()
                if last:return last
            except (httpx.HTTPError,AssertionError): pass
            time.sleep(interval)
        raise AssertionError('Timed out waiting for condition; last='+str(last))

    def asset(asset_id):
        return next(a for a in request('GET','/assets')['assets'] if a['id']==asset_id)

    def send_report(version,target,seq):
        return dict(event_id=f"{target['device_id']}-{seq}",device_id=target['device_id'],report_seq=seq,
            observed_at=now(),assignment_revision=target['assignment_revision'],actual_version_id=version['id'],
            current_item_id=version['items'][0]['id'],playback_state='playing',pending_version_id=None,error=None)

    write_env(data)
    try:
        compose('config','--quiet'); compose('up','-d','--no-build')
        wait_for(lambda:client.get('/api/v1/health').status_code==200)
        print('Web health OK',flush=True)
        samples=generate(root/'samples')
        with (samples/'basic-layout.xlsx').open('rb') as f:
            uploaded=request('POST','/assets',files={'file':('basic-layout.xlsx',f)})
        wait_for(lambda:asset(uploaded['id'])['status']=='processing',interval=.03)
        compose('kill','-s','SIGKILL','worker')
        compose('up','-d','--no-build','worker')
        converted=wait_for(lambda: (a if a['status'] in ('ready','failed') else None) if (a:=asset(uploaded['id'])) else None)
        assert converted['status']=='ready',converted
        assert converted['conversion_job']['attempts']==2,converted
        result['checks'].append({'worker_sigkill_recovery':'passed','attempts':2,'page_count':converted['metadata']['page_count']})
        print('Actual worker SIGKILL and recovery OK',flush=True)
        version=request('POST','/playlists',{'name':'Compose 接口验证'})
        request('PUT',f"/versions/{version['id']}/items",{'items':[{'asset_id':uploaded['id']}]})
        version=request('POST',f"/versions/{version['id']}/publish")
        for screen in ('screen-a','screen-b'):request('POST','/devices',{'device_id':screen,'name':screen})
        payload={'request_id':'compose-first-request','playlist_version_id':version['id'],'device_ids':['screen-a','screen-b'],'timeout_seconds':10}
        first=request('POST','/deployments',payload)
        assert request('POST','/deployments',payload)['id']==first['id']
        conflict=client.post('/api/v1/deployments',headers={'Authorization':'Bearer '+admin},json=payload|{'request_id':'another-request'})
        assert conflict.status_code==409
        wait_for(lambda:any(t['id']==first['id'] and t['status']=='timed_out' for t in request('GET','/deployments')['tasks']),20)
        second_version=request('POST',f"/versions/{version['id']}/clone")
        second_version=request('POST',f"/versions/{second_version['id']}/publish")
        second=request('POST','/deployments',payload|{'request_id':'compose-second-request','playlist_version_id':second_version['id'],'timeout_seconds':120})
        request('POST','/relay/reports',{'relay_id':'workshop-1','reports':[send_report(version,first['targets'][1],1)]},True)
        old_manifest=version['manifest']
        compose('restart','web','worker')
        wait_for(lambda:client.get('/api/v1/health').status_code==200)
        snapshot=request('POST','/relay/sync',{'relay_id':'workshop-1'},True)
        assert all(a['playlist_version_id']==second_version['id'] for a in snapshot['assignments'])
        assert snapshot['assignments']==request('POST','/relay/sync',{'relay_id':'workshop-1'},True)['assignments']
        assert next(v for v in request('GET','/playlists')['versions'] if v['id']==version['id'])['manifest']==old_manifest
        acks=[{k:a[k] for k in ('device_id','assignment_revision')} for a in snapshot['assignments']]
        request('POST','/relay/acks',{'relay_id':'workshop-1','assignments':acks},True)
        assert request('GET','/devices')['summary']['pending']==2
        reports=[send_report(second_version,target,2) for target in second['targets']]
        request('POST','/relay/reports',{'relay_id':'workshop-1','reports':reports},True)
        assert next(t for t in request('GET','/deployments')['tasks'] if t['id']==second['id'])['status']=='succeeded'
        state=request('GET','/devices');assert state['summary']['online']==2 and state['summary']['pending']==0
        wait_for(lambda:request('GET','/devices')['summary']['unknown']==2,10)
        result['checks'].append({'HTTP_tasks_offline_latest_restart_status':'passed','device_count':2})
        print('Task exclusion, restart persistence and simulated relay reports OK',flush=True)
        compose('stop','web','worker')
        backup=root/'backup';shutil.copytree(data,backup)
        restored=root/'restored';shutil.copytree(backup,restored)
        conn=sqlite3.connect(restored/'center.sqlite3')
        try:
            assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            for path,size,digest in conn.execute('SELECT path,size_bytes,sha256 FROM files'):
                file=restored/path
                assert file.stat().st_size==size
                with file.open('rb') as stream:assert hashlib.file_digest(stream,'sha256').hexdigest()==digest
        finally:
            conn.close()
        write_env(restored)
        compose('up','-d','--no-build','--force-recreate')
        wait_for(lambda:client.get('/api/v1/health').status_code==200)
        state=request('GET','/devices')
        assert all(d['actual_version_id']==second_version['id'] and d['desired']['playlist_version_id']==second_version['id'] for d in state['devices'])
        wait_for(lambda:_healthy(compose),180)
        result['checks'].append({'stop_backup_empty_directory_restore':'passed','sqlite_integrity':'ok','file_hashes':'all match','health':'both healthy'})
        result['status']='passed'
        (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        if args.keep_running:print(f'Test instance retained at http://127.0.0.1:{args.port}/ ; keys in {env} (not printed)',flush=True)
    finally:
        client.close()
        if not args.keep_running:compose('down')


def _healthy(compose):
    # Compose emits one JSON object per line (some versions emit an array).
    raw=compose('ps','--format','json').strip()
    if not raw:return False
    rows=json.loads(raw) if raw.startswith('[') else [json.loads(line) for line in raw.splitlines()]
    return len(rows)==2 and all(r.get('Health')=='healthy' for r in rows)


if __name__=='__main__':main()
