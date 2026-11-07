import hashlib
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from center.app import create_app as center_app
from center.config import Settings as CenterSettings
from relay.app import create_app as relay_app
from relay.config import Settings as RelaySettings
from test_center import png_bytes


def application(root,kind,secure=False):
    if kind=='center':
        return center_app(CenterSettings(data_dir=root,admin_key='test-admin-'+'a'*30,relay_key='test-relay-'+'b'*30,auth_cookie_secure=secure))
    return relay_app(RelaySettings(data_dir=root,center_url='http://center.test',center_key='test-center-'+'a'*30,
        device_key='test-device-'+'b'*30,admin_key='test-local-'+'c'*30,auth_cookie_secure=secure),background=False)


def credentials(root):
    return dict(line.split('=',1) for line in (root/'initial-login.txt').read_text(encoding='utf-8').splitlines())


def login(client,body):
    return client.post('/api/v1/auth/login',json=body,headers={'X-ESOP-Login':'1'})


@pytest.fixture(params=['center','relay'])
def account(request,tmp_path):
    root=tmp_path/request.param
    app=application(root,request.param)
    with TestClient(app) as client:
        yield request.param,root,app,client,credentials(root)


def test_login_refresh_logout_cookie_and_hashes(account):
    kind,root,app,client,creds=account
    assert client.get('/api/v1/auth/session').status_code==401
    assert client.post('/api/v1/auth/login',json=creds).status_code==403
    assert login(client,{**creds,'password':'incorrect-password'}).status_code==401
    response=login(client,creds);assert response.status_code==200
    cookie=response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie and 'path=/api/v1' in cookie and 'max-age=43200' in cookie
    assert 'secure;' not in cookie
    data=response.json();assert data['username']=='admin'
    assert client.get('/api/v1/auth/session').json()==data
    path='/api/v1/settings' if kind=='center' else '/api/v1/local/status'
    assert client.get(path).status_code==200
    with sqlite3.connect(root/'web-auth.sqlite3') as db:
        stored=db.execute('SELECT password_hash FROM web_users').fetchone()[0]
        session_hash=db.execute('SELECT token_hash FROM web_sessions').fetchone()[0]
    assert stored.startswith('scrypt$') and creds['password'] not in stored
    raw=client.cookies.get(app.state.accounts.cookie)
    assert session_hash==hashlib.sha256(raw.encode()).hexdigest()
    assert client.post('/api/v1/auth/logout').status_code==403
    assert client.post('/api/v1/auth/logout',headers={'X-CSRF-Token':data['csrf_token']}).status_code==200
    assert client.get(path).status_code==401
    assert client.get('/api/v1/auth/session').status_code==401


def test_password_change_revokes_sessions_and_removes_initial_file(account):
    kind,root,app,client,creds=account
    first=login(client,creds).json()
    old_cookie=client.cookies.get(app.state.accounts.cookie)
    response=client.post('/api/v1/auth/password',json={'current_password':creds['password'],'new_password':'new-password-1234'},headers={'X-CSRF-Token':first['csrf_token']})
    assert response.status_code==200
    assert response.json()['csrf_token']!=first['csrf_token']
    assert not (root/'initial-login.txt').exists()
    other=TestClient(app)
    try:
        other.cookies.set(app.state.accounts.cookie,old_cookie,path='/api/v1')
        assert other.get('/api/v1/auth/session').status_code==401
        assert login(other,creds).status_code==401
        assert login(other,{'username':'admin','password':'new-password-1234'}).status_code==200
    finally:other.close()


def test_csrf_origin_expiry_and_restart_persistence(account):
    kind,root,app,client,creds=account
    assert client.post('/api/v1/auth/login',json=creds,headers={'X-ESOP-Login':'1','Origin':'http://foreign.test'}).status_code==403
    data=login(client,creds).json()
    token=client.cookies.get(app.state.accounts.cookie)
    restarted=application(root,kind)
    restarted.state.accounts.initialize()
    after=TestClient(restarted)
    try:
        after.cookies.set(app.state.accounts.cookie,token,path='/api/v1')
        assert after.get('/api/v1/auth/session').json()['csrf_token']==data['csrf_token']
        assert after.post('/api/v1/auth/logout',headers={'X-CSRF-Token':data['csrf_token'],'Origin':'http://foreign.test'}).status_code==403
        with sqlite3.connect(root/'web-auth.sqlite3') as db:db.execute('UPDATE web_sessions SET expires_at=?',(time.time()-1,))
        assert after.get('/api/v1/auth/session').status_code==401
    finally:after.close()


def test_rate_limit_persists_and_recovery_is_local(account):
    kind,root,app,client,creds=account
    for _ in range(10):assert login(client,{'username':'unknown','password':'wrong-password'}).status_code==401
    assert login(client,creds).status_code==429
    restarted=application(root,kind)
    restarted.state.accounts.initialize()
    after=TestClient(restarted)
    try:
        assert login(after,creds).status_code==429
    finally:after.close()
    app.state.accounts.reset('operator','replacement-password-123')
    assert login(client,{'username':'operator','password':'replacement-password-123'}).status_code==200
    assert client.post('/api/v1/auth/reset',json={}).status_code==404


def test_secure_cookies(tmp_path):
    app=application(tmp_path/'secure','center',secure=True)
    with TestClient(app,base_url='https://testserver') as client:
        r=login(client,credentials(tmp_path/'secure'))
        assert 'Secure' in r.headers['set-cookie']
        assert client.get('/api/v1/auth/session').status_code==200


def test_cookie_upload_csrf_before_multipart_and_bearer_compatibility(tmp_path):
    app=application(tmp_path/'center','center')
    with TestClient(app) as client:
        csrf=login(client,credentials(tmp_path/'center')).json()['csrf_token']
        assert client.post('/api/v1/assets',files={'file':('file.png',png_bytes())}).status_code==403
        r=client.post('/api/v1/assets',files={'file':('file.png',png_bytes())},headers={'X-CSRF-Token':csrf})
        assert r.status_code==200
        assert client.get(r.json()['files'][0]['download_path']).status_code==200
        assert client.post('/api/v1/playlists',json={'name':'CSRF blocked'}).status_code==403
        assert client.post('/api/v1/playlists',json={'name':'Account-created'},headers={'X-CSRF-Token':csrf}).status_code==200
        assert client.post('/api/v1/relay/sync',json={'relay_id':'workshop-1'},headers={'X-CSRF-Token':csrf}).status_code==401
        assert client.post('/api/v1/relay/sync',json={'relay_id':'workshop-1'},headers={'Authorization':'Bearer '+app.state.settings.relay_key}).status_code==200
        assert client.get('/api/v1/settings',headers={'Authorization':'Bearer '+app.state.settings.admin_key}).status_code==200


def test_center_relay_cookie_and_device_roles_are_separate(tmp_path):
    center=application(tmp_path/'center','center');relay=application(tmp_path/'relay','relay')
    with TestClient(center) as a,TestClient(relay) as b:
        login(a,credentials(tmp_path/'center'))
        for cookie in a.cookies.jar:b.cookies.set(cookie.name,cookie.value,path=cookie.path)
        assert b.get('/api/v1/local/status').status_code==401
        login(b,credentials(tmp_path/'relay'))
        assert b.get('/api/v1/local/status').status_code==200
        assert b.post('/api/v1/device/sync',json={'device_id':'screen-a','known_assignment_revision':0}).status_code==401
