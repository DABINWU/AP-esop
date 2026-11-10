from pathlib import Path
import pytest

from relay.installer.bootstrap import configure, service_xml
from relay.config import read_env
from shared.auth import verify_password


def request():
    return dict(center_url='http://127.0.0.1:8080', center_key='center-'+'a'*32,
                admin_password='installer-test-password', relay_id='workshop-1')


def test_upgrade_preserves_keys_account_and_custom_settings(tmp_path):
    s=configure(tmp_path, request())
    first=read_env(tmp_path/'relay.env')
    assert len(s.device_key)>=24 and len(s.admin_key)>=24
    assert len({s.center_key,s.device_key,s.admin_key})==3
    with (tmp_path/'relay.env').open('a') as f: f.write('POLL_SECONDS=17\nDISK_RESERVE_MB=777\n')
    from shared.auth import Accounts
    account=Accounts(s.data_dir,'relay')
    with account.transaction() as c:
        digest=c.execute('SELECT password_hash FROM web_users WHERE username="admin"').fetchone()[0]
    assert verify_password(request()['admin_password'],digest)
    configure(tmp_path, {})
    second=read_env(tmp_path/'relay.env')
    assert all(second[k]==v for k,v in first.items())
    assert second['POLL_SECONDS']=='17' and second['DISK_RESERVE_MB']=='777'
    with account.transaction() as c:
        assert c.execute('SELECT password_hash FROM web_users WHERE username="admin"').fetchone()[0]==digest


@pytest.mark.parametrize('change',[{'center_key':'too-short'},{'port':'70000'},{'relay_id':'bad\nID'},{'admin_password':'short'}])
def test_bad_input_does_not_write_config(tmp_path,change):
    with pytest.raises(ValueError): configure(tmp_path,{**request(),**change})
    assert not (tmp_path/'relay.env').exists()


def test_upgrade_cannot_change_relay_identity(tmp_path):
    configure(tmp_path,request())
    before=(tmp_path/'relay.env').read_bytes()
    with pytest.raises(ValueError):configure(tmp_path,{'relay_id':'different-relay'})
    assert (tmp_path/'relay.env').read_bytes()==before


def test_service_paths_escaped_and_no_secrets():
    text=service_xml(Path('C:/Program Files/A&B'),Path('C:/ProgramData/A&B'),'AP-Test')
    assert '&amp;' in text and '<delayedAutoStart>true' in text
    assert 'relay.env' in text and 'API_KEY' not in text
