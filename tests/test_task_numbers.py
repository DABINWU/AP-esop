import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from center.db import Database, next_task_number
from test_center import env


def create(env, name='自动编号测试'):
    response=env[0].post('/api/v1/playlists',headers=env[2],json={'name':name})
    assert response.status_code==200,response.text
    return response.json()


def sequence(number):
    assert re.fullmatch(r'ESOP-\d{8}-\d{6,}',number)
    return int(number.rsplit('-',1)[1])


def test_concurrent_creation_returns_unique_persisted_numbers(env):
    with ThreadPoolExecutor(max_workers=4) as pool:
        versions=list(pool.map(lambda i:create(env,str(i)),range(12)))
    numbers=[v['task_number'] for v in versions]
    assert len(set(numbers))==12
    assert sorted(sequence(n) for n in numbers)==list(range(1,13))
    saved={p['id']:p['task_number'] for p in env[0].get('/api/v1/playlists',headers=env[2]).json()['playlists']}
    assert all(saved[v['playlist_id']]==v['task_number'] for v in versions)


def test_number_survives_restart_and_is_not_reused_after_archive(env):
    first=create(env)
    assert env[0].delete('/api/v1/playlists/'+first['playlist_id'],headers=env[2]).status_code==200
    Database(env[1].settings).initialize()
    second=create(env)
    assert sequence(second['task_number'])>sequence(first['task_number'])
    assert first['task_number']!=second['task_number']


def test_manual_number_is_rejected_and_cloning_does_not_create_task_number(env):
    response=env[0].post('/api/v1/playlists',headers=env[2],json={'name':'错误手填','task_number':'CUSTOM'})
    assert response.status_code==422
    first=create(env)
    clone=env[0].post('/api/v1/versions/'+first['id']+'/clone',headers=env[2])
    assert clone.status_code==200 and clone.json()['playlist_id']==first['playlist_id']
    second=create(env)
    assert sequence(second['task_number'])==sequence(first['task_number'])+1


def test_v2_migration_backfills_blanks_and_preserves_existing_numbers(env):
    db=env[1]
    with db.write() as c:
        c.execute('UPDATE schema_version SET version=2')
        c.execute("DELETE FROM counters WHERE name='task_number'")
        for id,number in [('blank',''),('whitespace','  '),('manual','CUSTOM-7'),('existing','ESOP-20260101-000042')]:
            c.execute('INSERT INTO playlists(id,name,task_number) VALUES(?,?,?)',(id,id,number))
    db.initialize()
    with db.read() as c:
        saved={r['id']:r['task_number'] for r in c.execute('SELECT * FROM playlists')}
        assert c.execute('SELECT version FROM schema_version').fetchone()[0]==4
    assert saved['manual']=='CUSTOM-7' and saved['existing']=='ESOP-20260101-000042'
    assert sequence(saved['blank'])==43 and sequence(saved['whitespace'])==44
    db.initialize()
    assert sequence(create(env)['task_number'])==45


def test_task_number_date_uses_shanghai_and_sequence_continues_next_day(env):
    with env[1].write() as c:
        first=next_task_number(c,datetime(2026,11,6,15,59,tzinfo=timezone.utc))
        second=next_task_number(c,datetime(2026,11,6,16,1,tzinfo=timezone.utc))
    assert first=='ESOP-20261106-000001'
    assert second=='ESOP-20261107-000002'
