from test_center import env, published, register, deploy, timeout, report, send


def test_delete_preserves_history_blocks_running_and_survives_restart(env):
    c,db,admin,relay=env
    register(env)
    v=published(env)
    task=deploy(env,v).json()
    path='/api/v1/devices/screen-a'
    assert c.delete(path).status_code==401
    assert c.delete(path,headers=admin).status_code==409
    timeout(db,task['id'])
    assert c.delete(path,headers=admin).json()['retained_history']
    assert c.delete(path,headers=admin).status_code==200
    db.initialize()
    status=c.get('/api/v1/devices',headers=admin).json()
    assert [d['device_id'] for d in status['devices']]==['screen-b']
    assert status['summary']['registered']==1
    snapshot=c.post('/api/v1/relay/sync',headers=relay,json={'relay_id':db.settings.relay_id}).json()
    assert all(d['device_id']!='screen-a' for d in snapshot['devices']+snapshot['assignments']+snapshot['assignment_history'])
    assert c.post('/api/v1/devices',headers=admin,json={'device_id':'screen-a','name':'again'}).status_code==409
    assert c.post('/api/v1/deployments',headers=admin,json={'request_id':'new-after-delete','playlist_version_id':v['id'],'device_ids':['screen-a'],'timeout_seconds':120}).status_code==404
    with db.read() as conn:
        assert conn.execute('SELECT COUNT(*) FROM task_targets WHERE device_id="screen-a"').fetchone()[0]==1
        assert conn.execute('SELECT COUNT(*) FROM assignments WHERE device_id="screen-a"').fetchone()[0]==0
    target=next(t for t in task['targets'] if t['device_id']=='screen-a')
    r=send(env,[report(v,target)]).json()
    assert r['results'][0]['status']=='stale'
