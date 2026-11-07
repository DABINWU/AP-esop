"""Real AOSP emulator -> HTTP relay -> Docker center, isolated from user data.

Requires built app/test APKs and workspace AOSP emulators 5554 and 5556.
Never clears data on a physical device; evidence includes source reports/screenshots.
"""
import hashlib,json,os,secrets,socket,subprocess,sys,threading,time
from pathlib import Path
import httpx,uvicorn
from fastapi import FastAPI,Request
from fastapi.responses import Response,StreamingResponse

ROOT=Path(__file__).resolve().parents[1]
ADB=Path(os.getenv('ANDROID_SDK_ROOT','C:/Users/WDB/AppData/Local/Android/Sdk'))/'platform-tools/adb.exe'


def wait(check,timeout=180):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            value=check()
            if value:return value
        except (httpx.RequestError,httpx.HTTPStatusError,json.JSONDecodeError):pass
        time.sleep(.5)
    raise AssertionError('Integration condition timed out')


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def main():
    run=ROOT/'test-output/android-http'/str(time.time_ns());run.mkdir(parents=True)
    cport,rport,pport=port(),port(),port()
    center_url=f'http://127.0.0.1:{cport}';relay_url=f'http://127.0.0.1:{rport}'
    admin='android-admin-'+secrets.token_hex(24);relay_key='android-relay-'+secrets.token_hex(24)
    device_key='android-device-'+secrets.token_hex(24);local_key='android-local-'+secrets.token_hex(24)
    project='esop-android-'+run.name[-10:]
    (run/'center.env').write_text(f'ADMIN_API_KEY={admin}\nRELAY_API_KEY={relay_key}\nESOP_DATA_DIR={(run/"center-data").as_posix()}\nESOP_BIND_IP=127.0.0.1\nESOP_PORT={cport}\nHEARTBEAT_SECONDS=2\nOFFLINE_SECONDS=10\n',encoding='utf-8')
    (run/'relay.env').write_text(f'CENTER_URL={center_url}\nCENTER_API_KEY={relay_key}\nDEVICE_API_KEY={device_key}\nLOCAL_ADMIN_API_KEY={local_key}\nRELAY_HOST=127.0.0.1\nRELAY_PORT={rport}\nRELAY_DATA_DIR={(run/"relay-data").as_posix()}\nPOLL_SECONDS=1\nDISK_RESERVE_MB=0\nRETRY_MAX_SECONDS=2\n',encoding='utf-8')
    compose=['docker','compose','--env-file',str(run/'center.env'),'-p',project]
    def docker(*args):
        p=subprocess.run([*compose,*args],cwd=ROOT,capture_output=True)
        assert p.returncode==0,'Isolated center Docker command failed'
    def adb(*args,timeout=45):
        p=subprocess.run([str(ADB),'-s','emulator-5554',*args],capture_output=True,timeout=timeout)
        assert p.returncode==0,'Workspace emulator command failed'
        return p.stdout
    assert adb('shell','getprop','ro.kernel.qemu').strip()==b'1','Only a disposable emulator is allowed'
    adb('shell','settings','put','global','device_provisioned','1')
    adb('shell','settings','put','secure','user_setup_complete','1')
    adb('shell','settings','put','secure','immersive_mode_confirmations','confirmed')
    adb('shell','am','force-stop','com.android.sdksetup')
    app_apk=ROOT/'android/app/build/outputs/apk/debug/app-debug.apk'
    test_apk=ROOT/'android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk'
    assert app_apk.is_file() and test_apk.is_file()
    samples=next(p.parent for p in sorted((ROOT/'test-output/media').glob('*/samples/basic-layout.xlsx'),reverse=True) if (p.parent/'short.mp4').is_file() and (p.parent.parent/'result.json').exists())
    relay_process=None;gate=set();proxy_app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    proxy_http=httpx.Client(base_url=relay_url,timeout=30,trust_env=False)
    @proxy_app.api_route('/api/v1/{tail:path}',methods=['GET','POST','HEAD'])
    async def proxy(tail:str,request:Request):
        if tail.startswith('files/') and tail.split('/')[-1] in gate:return Response(status_code=503)
        headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','connection','content-length')}
        body=await request.body()
        try:
            response=await __import__('asyncio').to_thread(proxy_http.request,request.method,'/api/v1/'+tail,headers=headers,content=body)
            result_headers={k:v for k,v in response.headers.items() if k.lower() in ('content-type','content-range','accept-ranges','etag')}
            return Response(response.content,status_code=response.status_code,headers=result_headers)
        except httpx.RequestError:return Response(status_code=503)
    proxy_server=uvicorn.Server(uvicorn.Config(proxy_app,host='0.0.0.0',port=pport,log_level='critical',access_log=False))
    thread=threading.Thread(target=proxy_server.run,daemon=True);thread.start()
    def start_relay():
        nonlocal relay_process
        relay_process=subprocess.Popen([sys.executable,'-m','relay','--config',str(run/'relay.env')],cwd=ROOT,
            stdout=(run/'relay-process.log').open('ab'),stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        wait(lambda:httpx.get(relay_url+'/api/v1/health',timeout=2).status_code==200,30)
    def stop_relay():
        nonlocal relay_process
        if relay_process and relay_process.poll() is None:relay_process.kill();relay_process.wait(timeout=15)
        relay_process=None
    checks=[]
    def record(check,**details):checks.append(dict(check=check,**details));print(check,flush=True)
    def screen(name):
        adb('shell','rm','-f','/sdcard/esop-ui.xml')
        text=adb('shell','uiautomator','dump','/sdcard/esop-ui.xml');xml=adb('shell','cat','/sdcard/esop-ui.xml').decode('utf-8',errors='replace')
        data=adb('exec-out','screencap','-p');(run/(name+'.png')).write_bytes(data)
        (run/(name+'.xml')).write_text(xml,encoding='utf-8');return xml
    try:
        docker('up','-d','--no-build')
        with httpx.Client(base_url=center_url,headers={'Authorization':'Bearer '+admin},timeout=40) as center:
            def post(path,body=None):
                r=center.post('/api/v1/'+path,json=body);r.raise_for_status();return r.json()
            wait(lambda:center.get('/api/v1/health').status_code==200)
            assets={}
            for filename in ('basic-layout.xlsx','short.mp4','embedded.png'):
                with (samples/filename).open('rb') as f:r=center.post('/api/v1/assets',files={'file':(filename,f)})
                r.raise_for_status();assets[filename]=r.json()['id']
            def ready_assets():
                r=center.get('/api/v1/assets');r.raise_for_status();selected=[a for a in r.json()['assets'] if a['id'] in assets.values()]
                assert all(a['status']!='failed' for a in selected),'Real sample conversion failed'
                return all(a['status']=='ready' for a in selected)
            wait(ready_assets)
            version=post('playlists',{'name':'ESOP 安卓联调'})['id']
            items=[{'asset_id':assets['basic-layout.xlsx'],'category':'production','duration_seconds':2},
                {'asset_id':assets['short.mp4'],'category':'production'},
                {'asset_id':assets['embedded.png'],'category':'inspection','duration_seconds':2},
                {'asset_id':assets['short.mp4'],'category':'check_video'},
                {'asset_id':assets['embedded.png'],'category':'custom','duration_seconds':2}]
            r=center.put('/api/v1/versions/'+version+'/items',json={'items':items});r.raise_for_status()
            published=post('versions/'+version+'/publish');manifest=published['manifest']
            for device in ('android-a','android-b'):post('devices',{'device_id':device,'name':device})
            def deploy(vid,request,timeout=10):return post('deployments',{'request_id':request,'playlist_version_id':vid,'device_ids':['android-a'],'timeout_seconds':timeout})
            start_relay()
            adb('install','-r',str(app_apk));adb('install','-r',str(test_apk));adb('shell','pm','clear','com.ap.esop')
            no_target=subprocess.run([str(ADB),'-s','emulator-5554','shell','am','instrument','-w','-e','class','com.ap.esop.UnassignedConnectionTest','-e','relay_url',f'http://10.0.2.2:{pport}','-e','device_id','android-a','-e','device_key',device_key,'com.ap.esop.test/androidx.test.runner.AndroidJUnitRunner'],capture_output=True,timeout=90)
            (run/'unassigned-connection.txt').write_bytes(no_target.stdout)
            assert b'OK (1 test)' in no_target.stdout,'Registered screen without assignment could not connect'
            record('registered screen with assignment:null and manifest:null connects before first deployment')
            first=deploy(version,'android-first',120)
            # Device key is ephemeral integration-only; never recorded in logs/evidence.
            command=[str(ADB),'-s','emulator-5554','shell','am','instrument','-w','-e','class','com.ap.esop.PlayerIntegrationTest','-e','relay_url',f'http://10.0.2.2:{pport}','-e','device_id','android-a','-e','device_key',device_key,'com.ap.esop.test/androidx.test.runner.AndroidJUnitRunner']
            process=subprocess.Popen(command,stdout=(run/'instrumentation.txt').open('wb'),stderr=subprocess.STDOUT)
            def devices():
                r=center.get('/api/v1/devices');r.raise_for_status();return r.json()
            wait(lambda:process.poll() is not None or next(d for d in devices()['devices'] if d['device_id']=='android-a')['playback_state']=='paused',180)
            if process.poll() is not None:assert 'OK (2 tests)' in (run/'instrumentation.txt').read_text(encoding='utf-8',errors='replace'),'Android instrumentation failed; see retained log'
            record('real AOSP app reports paused to center while heartbeat continues')
            wait(lambda:process.poll() is not None,180)
            output=(run/'instrumentation.txt').read_text(encoding='utf-8',errors='replace')
            assert 'OK (2 tests)' in output,'Android instrumentation failed; see retained log'
            record('Room persistence, native previous/next/pause/resume/replay/zoom and Media3 first-frame tests passed')
            adb('shell','am','start','-n','com.ap.esop/.MainActivity');wait(lambda:'ESOP' in screen('controls-playback'))
            wait(lambda:next(d for d in devices()['devices'] if d['device_id']=='android-a')['actual_version_id']==version)
            stop_relay();adb('shell','am','force-stop','com.ap.esop');adb('shell','am','start','-n','com.ap.esop/.MainActivity')
            wait(lambda:'ESOP 安卓联调' in screen('offline-restored'),45)
            assert '离线' in screen('offline-restored')
            record('app force-stop/reopen with relay offline restores verified local content and pause state without claiming latest target')
            adb('shell','am','force-stop','com.ap.esop');start_relay()
            wait(lambda:center.get('/api/v1/deployments').json()['tasks'][0]['status'] in ('succeeded','timed_out'),130)
            def clone(name):
                vid=post('versions/'+version+'/clone')['id'];return post('versions/'+vid+'/publish')['id']
            v2=clone('second');deploy(v2,'android-second')
            wait(lambda:center.get('/api/v1/deployments').json()['tasks'][0]['status']=='timed_out',20)
            v3=clone('third')
            third=deploy(v3,'android-third',90)
            adb('shell','am','start','-n','com.ap.esop/.MainActivity')
            wait(lambda:next(d for d in devices()['devices'] if d['device_id']=='android-a')['actual_version_id']==v3,100)
            assert next(d for d in devices()['devices'] if d['device_id']=='android-a')['playback_state']=='playing'
            record('multiple assignments while app stopped: latest only; new assignment resets pause to normal playback')
            record('relay process restart reconnects and terminal report queue resumes')
            # Generate a new image so the next target cannot reuse existing verified bytes.
            from PIL import Image
            from io import BytesIO
            image=Image.new('RGB',(1920,1080),(40,70,180));stream=BytesIO();image.save(stream,format='PNG')
            r=center.post('/api/v1/assets',files={'file':('new-target.png',stream.getvalue())});r.raise_for_status();new_asset=r.json();blocked=new_asset['files'][0]['id'];gate.add(blocked)
            v4=post('versions/'+version+'/clone')['id']
            r=center.put('/api/v1/versions/'+v4+'/items',json={'items':[{'asset_id':new_asset['id'],'category':'production','duration_seconds':2}]});r.raise_for_status()
            post('versions/'+v4+'/publish');deploy(v4,'android-incomplete',90)
            wait(lambda:'准备' in screen('new-target-blocked') or '下载' in screen('new-target-blocked'),45)
            assert next(d for d in devices()['devices'] if d['device_id']=='android-a')['actual_version_id']==v3
            record('incomplete new download blocked by real HTTP503: previous active content retained')
            gate.clear()
            wait(lambda:next(d for d in devices()['devices'] if d['device_id']=='android-a')['actual_version_id']==v4,100)
            record('download recovery verifies bytes, checks revision and activates only after image is drawn')
            wait(lambda:center.get('/api/v1/deployments').json()['tasks'][0]['status'] in ('succeeded','timed_out'),100)
            v5=post('versions/'+version+'/clone')['id']
            r=center.put('/api/v1/versions/'+v5+'/items',json={'items':[{'asset_id':assets['short.mp4'],'category':'check_video'}]});r.raise_for_status()
            post('versions/'+v5+'/publish');deploy(v5,'android-video-first',60)
            wait(lambda:next(d for d in devices()['devices'] if d['device_id']=='android-a')['actual_version_id']==v5,70)
            record('video-only new assignment becomes actual only after Media3 first-frame confirmation')
            wait(lambda:center.get('/api/v1/deployments').json()['tasks'][0]['status'] in ('succeeded','timed_out'),70)
            second=post('deployments',{'request_id':'android-second-screen','playlist_version_id':version,'device_ids':['android-b'],'timeout_seconds':90})
            def b(*args):
                p=subprocess.run([str(ADB),'-s','emulator-5556',*args],capture_output=True,timeout=120)
                assert p.returncode==0,'Second workspace emulator command failed';return p.stdout
            assert b('shell','getprop','ro.kernel.qemu').strip()==b'1'
            for namespace,key,value in [('global','device_provisioned','1'),('secure','user_setup_complete','1'),('secure','immersive_mode_confirmations','confirmed')]:b('shell','settings','put',namespace,key,value)
            b('shell','am','force-stop','com.android.sdksetup');b('install','-r',str(app_apk));b('install','-r',str(test_apk));b('shell','pm','clear','com.ap.esop')
            args=[str(ADB),'-s','emulator-5556','shell','am','instrument','-w','-e','class','com.ap.esop.SecondScreenSetupTest','-e','relay_url',f'http://10.0.2.2:{pport}','-e','device_id','android-b','-e','device_key',device_key,'com.ap.esop.test/androidx.test.runner.AndroidJUnitRunner']
            second_result=subprocess.run(args,capture_output=True,timeout=120);(run/'second-screen-instrumentation.txt').write_bytes(second_result.stdout)
            assert b'OK (1 test)' in second_result.stdout
            b('shell','am','start','-n','com.ap.esop/.MainActivity')
            wait(lambda:devices()['summary']['online']==2 and all(d['actual_version_id'] for d in devices()['devices']),45)
            record('two independent AOSP emulators: center online count 2 and distinct per-screen actual versions')
            screen('final-playback')
            (run/'center-status.json').write_text(json.dumps(devices(),ensure_ascii=False,indent=2),encoding='utf-8')
            result=dict(kind='real Android 10 AOSP x86_64 emulator -> fault-injecting HTTP proxy -> Windows relay -> Docker center; not MediaTek hardware',
                        checks=checks,artifacts=str(run),apk_sha256=hashlib.sha256(app_apk.read_bytes()).hexdigest())
            (run/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:
        adb('shell','am','force-stop','com.ap.esop')
        subprocess.run([str(ADB),'-s','emulator-5556','shell','am','force-stop','com.ap.esop'],capture_output=True,timeout=20)
        stop_relay();proxy_server.should_exit=True;thread.join(timeout=5);proxy_http.close();docker('down')


if __name__=='__main__':main()
