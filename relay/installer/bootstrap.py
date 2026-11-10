"""Privileged local installation helper. Never writes secrets to stdout or arguments."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from xml.sax.saxutils import escape

from relay.config import Settings, read_env
from shared.auth import Accounts, restrict_file


def run(args, required=True):
    p = subprocess.run(args, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if required and p.returncode:
        raise RuntimeError('System operation failed: ' + Path(args[0]).name)
    return p.returncode


def secure(root):
    root.mkdir(parents=True, exist_ok=True)
    run(['icacls', str(root), '/inheritance:r', '/grant:r', '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'])


def configure(root, request):
    """Validate before writing; preserve keys, tunables and identity on upgrade."""
    root = Path(root)
    path = root / 'relay.env'
    old = read_env(path) if path.exists() else {}
    values = dict(old)
    fields = {'center_url': 'CENTER_URL', 'center_key': 'CENTER_API_KEY', 'device_key': 'DEVICE_API_KEY',
              'relay_id': 'RELAY_ID', 'host': 'RELAY_HOST', 'port': 'RELAY_PORT'}
    for field, key in fields.items():
        if str(request.get(field, '')).strip():
            values[key] = str(request[field]).strip()
    values.setdefault('DEVICE_API_KEY', secrets.token_hex(32))
    values.setdefault('LOCAL_ADMIN_API_KEY', secrets.token_hex(32))
    values.setdefault('RELAY_ID', 'workshop-1')
    values.setdefault('RELAY_HOST', '0.0.0.0')
    values.setdefault('RELAY_PORT', '18081')
    values.setdefault('RELAY_DATA_DIR', str(root / 'relay-data'))
    if old and values['RELAY_ID'] != old.get('RELAY_ID', 'workshop-1'):
        raise ValueError('Upgrade cannot change relay_id')
    if any('\n' in v or '\r' in v for v in values.values()):
        raise ValueError('Multiline settings rejected')
    settings = Settings(data_dir=values['RELAY_DATA_DIR'], center_url=values['CENTER_URL'].rstrip('/'),
        center_key=values['CENTER_API_KEY'], device_key=values['DEVICE_API_KEY'], admin_key=values['LOCAL_ADMIN_API_KEY'],
        relay_id=values['RELAY_ID'], host=values['RELAY_HOST'], port=int(values['RELAY_PORT']))
    password = request.get('admin_password', '')
    if password and not 10 <= len(password) <= 128:
        raise ValueError('Administrator password must be 10..128 characters')
    if not (settings.data_dir / 'web-auth.sqlite3').exists() and not password:
        raise ValueError('First installation requires administrator password')
    secure(root)
    temp = root / 'relay.env.new'
    temp.write_text(''.join(k + '=' + v + '\n' for k, v in values.items()), encoding='utf-8')
    restrict_file(temp)
    # Validate the full preserved settings (not only wizard fields).
    Settings.from_env(temp)
    os.replace(temp, path)
    accounts = Accounts(settings.data_dir, 'relay')
    accounts.initialize()
    if password:
        accounts.reset('admin', password)
    return settings


def service_xml(program, root, service_id):
    def x(p): return escape(str(p), {'"': '&quot;'})
    return f'''<service><id>{service_id}</id><name>AP ESOP Workshop Relay</name>
<description>ESOP LAN cache and report forwarding</description>
<executable>{x(program / 'python/python.exe')}</executable>
<arguments>-m relay --config &quot;{x(root / 'relay.env')}&quot;</arguments>
<workingdirectory>{x(program / 'app')}</workingdirectory>
<startmode>Automatic</startmode><delayedAutoStart>true</delayedAutoStart><stoptimeout>45 sec</stoptimeout>
<onfailure action="restart" delay="10 sec"/><onfailure action="restart" delay="30 sec"/>
<onfailure action="restart" delay="60 sec"/><resetfailure>1 hour</resetfailure>
<logpath>{x(root / 'service-logs')}</logpath><log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>5</keepFiles></log>
</service>'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['install', 'uninstall', 'stop', 'secure'])
    p.add_argument('--program', required=True)
    p.add_argument('--data', required=True)
    p.add_argument('--service', default='AP-ESOP-Relay')
    p.add_argument('--request')
    a = p.parse_args()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{1,60}', a.service):
        raise ValueError('Invalid service identifier')
    program, root = Path(a.program).resolve(), Path(a.data).resolve()
    if a.action == 'secure':
        secure(root)
        return
    wrapper = program / 'service' / (a.service + '.exe')
    marker = root / 'installer.json'
    if a.action in ('stop', 'uninstall'):
        if not marker.exists():
            raise ValueError('Not managed by this installer')
        installed = json.loads(marker.read_text(encoding='utf-8'))
        if installed != {'program': str(program), 'service': a.service}:
            raise ValueError('Installation identity mismatch')
        run([str(wrapper), 'stop'], required=False)
        if a.action == 'uninstall':
            run([str(wrapper), 'uninstall'])
            run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule', 'name=' + a.service + '-LAN'], required=False)
            marker.unlink()
        return
    request_path = Path(a.request)
    try:
        request = json.loads(request_path.read_text(encoding='utf-8-sig'))
    finally:
        request_path.unlink(missing_ok=True)
    # Existing manual services must never be silently overwritten.
    exists = run(['sc.exe', 'query', a.service], required=False) == 0
    if exists and not marker.exists():
        raise ValueError('Service already exists outside this installer')
    settings = configure(root, request)
    from shutil import copyfile
    wrapper.parent.mkdir(exist_ok=True)
    copyfile(program / 'service/WinSW.NET4.exe', wrapper)
    wrapper.with_suffix('.xml').write_text(service_xml(program, root, a.service), encoding='utf-8')
    if not exists:
        run([str(wrapper), 'install'])
    marker.write_text(json.dumps({'program': str(program), 'service': a.service}), encoding='utf-8')
    run(['netsh', 'advfirewall', 'firewall', 'delete', 'rule', 'name=' + a.service + '-LAN'], required=False)
    if request.get('firewall', True):
        run(['netsh', 'advfirewall', 'firewall', 'add', 'rule', 'name=' + a.service + '-LAN', 'dir=in',
            'action=allow', 'protocol=TCP', 'localport=' + str(settings.port), 'remoteip=LocalSubnet', 'profile=private,domain'])
    run([str(wrapper), 'start'])
    host = '127.0.0.1' if settings.host in ('0.0.0.0', '::') else settings.host
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        try:
            with opener.open(f'http://{host}:{settings.port}/api/v1/health', timeout=2) as response:
                if response.status == 200:
                    (program / '状态网页.url').write_text('[InternetShortcut]\nURL=http://' + host + ':' + str(settings.port) + '/\n', encoding='utf-8')
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError('Service did not become healthy; inspect relay-data/logs')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No exception repr: URLs/credentials can be present in exception values.
        print('Installation failed. Check configuration, existing service, port, permissions and service logs.', file=sys.stderr)
        sys.exit(1)
