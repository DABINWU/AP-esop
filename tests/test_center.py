import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from center.app import create_app
from center.config import Settings
from center.db import Database, now
from center.media import MediaError, verify_video
from center.models import DeploymentCreate
from center.service import create_deployment
from center.worker import process_one, recover_jobs, worker_lock


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_key="test-admin-" + "a" * 30, relay_key="test-relay-" + "b" * 30)
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app.state.db, {"Authorization":"Bearer " + settings.admin_key}, {"Authorization":"Bearer " + settings.relay_key}


def png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (64, 32), "#148675").save(stream, "PNG")
    return stream.getvalue()


@pytest.mark.parametrize('mode', ['print', 'screen_16_9'])
def test_excel_render_mode_survives_queue_recovery(env, mode):
    from openpyxl import Workbook
    stream = io.BytesIO()
    Workbook().save(stream)
    client, db, admin, _ = env
    response = client.post('/api/v1/assets', headers=admin, data={'render_mode': mode},
                           files={'file': ('sheet.xlsx', stream.getvalue())})
    assert response.status_code == 200, response.text
    asset = response.json()
    assert asset['metadata']['render_mode'] == mode
    with db.write() as c:
        c.execute("UPDATE conversion_jobs SET status='running',attempts=1 WHERE asset_id=?", (asset['id'],))
    recover_jobs(Database(db.settings))
    recovered = next(a for a in client.get('/api/v1/assets', headers=admin).json()['assets'] if a['id'] == asset['id'])
    assert recovered['conversion_job']['status'] == 'queued'
    assert recovered['metadata']['render_mode'] == mode


def test_render_mode_rejects_unknown_and_non_excel(env):
    client, db, admin, _ = env
    for mode in ('stretch', 'screen_16_9'):
        response = client.post('/api/v1/assets', headers=admin, data={'render_mode': mode},
                               files={'file': ('picture.png', png_bytes())})
        assert response.status_code == 422
    assert not list((db.settings.data_dir / 'originals').iterdir())


def image_asset(env):
    c, db, admin, relay = env
    r = c.post("/api/v1/assets", headers=admin, files={"file":("test.png", png_bytes(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()


def published(env, asset=None):
    c, db, admin, relay = env
    asset = asset or image_asset(env)
    v = c.post("/api/v1/playlists", headers=admin, json={"name":"装配指导"}).json()
    r = c.put(f"/api/v1/versions/{v['id']}/items", headers=admin, json={"items":[{"asset_id":asset["id"]}]})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/v1/versions/{v['id']}/publish", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def register(env, ids=("screen-a", "screen-b")):
    for device in ids:
        r = env[0].post("/api/v1/devices", headers=env[2], json={"device_id":device,"name":device})
        assert r.status_code == 200


def deploy(env, version, request_id="request-0001", ids=("screen-a", "screen-b")):
    return env[0].post("/api/v1/deployments", headers=env[2], json={"request_id":request_id,
        "playlist_version_id":version["id"],"device_ids":list(ids),"timeout_seconds":10})


def timeout(db, task_id):
    with db.write() as c:
        c.execute("UPDATE tasks SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?", (task_id,))


def report(version, target, seq=1, **overrides):
    data = dict(event_id=f"{target['device_id']}-{seq}",device_id=target["device_id"],report_seq=seq,
                observed_at=now(),assignment_revision=target["assignment_revision"],
                actual_version_id=version["id"],current_item_id=version["items"][0]["id"],
                playback_state="playing",pending_version_id=None,error=None)
    data.update(overrides)
    return data


def send(env, reports):
    return env[0].post("/api/v1/relay/reports",headers=env[3],json={"relay_id":"workshop-1","reports":reports})


def test_auth_download_and_role_separation(env):
    c, db, admin, relay = env
    assert c.get("/api/v1/health").status_code == 200
    assert c.get("/api/v1/assets").status_code == 401
    assert c.get("/api/v1/assets", headers=relay).status_code == 401
    assert c.post("/api/v1/relay/sync", headers=admin, json={"relay_id":"workshop-1"}).status_code == 401
    a = image_asset(env)
    url = a["files"][0]["download_path"]
    assert c.get(url).status_code == 401
    assert c.get(url, headers=relay).content == png_bytes()
    html = c.get("/").text + c.get("/static/app.js").text
    assert db.settings.admin_key not in html and db.settings.relay_key not in html
    assert "localStorage" not in c.get("/static/app.js").text


def test_upload_invalid_and_limits(env):
    c, db, admin, relay = env
    assert c.post("/api/v1/assets", headers=admin, files={"file":("bad.png",b"not png")}).status_code == 422
    assert c.post("/api/v1/assets", headers=admin, files={"file":("bad.xlsx",b"broken")}).status_code == 422
    assert c.post("/api/v1/assets", headers=admin, files={"file":("bad.exe",b"a")}).status_code == 415
    assert c.post("/api/v1/assets", headers=admin, files={"file":("empty.png",b"")}).status_code == 422
    assert not list((db.settings.data_dir / "originals").iterdir())
    settings = replace(db.settings, upload_max_bytes=40,video_max_bytes=40)
    with TestClient(create_app(settings)) as small:
        assert small.post("/api/v1/assets", headers=admin, files={"file":("big.png",png_bytes())}).status_code == 413


def test_manifest_immutable_and_clone(env):
    c, db, admin, relay = env
    v = published(env)
    f = v["manifest"]["items"][0]["file"]
    assert f["sha256"] == hashlib.sha256(png_bytes()).hexdigest()
    assert f["size_bytes"] == len(png_bytes())
    assert v["manifest"]["loop"] and v["items"][0]["duration_seconds"] == 20
    stored = (db.settings.data_dir / "manifests" / (v["id"]+".json")).read_text(encoding="utf-8")
    assert json.loads(stored) == v["manifest"]
    assert c.put(f"/api/v1/versions/{v['id']}/items", headers=admin,json={"items":[]}).status_code == 409
    assert c.post(f"/api/v1/versions/{v['id']}/publish", headers=admin).json()["manifest"] == v["manifest"]
    clone = c.post(f"/api/v1/versions/{v['id']}/clone", headers=admin).json()
    assert clone["version"] == 2 and clone["status"] == "draft" and clone["items"] == v["items"]
    with pytest.raises(Exception, match="immutable"):
        with db.write() as conn:
            conn.execute("UPDATE versions SET name='bad' WHERE id=?",(v["id"],))


def test_order_duration_and_validation(env):
    c, db, admin, relay = env
    a, b = image_asset(env), image_asset(env)
    v = c.post("/api/v1/playlists",headers=admin,json={"name":"排序"}).json()
    path = f"/api/v1/versions/{v['id']}/items"
    result = c.put(path, headers=admin,json={"items":[{"file_id":b["files"][0]["id"],"duration_seconds":15},{"asset_id":a["id"]}]}).json()
    assert [i["position"] for i in result["items"]] == [0,1]
    assert [i["duration_seconds"] for i in result["items"]] == [15,20]
    assert result["items"][0]["file"]["id"] == b["files"][0]["id"]
    assert c.put(path, headers=admin,json={"items":[{"asset_id":a["id"],"file_id":"x"}]}).status_code == 422
    assert c.put(path, headers=admin,json={"items":[{"asset_id":a["id"],"duration_seconds":0}]}).status_code == 422


def test_concurrent_task_lock_and_idempotency(env):
    c, db, admin, relay = env
    v = published(env)
    register(env)
    def race(index):
        try:
            return create_deployment(db, DeploymentCreate(request_id=f"concurrent-{index}",playlist_version_id=v["id"],device_ids=["screen-a"]))
        except MediaError as e:
            return e.status
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(race, range(6)))
    assert sum(isinstance(r,dict) for r in results) == 1
    assert results.count(409) == 5
    winner = next(r for r in results if isinstance(r,dict))
    retry = c.post("/api/v1/deployments",headers=admin,json={"request_id":winner["request_id"],"playlist_version_id":v["id"],"device_ids":["screen-a"]})
    assert retry.status_code == 200 and retry.json()["id"] == winner["id"]
    assert deploy(env,v,winner["request_id"]).status_code == 409


def test_offline_task_releases_at_deadline_without_any_screen_report(env, monkeypatch):
    c, db, admin, relay = env
    old, new = published(env), published(env)
    register(env)
    first = deploy(env, old).json()
    deadline = datetime.fromisoformat(first['expires_at'].replace('Z', '+00:00'))
    before = (deadline - timedelta(milliseconds=1)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    monkeypatch.setattr('center.db.now', lambda: before)
    blocked = deploy(env, new, 'offline-second')
    assert blocked.status_code == 409
    assert first['expires_at'] in blocked.json()['error']['message']
    monkeypatch.setattr('center.db.now', lambda: first['expires_at'])
    accepted = deploy(env, new, 'offline-second')
    assert accepted.status_code == 200
    devices = c.get('/api/v1/devices', headers=admin).json()['devices']
    assert all(d['desired']['playlist_version_id'] == new['id'] and d['pending'] for d in devices)
    tasks = c.get('/api/v1/deployments', headers=admin).json()['tasks']
    assert next(t for t in tasks if t['id'] == first['id'])['status'] == 'timed_out'


def test_offline_latest_assignment_survives_restart_and_nonconsuming_poll(env):
    c, db, admin, relay = env
    v1, v2 = published(env), published(env)
    register(env)
    t1 = deploy(env,v1).json(); timeout(db,t1["id"])
    t2 = deploy(env,v2,"request-0002").json()
    assert t2["status"] == "running"
    # A new app and DB connection on the same data directory emulate a center restart.
    with TestClient(create_app(db.settings)) as fresh:
        payload = {"relay_id":"workshop-1"}
        first = fresh.post("/api/v1/relay/sync",headers=relay,json=payload).json()
        second = fresh.post("/api/v1/relay/sync",headers=relay,json=payload).json()
        assert first["assignments"] == second["assignments"]
        assert {a["playlist_version_id"] for a in first["assignments"]} == {v2["id"]}
        assert min(a["assignment_revision"] for a in first["assignments"]) > max(a["assignment_revision"] for a in t1["targets"])
        assert first["versions"][0]["manifest"] == v2["manifest"]
    task = c.get("/api/v1/deployments",headers=admin).json()["tasks"]
    assert next(t for t in task if t["id"]==t1["id"])["status"] == "timed_out"


def test_multi_screen_completion_and_duplicate_stale_reports(env):
    c, db, admin, relay = env
    v = published(env); register(env); t = deploy(env,v).json()
    r1 = report(v,t["targets"][0]); r2 = report(v,t["targets"][1])
    assert send(env,[r1]).json()["results"][0]["status"] == "accepted"
    assert c.get("/api/v1/deployments",headers=admin).json()["tasks"][0]["status"] == "running"
    assert send(env,[r1]).json()["results"][0]["status"] == "duplicate"
    assert send(env,[dict(r1,error="conflicting event")]).status_code == 409
    assert send(env,[r2]).status_code == 200
    assert c.get("/api/v1/deployments",headers=admin).json()["tasks"][0]["status"] == "succeeded"
    newer = report(v,t["targets"][0],2,playback_state="paused")
    assert send(env,[newer]).status_code == 200
    stale = dict(r1,event_id="separate-old-event")
    assert send(env,[stale]).json()["results"][0]["status"] == "stale"
    state = c.get("/api/v1/devices",headers=admin).json()
    assert state["summary"] == dict(registered=2,online=2,offline=0,unknown=0,pending=0)
    assert state["devices"][0]["playback_state"] == "paused"


def test_late_report_changes_actual_but_not_terminal_task(env):
    c, db, admin, relay = env
    v = published(env); register(env); t = deploy(env,v).json(); timeout(db,t["id"])
    assert send(env,[report(v,t["targets"][0])]).status_code == 200
    task = c.get("/api/v1/deployments",headers=admin).json()["tasks"][0]
    assert task["status"] == "timed_out" and task["targets"][0]["status"] == "pending"
    assert c.get("/api/v1/devices",headers=admin).json()["devices"][0]["actual_version_id"] == v["id"]


def test_ack_is_not_playing_and_old_ack_ignored(env):
    c, db, admin, relay = env
    v = published(env); register(env); t = deploy(env,v).json()
    acks = [{"device_id":x["device_id"],"assignment_revision":x["assignment_revision"]} for x in t["targets"]]
    payload = {"relay_id":"workshop-1","assignments":acks}
    assert c.post("/api/v1/relay/acks",headers=relay,json=payload).json()["accepted"] == 2
    assert c.post("/api/v1/relay/acks",headers=relay,json=payload).json()["accepted"] == 2
    state = c.get("/api/v1/devices",headers=admin).json()
    assert state["summary"]["pending"] == 2 and state["summary"]["offline"] == 2
    timeout(db,t["id"]); deploy(env,v,"newer-request")
    assert c.post("/api/v1/relay/acks",headers=relay,json=payload).json()["accepted"] == 0
    assert c.get("/api/v1/devices",headers=admin).json()["devices"][0]["desired"]["relay_acked_at"] is None


def test_unknown_relay_and_old_device_contact(env):
    c, db, admin, relay = env
    v = published(env); register(env); t = deploy(env,v).json()
    old = (datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat().replace("+00:00","Z")
    assert send(env,[report(v,t["targets"][0],observed_at=old)]).status_code == 200
    state = c.get("/api/v1/devices",headers=admin).json()
    assert state["summary"]["offline"] == 2
    with db.write() as conn:
        conn.execute("UPDATE relay_state SET last_seen_at=?", (old,))
    state = c.get("/api/v1/devices",headers=admin).json()
    assert state["relay"]["state"] == "unknown" and state["summary"]["unknown"] == 2
    assert state["summary"]["online"] == 0 and state["summary"]["offline"] == 0


def test_report_batch_rollback_and_invalid_revision(env):
    c, db, admin, relay = env
    v = published(env); register(env); t = deploy(env,v).json()
    good, bad = report(v,t["targets"][0]), report(v,t["targets"][1],assignment_revision=999)
    assert send(env,[good,bad]).status_code == 409
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert conn.execute("SELECT MAX(report_seq) FROM devices").fetchone()[0] == 0
    future = report(v,t["targets"][0],observed_at="2099-01-01T00:00:00Z")
    assert send(env,[future]).status_code == 422
    assert send(env,[report(v,t["targets"][0],current_item_id="wrong-item")]).status_code == 422


def test_conversion_failure_and_restart_recovery(env, monkeypatch):
    c, db, admin, relay = env
    fake_xls = bytes.fromhex("d0cf11e0a1b11ae1") + b"intentionally broken compound workbook"
    a = c.post("/api/v1/assets",headers=admin,files={"file":("broken.xls",fake_xls)}).json()
    assert a["status"] == "queued"
    def fail(*args, **kwargs):
        raise MediaError("测试：转换失败")
    monkeypatch.setattr("center.worker.run_command", fail)
    assert process_one(db)
    failed = c.get("/api/v1/assets",headers=admin).json()["assets"][0]
    assert failed["status"] == "failed" and failed["conversion_job"]["status"] == "failed"
    v = c.post("/api/v1/playlists",headers=admin,json={"name":"失败禁止发布"}).json()
    assert c.put(f"/api/v1/versions/{v['id']}/items",headers=admin,json={"items":[{"asset_id":a["id"]}]}).status_code == 409
    assert c.post(f"/api/v1/versions/{v['id']}/publish",headers=admin).status_code == 409
    for attempts, expected in [(1,"queued"),(2,"failed")]:
        with db.write() as conn:
            conn.execute("UPDATE conversion_jobs SET status='running',attempts=?", (attempts,))
            conn.execute("UPDATE assets SET status='processing'")
        fresh = Database(db.settings); fresh.initialize(); recover_jobs(fresh)
        with fresh.read() as conn:
            assert conn.execute("SELECT status FROM conversion_jobs").fetchone()[0] == expected


def test_worker_lock_excludes_second_worker(env):
    with worker_lock(env[1].settings.data_dir):
        with pytest.raises(OSError):
            with worker_lock(env[1].settings.data_dir):
                pass


@pytest.mark.parametrize("change,status", [({"duration":181},422),({"codec":"hevc"},415),({"audio":"mp3"},415),({"pix_fmt":"yuv444p"},415),({"duration":float("nan")},422)])
def test_video_probe_rejections(env, monkeypatch, change, status):
    cfg = dict(duration=5,codec="h264",audio="aac",pix_fmt="yuv420p") | change
    probe = {"format":{"format_name":"mov,mp4,m4a,3gp,3g2,mj2","duration":str(cfg["duration"])},"streams":[
        {"codec_type":"video","codec_name":cfg["codec"],"pix_fmt":cfg["pix_fmt"],"width":640,"height":360},
        {"codec_type":"audio","codec_name":cfg["audio"]}]}
    monkeypatch.setattr("center.media.run_command",lambda *a: json.dumps(probe).encode())
    with pytest.raises(MediaError) as error:
        verify_video("unused.mp4",env[1].settings)
    assert error.value.status == status
