"""Elevated, isolated real Windows installer/SCM smoke test; never touches live service/data."""
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx
from PIL import Image


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def wait(check, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = check()
            if result: return result
        except (OSError, httpx.HTTPError): pass
        time.sleep(.5)
    raise AssertionError('Isolated installer validation timed out')


def main():
    root = Path(__file__).resolve().parents[1]
    run = root / 'test-output/relay-installer-validation' / str(time.time_ns())
    run.mkdir(parents=True)
    program, data = run / 'program', run / 'data'
    center_port, relay_port = port(), port()
    sid = 'AP-ESOP-SetupTest-' + run.name[-9:]
    center_key, admin_key = secrets.token_hex(32), secrets.token_hex(32)
    device_key, password = secrets.token_hex(32), secrets.token_hex(20)
    env = run / 'center.env'
    env.write_text(f'ADMIN_API_KEY={admin_key}\nRELAY_API_KEY={center_key}\nESOP_BIND_IP=127.0.0.1\nESOP_PORT={center_port}\nESOP_DATA_DIR={(run / "center").as_posix()}\n')
    subprocess.run(['icacls', str(run), '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'], check=True, capture_output=True)
    project = 'esop-setup-' + run.name[-9:]
    compose = ['docker', 'compose', '--env-file', str(env), '-p', project]
    exe = root / 'relay/dist/AP-ESOP-Relay-1.0.0-Setup.exe'
    checks = []
    def command(args):
        subprocess.run(args, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    def install(request):
        response = run / 'request.json'
        response.write_text(json.dumps(request), encoding='utf-8')
        command([str(exe), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/DIR=' + str(program),
            '/DATAROOT=' + str(data), '/SERVICEID=' + sid, '/REQUESTFILE=' + str(response), '/LOG=' + str(run / 'setup.log')])
        response.unlink()
    try:
        command(compose + ['up', '-d', '--no-build'])
        with httpx.Client(base_url=f'http://127.0.0.1:{center_port}', headers={'Authorization': 'Bearer ' + admin_key}, trust_env=False) as center, \
             httpx.Client(base_url=f'http://127.0.0.1:{relay_port}', headers={'Authorization': 'Bearer ' + device_key}, trust_env=False) as terminal:
            wait(lambda: center.get('/api/v1/health').status_code == 200)
            request = dict(center_url=str(center.base_url).rstrip('/'), center_key=center_key,
                device_key=device_key, admin_password=password, host='127.0.0.1', port=str(relay_port),
                relay_id='workshop-1', firewall=True)
            install(request)
            assert terminal.get('/api/v1/health').status_code == 200
            assert not (data / 'install-request.json').exists()
            assert (program / 'python/python.exe').exists()
            checks.append('offline runtime packaged; actual EXE installs automatic WinSW SCM service; health OK; transient secret request removed')
            with httpx.Client(base_url=str(terminal.base_url), trust_env=False) as browser:
                assert browser.post('/api/v1/auth/login', headers={'X-ESOP-Login': '1'}, json={'username': 'admin', 'password': password}).status_code == 200
            command(['powershell.exe', '-NoProfile', '-Command',
                "$s=Get-CimInstance Win32_Service -Filter \"Name='" + sid + "'\"; if($s.StartMode -ne 'Auto' -or $s.StartName -ne 'LocalSystem' -or $s.State -ne 'Running'){exit 1}"])
            checks.append('administrator password login and LocalSystem Automatic service verified')
            command(['powershell.exe','-NoProfile','-Command',
                "$r=Get-NetFirewallRule -DisplayName '"+sid+"-LAN'; $a=$r | Get-NetFirewallAddressFilter; $p=$r | Get-NetFirewallPortFilter; if($a.RemoteAddress -ne 'LocalSubnet' -or $p.LocalPort -ne '"+str(relay_port)+"'){exit 1}"])
            def post(path, body):
                r = center.post('/api/v1/' + path, json=body); r.raise_for_status(); return r.json()
            post('devices', {'device_id': 'setup-screen', 'name': 'Installer test'})
            image = io.BytesIO(); Image.new('RGB', (80, 40), 'blue').save(image, format='PNG')
            r = center.post('/api/v1/assets', files={'file': ('setup.png', image.getvalue(), 'image/png')}); r.raise_for_status()
            v = post('playlists', {'name': 'Installer validation'})
            r = center.put('/api/v1/versions/' + v['id'] + '/items', json={'items': [{'asset_id': r.json()['id']}]}); r.raise_for_status()
            v = post('versions/' + v['id'] + '/publish', None)
            task = post('deployments', {'request_id': 'setup-001', 'playlist_version_id': v['id'], 'device_ids': ['setup-screen'], 'timeout_seconds': 10})
            def sync():
                r = terminal.post('/api/v1/device/sync', json={'device_id': 'setup-screen', 'known_assignment_revision': 0})
                r.raise_for_status(); return r.json()
            wait(lambda: sync()['ready'])
            file = v['manifest']['items'][0]['file']
            r = terminal.get(file['download_path']); r.raise_for_status()
            assert hashlib.sha256(r.content).hexdigest() == file['sha256']
            assert httpx.get(str(terminal.base_url).rstrip('/') + file['download_path'], trust_env=False).status_code == 401
            checks.append('real center assignment -> installed SCM relay -> verified SHA media download and authentication')
            command(compose + ['stop', 'web'])
            report = dict(event_id='setup-report-1', device_id='setup-screen', report_seq=1,
                observed_at='2026-11-10T00:00:00.000Z', assignment_revision=task['targets'][0]['assignment_revision'],
                actual_version_id=v['id'], current_item_id=v['manifest']['items'][0]['id'], playback_state='paused',
                pending_version_id=None, error=None)
            r = terminal.post('/api/v1/device/reports', json={'report': report}); r.raise_for_status()
            from relay.config import read_env
            before = read_env(data / 'relay.env')
            # Reinstall the real EXE with empty fields: persistent keys/accounts/outbox must survive.
            install(dict(firewall=True))
            after = read_env(data / 'relay.env')
            assert before == after
            assert sync()['ready']
            command(compose + ['start', 'web'])
            wait(lambda: center.get('/api/v1/devices').json()['devices'][0]['report_seq'] == 1)
            checks.append('actual EXE upgrade while center offline keeps settings/cache/revision/outbox; original report delivered after reconnect')
            with httpx.Client(base_url=str(terminal.base_url), trust_env=False) as browser:
                assert browser.post('/api/v1/auth/login', headers={'X-ESOP-Login': '1'}, json={'username': 'admin', 'password': password}).status_code == 200
            uninstall = program / 'unins000.exe'
            command([str(uninstall), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'])
            assert (data / 'relay.env').exists() and (data / 'relay-data/relay.sqlite3').exists()
            assert subprocess.run(['sc.exe', 'query', sid], capture_output=True).returncode != 0
            command(['powershell.exe','-NoProfile','-Command',
                "if(Get-NetFirewallRule -DisplayName '"+sid+"-LAN' -ErrorAction SilentlyContinue){exit 1}"])
            checks.append('actual uninstall removes service and firewall rule; retains configuration, database and verified cache')
            result = dict(kind='actual Windows installer EXE + SCM + real Docker center HTTP; terminal requests simulated, no Android hardware',
                checks=checks, installer_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
            (run / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            print(json.dumps(result, indent=2), flush=True)
    finally:
        wrapper = program / 'service' / (sid + '.exe')
        if wrapper.exists():
            subprocess.run([str(wrapper), 'stop'], capture_output=True)
            subprocess.run([str(wrapper), 'uninstall'], capture_output=True)
        subprocess.run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule', 'name=' + sid + '-LAN'], capture_output=True)
        subprocess.run(compose + ['down'], capture_output=True)


if __name__ == '__main__': main()
