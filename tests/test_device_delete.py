from test_center import env, published, register, deploy, timeout, report, send


def test_deleted_screen_actual_state_does_not_block_playlist_archive(env):
    c,db,admin,_=env
    register(env,('screen00',))
    v=published(env)
    task=deploy(env,v,ids=('screen00',)).json()
    assert send(env,[report(v,task['targets'][0])]).status_code==200
    path='/api/v1/playlists/'+v['playlist_id']
    assert c.delete(path,headers=admin).status_code==409
    assert c.delete('/api/v1/devices/screen00',headers=admin).status_code==200
    db.initialize()
    response=c.delete(path,headers=admin)
    assert response.status_code==200,response.text
    assert response.json()['retained_history']
    with db.read() as conn:
        assert conn.execute('SELECT actual_version_id FROM devices WHERE device_id="screen00"').fetchone()[0]==v['id']
        assert conn.execute('SELECT COUNT(*) FROM events WHERE device_id="screen00"').fetchone()[0]==1


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
