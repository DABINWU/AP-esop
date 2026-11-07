from concurrent.futures import ThreadPoolExecutor

import pytest

from test_center import env, image_asset, published, register, deploy, timeout
from test_modules import task, upload, get_version


def clone(env,v):
    r=env[0].post('/api/v1/versions/'+v['id']+'/clone',headers=env[2]);assert r.status_code==200,r.text;return r.json()


def remove(env,v,headers=None):
    return env[0].delete('/api/v1/versions/'+v['id'],headers=env[2] if headers is None else headers)


def test_delete_last_version_and_auth_guards(env):
    v=task(env)
    assert remove(env,v,{}).status_code==401
    assert remove(env,v,env[3]).status_code==401
    r=remove(env,v)
    assert r.status_code==409 and r.json()['error']['code']=='last_version'
    assert '整个资料条目' in r.json()['error']['message']
    assert env[0].delete('/api/v1/versions/missing',headers=env[2]).status_code==404


def test_draft_version_delete_cleans_files_and_rejects_future_mutations(env):
    first=task(env);v=clone(env,first);asset=upload(env,v).json()
    r=remove(env,v)
    assert r.status_code==200 and not r.json()['retained_history'] and not r.json()['cleanup_pending']
    assert get_version(env,v)['items']==[] and get_version(env,v)['archived_at'].endswith('Z')
    assert env[0].get(asset['files'][0]['download_path'],headers=env[2]).status_code==404
    assert remove(env,v).status_code==200
    for suffix in ('publish','clone'):
        assert env[0].post('/api/v1/versions/'+v['id']+'/'+suffix,headers=env[2]).status_code==409
    assert env[0].put('/api/v1/versions/'+v['id']+'/items',headers=env[2],json={'items':[]}).status_code==409
    assert upload(env,v).status_code==409
    assert clone(env,first)['version']==3


def test_published_version_archive_retains_manifest_and_downloads(env):
    asset=image_asset(env);first=published(env,asset);clone(env,first)
    path=env[1].settings.data_dir/'manifests'/(first['id']+'.json');data=path.read_bytes()
    r=remove(env,first)
    assert r.status_code==200 and r.json()['retained_history']
    assert get_version(env,first)['manifest']==first['manifest'] and path.read_bytes()==data
    assert env[0].get(asset['files'][0]['download_path'],headers=env[2]).status_code==200
    assert env[0].delete('/api/v1/assets/'+asset['id'],headers=env[2]).status_code==409
    register(env)
    assert deploy(env,first).status_code==409
    assert env[0].post('/api/v1/versions/'+first['id']+'/clone',headers=env[2]).status_code==409


@pytest.mark.parametrize('kind',['desired','actual','pending'])
def test_version_delete_reports_specific_screen_reference(env,kind):
    first=published(env);clone(env,first);register(env)
    if kind=='desired':
        task_record=deploy(env,first).json();timeout(env[1],task_record['id'])
    else:
        column='actual_version_id' if kind=='actual' else 'pending_version_id'
        with env[1].write() as c:
            c.execute(f'UPDATE devices SET {column}=? WHERE device_id=?',(first['id'],'screen-a'))
    r=remove(env,first)
    assert r.status_code==409 and r.json()['error']['code']=='version_in_use'
    assert 'screen-a' in r.json()['error']['details']['device_ids']
    assert get_version(env,first)['archived_at'] is None


def test_conversion_in_progress_blocks_version_delete(env):
    from scripts.pdf_samples import sample_pdf
    first=task(env);v=clone(env,first)
    assert upload(env,v,'production','guide.pdf',sample_pdf()).json()['status']=='queued'
    r=remove(env,v)
    assert r.status_code==409 and r.json()['error']['code']=='version_processing'


def test_draft_shared_files_are_preserved_for_other_version(env):
    first=task(env);asset=upload(env,first).json();v=clone(env,first)
    assert remove(env,first).status_code==200
    assert get_version(env,v)['items']
    assert env[0].get(asset['files'][0]['download_path'],headers=env[2]).status_code==200


def test_concurrent_deletes_cannot_remove_every_version(env):
    first=task(env);second=clone(env,first)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda v:remove(env,v),[first,second]))
    assert sorted(r.status_code for r in responses)==[200,409]
    versions=env[0].get('/api/v1/playlists',headers=env[2]).json()['versions']
    assert sum(v['archived_at'] is None for v in versions)==1
