import pytest

from center.worker import process_one, recover_jobs
from center.db import Database
from scripts.pdf_samples import sample_pdf
from test_center import env


@pytest.mark.parametrize('mode', [None, 'print', 'screen_16_9'])
def test_pdf_queue_mode_original_and_recovery(env, mode):
    client, db, admin, relay = env
    data = {} if mode is None else {'render_mode': mode}
    content = sample_pdf()
    response = client.post('/api/v1/assets', headers=admin, data=data,
                           files={'file': ('guide.pdf', content, 'application/pdf')})
    assert response.status_code == 200, response.text
    asset = response.json()
    assert asset['kind'] == 'pdf' and asset['status'] == 'queued'
    assert asset['metadata']['render_mode'] == (mode or 'screen_16_9')
    file = asset['files'][0]
    assert file['role'] == 'original'
    assert client.get(file['download_path'], headers=admin).content == content
    assert client.get(file['download_path'], headers=relay).content == content
    assert client.get(file['download_path']).status_code == 401
    with db.write() as c:
        c.execute("UPDATE conversion_jobs SET status='running',attempts=1 WHERE asset_id=?", (asset['id'],))
    recover_jobs(Database(db.settings))
    recovered = client.get('/api/v1/assets', headers=admin).json()['assets'][0]
    assert recovered['conversion_job']['status'] == 'queued'
    assert recovered['metadata'] == asset['metadata']
    version = client.post('/api/v1/playlists', headers=admin, json={'name':'PDF'}).json()
    assert client.put('/api/v1/versions/'+version['id']+'/items', headers=admin,
                      json={'items':[{'asset_id':asset['id']}]}).status_code == 409


def test_disguised_pdf_rejected_without_job(env):
    client, db, admin, _ = env
    response = client.post('/api/v1/assets', headers=admin, files={'file':('guide.pdf', b'not a pdf')})
    assert response.status_code == 415
    assert client.get('/api/v1/assets', headers=admin).json()['assets'] == []
    assert not list((db.settings.data_dir / 'originals').iterdir())


def test_encrypted_pdf_fails_without_libreoffice_or_publish(env, monkeypatch):
    client, db, admin, _ = env
    asset = client.post('/api/v1/assets', headers=admin, files={'file':('locked.pdf',sample_pdf())}).json()
    def command(args, timeout):
        assert str(args[0]) == db.settings.pdfinfo
        return b'Pages: 1\nEncrypted: yes (print:yes)\n'
    monkeypatch.setattr('center.worker.run_command', command)
    assert process_one(db)
    failed = client.get('/api/v1/assets', headers=admin).json()['assets'][0]
    assert failed['id'] == asset['id'] and failed['status'] == 'failed'
    assert '加密' in failed['error']
    assert all(f['role'] == 'original' for f in failed['files'])
