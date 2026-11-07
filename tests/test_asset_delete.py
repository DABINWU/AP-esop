from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from center.asset_delete import cleanup_deleting_assets
from center.db import Database, dumps
from scripts.pdf_samples import sample_pdf
from center.media import file_record, insert_file
from test_center import env, image_asset, png_bytes, published, register, deploy, report, send


def usage(env, asset):
    return env[0].get('/api/v1/assets/'+asset['id']+'/usage', headers=env[2])


def remove(env, asset, headers=None):
    return env[0].delete('/api/v1/assets/'+asset['id'], headers=env[2] if headers is None else headers)


def draft(env, asset):
    client, _, admin, _ = env
    version = client.post('/api/v1/playlists', headers=admin, json={'name':'装配指导'}).json()
    response = client.put('/api/v1/versions/'+version['id']+'/items', headers=admin,
                          json={'items':[{'asset_id':asset['id']}]})
    assert response.status_code == 200
    return response.json()


def test_delete_unused_removes_bytes_and_records_and_stale_choice_has_message(env):
    client, db, admin, _ = env
    asset = image_asset(env)
    with db.read() as c:
        path = db.settings.data_dir / c.execute('SELECT path FROM files WHERE asset_id=?', (asset['id'],)).fetchone()[0]
    assert usage(env, asset).json()['can_delete']
    assert remove(env, asset, {}).status_code == 401
    assert remove(env, asset, env[3]).status_code == 401
    assert client.get('/api/v1/assets/'+asset['id']+'/usage', headers=env[3]).status_code == 401
    response = remove(env, asset)
    assert response.status_code == 200 and response.json()['deleted']
    assert not path.exists() and not client.get('/api/v1/assets', headers=admin).json()['assets']
    download = client.get(asset['files'][0]['download_path'], headers=admin)
    assert download.status_code == 404 and '删除' in download.json()['error']['message']
    assert remove(env, asset).status_code == 404
    version = client.post('/api/v1/playlists', headers=admin, json={'name':'未保存的旧选择'}).json()
    for item in ({'asset_id':asset['id']}, {'file_id':asset['files'][0]['id']}):
        response = client.put('/api/v1/versions/'+version['id']+'/items', headers=admin, json={'items':[item]})
        assert response.status_code == 404 and '重新选择' in response.json()['error']['message']


def test_saved_draft_blocks_delete_until_removed_and_saved(env):
    asset = image_asset(env)
    version = draft(env, asset)
    inspection = usage(env, asset).json()
    assert not inspection['can_delete'] and inspection['references'][0]['playlist_version_id'] == version['id']
    response = remove(env, asset)
    assert response.status_code == 409
    error = response.json()['error']
    assert error['code'] == 'asset_in_use' and '装配指导' in error['message'] and '草稿' in error['message']
    assert error['details']['references'][0]['version'] == 1
    assert env[0].get(asset['files'][0]['download_path'], headers=env[2]).status_code == 200
    assert env[0].put('/api/v1/versions/'+version['id']+'/items', headers=env[2], json={'items':[]}).status_code == 200
    assert remove(env, asset).status_code == 200


def test_published_and_history_refs_preserve_manifest_tasks_and_downloads(env):
    client, db, admin, relay = env
    asset = image_asset(env)
    version = published(env, asset)
    register(env)
    task = deploy(env, version).json()
    assert send(env, [report(version, task['targets'][0])]).status_code == 200
    inspection = usage(env, asset).json()
    ref = inspection['references'][0]
    assert ref['desired_device_ids'] == ['screen-a','screen-b']
    assert ref['actual_device_ids'] == ['screen-a'] and ref['task_count'] == 1
    with db.read() as c:
        snapshot = [tuple(row) for row in c.execute('SELECT * FROM assignments ORDER BY device_id')]
        tasks = [tuple(row) for row in c.execute('SELECT * FROM tasks')]
    response = remove(env, asset)
    assert response.status_code == 409 and 'screen-a' in response.json()['error']['message']
    assert client.post('/api/v1/versions/'+version['id']+'/publish', headers=admin).json()['manifest'] == version['manifest']
    assert client.get(asset['files'][0]['download_path'], headers=relay).status_code == 200
    with db.read() as c:
        assert [tuple(row) for row in c.execute('SELECT * FROM assignments ORDER BY device_id')] == snapshot
        assert [tuple(row) for row in c.execute('SELECT * FROM tasks')] == tasks
    # Even a published version with no current task/assignment blocks historical deletion.
    another = image_asset(env); published(env, another)
    assert remove(env, another).status_code == 409


def test_manifest_references_also_checked_independently(env):
    asset = image_asset(env)
    # Corrupt legacy data may have lost items but retained an immutable published manifest.
    version = draft(env, asset)
    with env[1].write() as c:
        c.execute('UPDATE versions SET manifest=?,items=? WHERE id=?', (dumps({'items':version['items']}), '[]', version['id']))
    assert remove(env, asset).status_code == 409


@pytest.mark.parametrize('running', [False, True])
def test_converting_asset_cannot_be_deleted(env, running):
    client, db, admin, _ = env
    asset = client.post('/api/v1/assets', headers=admin, files={'file':('guide.pdf',sample_pdf())}).json()
    if running:
        with db.write() as c:
            c.execute("UPDATE assets SET status='processing' WHERE id=?", (asset['id'],))
            c.execute("UPDATE conversion_jobs SET status='running',attempts=1 WHERE asset_id=?", (asset['id'],))
    response = remove(env, asset)
    assert response.status_code == 409 and response.json()['error']['code'] == 'asset_processing'
    assert '转换' in response.json()['error']['message']
    assert client.get(asset['files'][0]['download_path'], headers=admin).status_code == 200


def test_failed_document_deletes_original_job_and_orphan_derivatives(env):
    client, db, admin, _ = env
    asset = client.post('/api/v1/assets', headers=admin, files={'file':('broken.pdf',b'%PDF-invalid')}).json()
    with db.write() as c:
        c.execute("UPDATE assets SET status='failed' WHERE id=?", (asset['id'],))
        c.execute("UPDATE conversion_jobs SET status='failed' WHERE asset_id=?", (asset['id'],))
    derived = db.settings.data_dir/'derived'/asset['id']; derived.mkdir()
    (derived/'uncommitted-page.png').write_bytes(b'orphan fixture')
    temporary = db.settings.data_dir/'tmp'/asset['conversion_job']['id']; temporary.mkdir()
    (temporary/'source-copy.pdf').write_bytes(b'copy left by an interrupted conversion')
    assert remove(env, asset).status_code == 200
    assert not derived.exists() and not temporary.exists()
    with db.read() as c:
        assert c.execute('SELECT COUNT(*) FROM conversion_jobs').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM files').fetchone()[0] == 0


def test_cleanup_failure_is_not_playable_and_recovers_after_restart(env, monkeypatch):
    client, db, admin, _ = env
    asset = image_asset(env)
    original_unlink = Path.unlink
    def occupied(path, *args, **kwargs):
        if path.stem == asset['id']:
            raise PermissionError('Fixture: file in use')
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', occupied)
        response = remove(env, asset)
    assert response.status_code == 503 and response.json()['error']['code'] == 'asset_delete_failed'
    pending = client.get('/api/v1/assets', headers=admin).json()['assets'][0]
    assert pending['status'] == 'deleting' and '删除未完成' in pending['error']
    assert pending['metadata']['delete_requested_at'].endswith('Z')
    assert client.get(asset['files'][0]['download_path'], headers=admin).status_code == 404
    version = client.post('/api/v1/playlists', headers=admin, json={'name':'不能使用半删除资料'}).json()
    assert client.put('/api/v1/versions/'+version['id']+'/items', headers=admin,
                      json={'items':[{'asset_id':asset['id']}]}).status_code == 409
    cleanup_deleting_assets(Database(db.settings))
    assert client.get('/api/v1/assets', headers=admin).json()['assets'] == []


def test_cleanup_rejects_path_outside_asset_scope(env):
    asset = image_asset(env)
    other = image_asset(env)
    with env[1].write() as c:
        c.execute('UPDATE files SET path=? WHERE asset_id=?', ('originals/'+other['id']+'.foreign', asset['id']))
    assert remove(env, asset).status_code == 503
    assert env[0].get(other['files'][0]['download_path'], headers=env[2]).status_code == 200


def test_partial_file_cleanup_retries_without_exposing_broken_asset(env, monkeypatch):
    client, db, admin, _ = env
    asset = client.post('/api/v1/assets', headers=admin, files={'file':('guide.pdf',sample_pdf())}).json()
    derived = db.settings.data_dir/'derived'/asset['id']; derived.mkdir()
    pdf, page = derived/'document.pdf', derived/'page-0001.png'
    pdf.write_bytes(sample_pdf()); page.write_bytes(png_bytes())
    with db.write() as c:
        c.execute("UPDATE assets SET status='ready' WHERE id=?", (asset['id'],))
        c.execute("UPDATE conversion_jobs SET status='succeeded' WHERE asset_id=?", (asset['id'],))
        insert_file(c, file_record(pdf, db.settings.data_dir, asset['id'], 'pdf', 'application/pdf'))
        insert_file(c, file_record(page, db.settings.data_dir, asset['id'], 'page', 'image/png', 1))
        paths = [db.settings.data_dir/r[0] for r in c.execute('SELECT path FROM files WHERE asset_id=?',(asset['id'],))]
    original_unlink = Path.unlink
    count = 0
    def partial(path, *args, **kwargs):
        nonlocal count
        if path in paths:
            count += 1
            if count == 2: raise PermissionError('Fixture: cleanup interrupted after first file')
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', partial)
        assert remove(env, asset).status_code == 503
    assert sum(path.exists() for path in paths) == 2
    pending = client.get('/api/v1/assets', headers=admin).json()['assets'][0]
    assert pending['status'] == 'deleting'
    assert all(client.get(f['download_path'], headers=admin).status_code == 404 for f in pending['files'])
    assert remove(env, asset).status_code == 200
    assert all(not path.exists() for path in paths)


def test_delete_and_draft_save_are_serialized(env):
    client, db, admin, _ = env
    asset = image_asset(env)
    version = client.post('/api/v1/playlists', headers=admin, json={'name':'并发草稿'}).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(client.put, '/api/v1/versions/'+version['id']+'/items', headers=admin,
                            json={'items':[{'asset_id':asset['id']}]})
        deleted = pool.submit(remove, env, asset)
        saved, deleted = saved.result(), deleted.result()
    assert ((saved.status_code == 200 and deleted.status_code == 409)
            or (deleted.status_code == 200 and saved.status_code in (404,409)))
