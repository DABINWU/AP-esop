"""Real worker validation for task-first upload; synthetic documents, no device hardware."""
import hashlib
import json
import subprocess
import time
from pathlib import Path
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from center.app import create_app
from center.config import Settings
from center.db import Database
from center.worker import process_one, recover_jobs
from center.playlist_workflow import attach_asset
from scripts.generate_samples import generate
from scripts.pdf_samples import sample_pdf


def main():
    root=Path('test-output/modules')/str(time.time_ns());root=root.resolve();root.mkdir(parents=True)
    samples=generate(root/'samples')
    video=samples/'check.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=teal:s=320x180:r=2','-t','2','-c:v','libx264','-pix_fmt','yuv420p','-y',str(video)],check=True)
    settings=Settings(data_dir=root/'data',admin_key='modules-admin-'+'a'*30,relay_key='modules-relay-'+'b'*30)
    headers={'Authorization':'Bearer '+settings.admin_key}
    with TestClient(create_app(settings)) as client:
        def call(method,path,**kwargs):
            r=client.request(method,'/api/v1'+path,headers=headers,**kwargs);r.raise_for_status();return r.json()
        v=call('POST','/playlists',json={'name':'四模块真实转换样例'})
        ids=[]
        for category,name,data in [('production','guide.xlsx',(samples/'wps-cell-images.xlsx').read_bytes()),
                                   ('inspection','guide.pdf',sample_pdf(((960,540),(960,540)))),
                                   ('check_video','check.mp4',video.read_bytes()),
                                   ('custom','image.png',(samples/'embedded.png').read_bytes())]:
            asset=call('POST',f"/versions/{v['id']}/modules/{category}/assets",data={'duration_seconds':'12'},files={'file':(name,data)})
            ids.append(asset['id'])
        db=client.app.state.db
        with db.write() as c:
            c.execute("UPDATE conversion_jobs SET status='running',attempts=1 WHERE asset_id=?",(ids[0],))
            c.execute("UPDATE assets SET status='processing' WHERE id=?",(ids[0],))
        recover_jobs(Database(settings))
        while process_one(db):pass
        assets=call('GET','/assets')['assets']
        assert all(a['status']=='ready' for a in assets),assets
        for asset in assets:
            for f in asset['files']:
                r=client.get(f['download_path'],headers=headers);assert r.status_code==200
                assert hashlib.sha256(r.content).hexdigest()==f['sha256']
                if f['role']=='page':
                    with Image.open(BytesIO(r.content)) as image:assert image.size==(1920,1080)
        # Replaying attachment after recovery cannot duplicate a page.
        with db.write() as c:
            attach_asset(c,c.execute('SELECT * FROM assets WHERE id=?',(ids[0],)).fetchone(),v['id'])
        published=call('POST',f"/versions/{v['id']}/publish")
        manifest=published['manifest']
        assert manifest['schema_version']==2 and manifest['display']['fit']=='contain'
        assert [len(m['items']) for m in manifest['modules']]==[1,2,1,1],manifest
        assert manifest['modules'][2]['items'][0]['playback']=='once'
        assert all(i['duration_seconds']==12 for m in manifest['modules'] for i in m['items'] if i['type']!='video')
        clone=call('POST',f"/versions/{v['id']}/clone")
        removed=call('DELETE',f"/versions/{clone['id']}/assets/{ids[1]}")
        assert removed['retained']
        got=next(x for x in call('GET','/playlists')['versions'] if x['id']==clone['id'])
        assert len(got['items'])==3
        assert next(x for x in call('GET','/playlists')['versions'] if x['id']==v['id'])['manifest']==manifest
        deleted=call('DELETE','/versions/'+clone['id'])
        assert deleted['archived'] and not deleted['retained_history']
        next_version=call('POST',f"/versions/{v['id']}/clone")
        assert next_version['version']==3
        archived=call('DELETE','/versions/'+v['id'])
        assert archived['retained_history']
        assert next(x for x in call('GET','/playlists')['versions'] if x['id']==v['id'])['manifest']==manifest
        archive=call('DELETE','/playlists/'+v['playlist_id'])
        assert archive['retained_history']
        assert all(p['id']!=v['playlist_id'] for p in call('GET','/playlists')['playlists'])
        assert next(x for x in call('GET','/playlists')['versions'] if x['id']==v['id'])['manifest']==manifest
    result={'kind':'real Linux worker, no Android hardware','module_counts':[1,2,1,1],
            'conversion_recovery':'once, no duplicate attachment','page_size':[1920,1080],
            'delete':'clone removes all PDF pages, historical files retained; task archived',
            'manifest_sha256':hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest(),
            'artifacts':str(root)}
    (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
