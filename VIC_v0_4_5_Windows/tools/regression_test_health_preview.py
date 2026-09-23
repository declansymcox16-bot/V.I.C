from __future__ import annotations
import importlib.util, json, sys, tempfile, time
from pathlib import Path
BASE=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location("vic_health_test",BASE/"dashboard"/"app.py")
if spec is None or spec.loader is None: raise RuntimeError("Could not load dashboard")
mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory(prefix="vic-health-test-") as temp:
    p=Path(temp);c=p/"config";c.mkdir();mod.SOURCES_FILE=c/"sources.json";mod.JOBS_FILE=c/"jobs.json";mod.WORKERS_FILE=c/"workers.json";mod.TRANSFERS_FILE=c/"transfers.json";mod.SETTINGS_FILE=c/"dashboard.json";mod.PREVIEW_DIR=p/"previews";mod.PREVIEW_DIR.mkdir()
    now=time.time();src={"id":"s1","name":"Screen","type":"screen","type_label":"Screen","worker_id":"w1","options":{"target":"desktop","fps_mode":"auto","fps":60,"encoder_preference":"auto"},"summary":"desktop"}
    for path,value in [(mod.SOURCES_FILE,[src]),(mod.JOBS_FILE,[]),(mod.TRANSFERS_FILE,[]),(mod.SETTINGS_FILE,{"cluster_token":"x","worker_offline_seconds":60}),(mod.WORKERS_FILE,[{"id":"w1","name":"Local","host":"PC","last_seen_ts":now,"is_local_dashboard":True,"recordings":[]}])]:path.write_text(json.dumps(value),encoding="utf-8")
    client=mod.app.test_client()
    assert client.post('/sources/s1/preview').status_code in {302,303}
    jobs=json.loads(mod.JOBS_FILE.read_text());assert jobs[-1]['mode']=='preview'
    assert client.post('/sources/s1/start').status_code in {302,303}
    jobs=json.loads(mod.JOBS_FILE.read_text());assert any(j['mode']=='record' and j['state']=='waiting' for j in jobs)
    assert client.get('/health').status_code==200
    assert client.get('/live').status_code==200
    assert client.get('/live/all').status_code==200
print('VIC Preview and Health regression tests passed.')
