"""Real LibreOffice/poppler/ffprobe integration. Run INSIDE the Linux image.

Writes retained samples and conversion pages to test-output/media. No device clients are simulated here.
"""
import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from io import BytesIO

from PIL import Image
from openpyxl import Workbook

from fastapi.testclient import TestClient

from center.app import create_app
from center.config import Settings
from center.db import Database
from center.worker import process_one, recover_jobs
from scripts.generate_samples import generate
from scripts.pdf_samples import sample_pdf


def main():
    root=Path("test-output/media").resolve()
    root.mkdir(parents=True,exist_ok=True)
    # New directory each run, do not delete previous validation artifacts.
    run=root/str(time.time_ns()); run.mkdir()
    samples=generate(run/"samples")
    settings=Settings(data_dir=run/"data",admin_key="integration-admin-"+"a"*30,relay_key="integration-relay-"+"b"*30)
    result={"kind":"real Linux media integration, no relay or Android hardware", "checks":[], "artifacts":str(run)}
    headers={"Authorization":"Bearer "+settings.admin_key}
    with TestClient(create_app(settings)) as client:
        db=client.app.state.db
        ready=[]
        for filename in ("basic-layout.xlsx","basic-legacy.xls","no-print-area.xlsx","wps-cell-images.xlsx"):
            with (samples/filename).open("rb") as f:
                response=client.post("/api/v1/assets",headers=headers,files={"file":(filename,f)})
            assert response.status_code==200,response.text
            asset_id=response.json()["id"]
            if filename=="basic-layout.xlsx":
                # Persistent running state left by an interrupted worker, then a fresh DB instance recovers it.
                with db.write() as c:
                    c.execute("UPDATE conversion_jobs SET status='running',attempts=1 WHERE asset_id=?",(asset_id,))
                    c.execute("UPDATE assets SET status='processing' WHERE id=?",(asset_id,))
                recover_jobs(Database(settings))
            assert process_one(db)
            asset=next(a for a in client.get("/api/v1/assets",headers=headers).json()["assets"] if a["id"]==asset_id)
            assert asset["status"]=="ready",asset
            pages=[f for f in asset["files"] if f["role"]=="page"]
            assert len(pages)>=2,asset
            for f in asset["files"]:
                content=client.get(f["download_path"],headers=headers).content
                assert hashlib.sha256(content).hexdigest()==f["sha256"]
                assert len(content)==f["size_bytes"]
            if filename=="no-print-area.xlsx": assert any("未设置打印区域" in w for w in asset["warnings"])
            if filename=="basic-layout.xlsx": assert asset["conversion_job"]["attempts"]==2
            if filename=='wps-cell-images.xlsx':
                assert asset['metadata']['wps_cell_images_restored']==1
                pdf=settings.data_dir/'derived'/asset_id/'document.pdf'
                text=subprocess.check_output(['pdftotext',str(pdf),'-'],text=True)
                assert 'DISPIMG' not in text
                listing=subprocess.check_output(['pdfimages','-list',str(pdf)],text=True)
                image_count=sum(1 for line in listing.splitlines()[2:] if len(line.split())>2 and line.split()[2]=='image')
                assert image_count>=len(pages)+1,'WPS image missing from actual PDF'
            result["checks"].append({"excel":filename,"page_count":len(pages),"warnings":asset["warnings"]})
            ready.append(asset)
        # Whole-sheet export must preserve visible-sheet order and omit hidden content.
        book=Workbook(); book.active.title='Visible one'; book.active['A1']='VISIBLE_FIRST'
        hidden=book.create_sheet('Private middle'); hidden['A1']='HIDDEN_MUST_NOT_EXPORT'; hidden.sheet_state='hidden'
        last=book.create_sheet('Visible last'); last['A1']='VISIBLE_LAST'
        for sheet in book: sheet.column_dimensions['A'].width=40
        stream=BytesIO();book.save(stream)
        screen_cases=[('visible-hidden.xlsx',stream.getvalue(),2),
                      ('wps-cell-images.xlsx',(samples/'wps-cell-images.xlsx').read_bytes(),1)]
        for filename,data,expected in screen_cases:
            response=client.post('/api/v1/assets',headers=headers,data={'render_mode':'screen_16_9'},files={'file':(filename,data)})
            assert response.status_code==200,response.text
            asset_id=response.json()['id']
            assert process_one(db)
            asset=next(a for a in client.get('/api/v1/assets',headers=headers).json()['assets'] if a['id']==asset_id)
            assert asset['status']=='ready',asset
            assert asset['metadata']['render_mode']=='screen_16_9'
            pages=[f for f in asset['files'] if f['role']=='page']
            assert len(pages)==expected,asset
            for f in pages:
                content=client.get(f['download_path'],headers=headers).content
                assert hashlib.sha256(content).hexdigest()==f['sha256']
                with Image.open(BytesIO(content)) as image: assert image.size==(1920,1080)
            pdf=settings.data_dir/'derived'/asset_id/'document.pdf'
            text=subprocess.check_output(['pdftotext',str(pdf),'-'],text=True)
            assert 'DISPIMG' not in text and 'HIDDEN_MUST_NOT_EXPORT' not in text
            if filename=='visible-hidden.xlsx': assert text.index('VISIBLE_FIRST')<text.index('VISIBLE_LAST')
            else: assert asset['metadata']['wps_cell_images_restored']==1
            result['checks'].append({'screen_render':filename,'pages':len(pages),'size':[1920,1080],'hidden_sheets':'not exported'})
        for filename in ('broken.xls','wps-missing-image.xlsx'):
            with (samples/filename).open("rb") as f:
                bad=client.post("/api/v1/assets",headers=headers,files={"file":(filename,f)}).json()
            assert process_one(db)
            failed=next(a for a in client.get("/api/v1/assets",headers=headers).json()["assets"] if a["id"]==bad["id"])
            assert failed["status"]=="failed"
            result["checks"].append({"broken_excel":filename,"result":"failed, not publishable"})
        # Direct PDF imports bypass the Office parser and preserve each existing page.
        pdf_content=sample_pdf(((960,540),(540,960)))
        (samples/'direct-guide.pdf').write_bytes(pdf_content)
        pdf_assets=[]
        for mode in (None,'print'):
            data={} if mode is None else {'render_mode':mode}
            response=client.post('/api/v1/assets',headers=headers,data=data,
                                 files={'file':('direct-guide.pdf',pdf_content)})
            assert response.status_code==200,response.text
            asset_id=response.json()['id']
            # A missing UNO executable proves that direct PDF processing bypasses LibreOffice.
            direct_settings=replace(settings,uno_python='/not-installed-for-pdf')
            assert process_one(Database(direct_settings))
            asset=next(a for a in client.get('/api/v1/assets',headers=headers).json()['assets'] if a['id']==asset_id)
            assert asset['status']=='ready',asset
            assert asset['kind']=='pdf' and asset['metadata']['page_count']==2
            assert asset['metadata']['render_mode']==(mode or 'screen_16_9')
            assert 'wps_cell_images_restored' not in asset['metadata']
            files=asset['files']; pages=[f for f in files if f['role']=='page']
            assert [f['page_number'] for f in pages]==[1,2]
            for f in files:
                content=client.get(f['download_path'],headers=headers).content
                assert hashlib.sha256(content).hexdigest()==f['sha256'] and len(content)==f['size_bytes']
                if f['role'] in ('original','pdf'): assert content==pdf_content
                if f['role']=='page':
                    with Image.open(BytesIO(content)) as image:
                        if mode is None:
                            assert image.size==(1920,1080)
                            if f['page_number']==2: assert image.getpixel((0,540))==(255,255,255)
                        else:
                            assert (image.width>image.height)==(f['page_number']==1)
            pdf_assets.append(asset)
            result['checks'].append({'direct_pdf':mode or 'screen_16_9 default','page_count':2,
                                     'original_and_preview_identical':True,'office_parser':'not used'})
        # Real invalid, encrypted and over-page-limit PDFs cannot become playable.
        encrypted=samples/'encrypted';encrypted.mkdir()
        subprocess.run(['libreoffice','-env:UserInstallation='+(run/'encrypted-profile').as_uri(),
                        '--headless','--convert-to','pdf:calc_pdf_Export:{"EncryptFile":{"type":"boolean","value":"true"},"DocumentOpenPassword":{"type":"string","value":"fixture-only-password"}}',
                        '--outdir',str(encrypted),str(samples/'basic-layout.xlsx')],check=True,timeout=120,capture_output=True)
        encrypted_bytes=(encrypted/'basic-layout.pdf').read_bytes()
        for filename,content,maximum in [('broken.pdf',b'%PDF-1.7\ninvalid fixture',settings.max_pages),
                                         ('locked.pdf',encrypted_bytes,settings.max_pages),
                                         ('too-many-pages.pdf',pdf_content,1)]:
            response=client.post('/api/v1/assets',headers=headers,files={'file':(filename,content)})
            assert response.status_code==200,response.text
            asset_id=response.json()['id']
            assert process_one(Database(replace(settings,max_pages=maximum)))
            failed=next(a for a in client.get('/api/v1/assets',headers=headers).json()['assets'] if a['id']==asset_id)
            assert failed['status']=='failed' and all(f['role']=='original' for f in failed['files']),failed
            if filename=='locked.pdf': assert '加密' in failed['error'],failed
            result['checks'].append({'invalid_pdf':filename,'result':'failed, original retained, no playable files'})
        videos=[]
        for name,duration,codec,expected in [("short.mp4",2,"libx264",200),("at-limit.mp4",180,"libx264",200),("long.mp4",181,"libx264",422),("incompatible.mp4",2,"mpeg4",415)]:
            path=samples/name
            subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=c=teal:s=320x180:r=1", "-t",str(duration),"-c:v",codec,"-pix_fmt","yuv420p","-y",str(path)],check=True)
            with path.open("rb") as f: response=client.post("/api/v1/assets",headers=headers,files={"file":(name,f)})
            assert response.status_code==expected,response.text
            if expected==200: videos.append(response.json())
            result["checks"].append({"video":name,"http_status":response.status_code,"result":response.json()})
        audio=samples/"audio.mp4"
        subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=c=teal:s=320x180:r=5","-f","lavfi","-i","sine=frequency=400:sample_rate=44100","-t","2","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-y",str(audio)],check=True)
        with audio.open("rb") as f: response=client.post("/api/v1/assets",headers=headers,files={"file":("audio.mp4",f)})
        assert response.status_code==200,response.text
        result["checks"].append({"video":"AAC accepted","metadata":response.json()["metadata"]})
        # File size rejects use the real short MP4 with a deliberately lower configured limit.
        with TestClient(create_app(replace(settings,video_max_bytes=32))) as limited:
            with (samples/"short.mp4").open("rb") as f: response=limited.post("/api/v1/assets",headers=headers,files={"file":("short.mp4",f)})
            assert response.status_code==413,response.text
        with (samples/"embedded.png").open("rb") as f:
            picture=client.post("/api/v1/assets",headers=headers,files={"file":("embedded.png",f)}).json()
        version=client.post("/api/v1/playlists",headers=headers,json={"name":"真实媒体转换验证"}).json()
        response=client.put(f"/api/v1/versions/{version['id']}/items",headers=headers,json={"items":[{"asset_id":ready[0]["id"]},{"asset_id":pdf_assets[0]['id']},{"asset_id":picture["id"],"duration_seconds":12},{"asset_id":videos[0]["id"]}]})
        assert response.status_code==200,response.text
        items=response.json()["items"]
        expected_pages=ready[0]["metadata"]["page_count"]
        assert [i["file"]["page_number"] for i in items[:expected_pages]]==list(range(1,expected_pages+1))
        assert [i['file']['page_number'] for i in items[expected_pages:expected_pages+2]]==[1,2]
        assert all(i['type']=='page' and i['duration_seconds']==20 for i in items[expected_pages:expected_pages+2])
        assert items[-1]["type"]=="video" and items[-1]["playback"]=="once" and items[-1]["duration_seconds"] is None
        response=client.post(f"/api/v1/versions/{version['id']}/publish",headers=headers)
        assert response.status_code==200,response.text
        result["checks"].append({"manifest":"immutable, ordered Excel/PDF pages + images + one-shot video", "item_count":len(items),"version_id":version["id"]})
        # Referenced real PDF pages remain downloadable; unreferenced PDF derivatives are removed.
        protected=pdf_assets[0]
        inspection=client.get('/api/v1/assets/'+protected['id']+'/usage',headers=headers).json()
        assert not inspection['can_delete'] and inspection['references'][0]['status']=='published'
        blocked=client.delete('/api/v1/assets/'+protected['id'],headers=headers)
        assert blocked.status_code==409 and blocked.json()['error']['code']=='asset_in_use'
        assert client.post(f"/api/v1/versions/{version['id']}/publish",headers=headers).json()['manifest']==response.json()['manifest']
        disposable=pdf_assets[1]
        with db.read() as c:
            paths=[settings.data_dir/row[0] for row in c.execute('SELECT path FROM files WHERE asset_id=?',(disposable['id'],))]
        assert client.delete('/api/v1/assets/'+disposable['id'],headers=headers).status_code==200
        assert all(not path.exists() for path in paths)
        assert not (settings.data_dir/'derived'/disposable['id']).exists()
        assert all(client.get(f['download_path'],headers=headers).status_code==404 for f in disposable['files'])
        result['checks'].append({'delete':'unused PDF original, preview, two PNGs and queue record removed; published PDF blocked409; manifest unchanged'})
    (run/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
