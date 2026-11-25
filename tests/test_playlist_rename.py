from test_center import env,published


def test_rename_keeps_manifest_number_and_clones_new_title(env):
    c,db,admin,_=env
    v=published(env);path='/api/v1/playlists/'+v['playlist_id']
    old=c.get('/api/v1/playlists',headers=admin).json()['playlists'][0]
    assert c.patch(path,json={'name':'new'}).status_code==401
    assert c.patch(path,headers=admin,json={'name':'   '}).status_code==422
    r=c.patch(path,headers=admin,json={'name':'New title','description':'New note'})
    assert r.status_code==200 and r.json()['task_number']==old['task_number']
    with db.read() as conn:
        import json
        assert json.loads(conn.execute('SELECT manifest FROM versions WHERE id=?',(v['id'],)).fetchone()[0])==v['manifest']
    draft=c.post('/api/v1/versions/'+v['id']+'/clone',headers=admin).json()
    assert draft['name']=='New title'
    assert c.patch(path,headers=admin,json={'name':'Latest'}).status_code==200
    with db.read() as conn:
        assert conn.execute('SELECT name FROM versions WHERE id=?',(draft['id'],)).fetchone()[0]=='Latest'
