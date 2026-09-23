import json
import os
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory

BASE = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE / "config"
RECORDINGS_DIR = BASE / "recordings"
LOGS_DIR = BASE / "logs"
SOURCES_FILE = CONFIG_DIR / "sources.json"

for folder in (CONFIG_DIR, RECORDINGS_DIR, LOGS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
recording_jobs = {}
workers = {}
worker_lock = threading.Lock()

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VIC Dashboard</title>
<style>body{font-family:Arial,sans-serif;background:#101114;color:#f2f2f2;margin:0}header{background:#1b1d22;padding:22px 30px;border-bottom:1px solid #333}main{max-width:1200px;margin:auto;padding:26px}.card{background:#191b20;border:1px solid #333;border-radius:12px;padding:20px;margin-bottom:20px}table{width:100%;border-collapse:collapse}th,td{padding:11px;border-bottom:1px solid #333;text-align:left}input,select{width:100%;box-sizing:border-box;background:#22252b;color:#fff;border:1px solid #555;border-radius:6px;padding:10px;margin:5px 0 14px}button{padding:9px 14px;border-radius:6px;border:1px solid #666;cursor:pointer}.ok{color:#73e673}.bad{color:#ff7777}.muted{color:#aaa}a{color:#8fc7ff}</style></head><body>
<header><h1>VIC — Video Ingest Cluster</h1><div class="muted">Windows Foundation v0.1</div></header><main>
<div class="card"><h2>Add RTSP Source</h2><form method="post" action="/sources/add"><label>Name</label><input name="name" required><label>RTSP URL</label><input name="url" required><label>Worker</label><select name="worker_id"><option value="">Local dashboard PC</option>{% for w in workers %}<option value="{{w.id}}">{{w.name}} — {{w.host}}</option>{% endfor %}</select><button type="submit">Add Source</button></form></div>
<div class="card"><h2>Sources</h2><table><tr><th>Name</th><th>URL</th><th>Worker</th><th>Status</th><th>Actions</th></tr>{% for s in sources %}<tr><td>{{s.name}}</td><td><code>{{s.url}}</code></td><td>{{s.worker_name or 'Local dashboard PC'}}</td><td>{{'RECORDING' if s.running else 'STOPPED'}}</td><td>{% if s.running %}<form style="display:inline" method="post" action="/sources/{{s.id}}/stop"><button>Stop</button></form>{% else %}<form style="display:inline" method="post" action="/sources/{{s.id}}/start"><button>Start</button></form>{% endif %}<form style="display:inline" method="post" action="/sources/{{s.id}}/delete"><button>Delete</button></form></td></tr>{% else %}<tr><td colspan="5">No sources added yet.</td></tr>{% endfor %}</table></div>
<div class="card"><h2>Workers</h2><table><tr><th>Name</th><th>Host</th><th>Status</th><th>Last Seen</th></tr>{% for w in workers %}<tr><td>{{w.name}}</td><td>{{w.host}}</td><td>{{'ONLINE' if w.online else 'OFFLINE'}}</td><td>{{w.last_seen}}</td></tr>{% else %}<tr><td colspan="4">No worker PCs connected yet.</td></tr>{% endfor %}</table></div>
<div class="card"><h2>Recordings</h2><a href="/recordings">Browse recordings</a></div></main></body></html>"""

def load_sources():
    if not SOURCES_FILE.exists(): return []
    try: return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except Exception: return []

def save_sources(sources):
    SOURCES_FILE.write_text(json.dumps(sources,indent=2),encoding="utf-8")

def get_workers():
    now=time.time()
    with worker_lock:
        return [{**w,"online":now-w["last_seen_ts"]<10} for w in workers.values()]

def start_local_recording(source):
    sid=source["id"]; folder=RECORDINGS_DIR/sid; folder.mkdir(parents=True,exist_ok=True)
    output=folder/f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.mkv"
    cmd=["ffmpeg","-hide_banner","-loglevel","warning","-rtsp_transport","tcp","-i",source["url"],"-map","0","-c","copy","-f","matroska",str(output)]
    try:
        recording_jobs[sid]=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); return True
    except Exception as exc:
        with (LOGS_DIR/"dashboard_error.log").open("a",encoding="utf-8") as log: log.write(f"{time.ctime()} | {sid} | {exc}\n")
        return False

def stop_local_recording(sid):
    p=recording_jobs.get(sid)
    if p and p.poll() is None:
        p.terminate()
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired: p.kill()
    recording_jobs.pop(sid,None)

@app.get("/")
def index():
    sources=load_sources(); worker_items=get_workers(); lookup={x["id"]:x for x in worker_items}
    for s in sources:
        p=recording_jobs.get(s["id"]); s["running"]=bool(p and p.poll() is None)
        s["worker_name"]=lookup.get(s.get("worker_id",""),{}).get("name","")
    return render_template_string(PAGE,sources=sources,workers=worker_items)

@app.post("/sources/add")
def add_source():
    s=load_sources(); s.append({"id":uuid.uuid4().hex[:12],"name":request.form["name"].strip(),"url":request.form["url"].strip(),"worker_id":request.form.get("worker_id","").strip()}); save_sources(s); return redirect("/")

@app.post("/sources/<source_id>/start")
def start_source(source_id):
    s=next((x for x in load_sources() if x["id"]==source_id),None)
    if s is None: return "Source not found",404
    start_local_recording(s); return redirect("/")

@app.post("/sources/<source_id>/stop")
def stop_source(source_id):
    stop_local_recording(source_id); return redirect("/")

@app.post("/sources/<source_id>/delete")
def delete_source(source_id):
    stop_local_recording(source_id); save_sources([x for x in load_sources() if x["id"]!=source_id]); return redirect("/")

@app.post("/api/workers/register")
def register_worker():
    data=request.get_json(force=True); wid=data.get("id") or uuid.uuid4().hex
    with worker_lock: workers[wid]={"id":wid,"name":data.get("name",wid),"host":data.get("host",request.remote_addr),"last_seen_ts":time.time(),"last_seen":time.strftime("%Y-%m-%d %H:%M:%S")}
    return jsonify({"ok":True,"worker_id":wid})

@app.get("/api/health")
def health():
    return jsonify({"ok":True,"version":"0.1","hostname":socket.gethostname()})

@app.get("/recordings")
def recordings():
    items=[str(p.relative_to(RECORDINGS_DIR)) for p in RECORDINGS_DIR.rglob("*") if p.is_file()]
    links="".join(f'<li><a href="/recordings/{item.replace(os.sep,"/")}">{item}</a></li>' for item in items)
    return f"<h1>Recordings</h1><ul>{links}</ul>"

@app.get("/recordings/<path:filename>")
def recording_file(filename):
    return send_from_directory(RECORDINGS_DIR,filename,as_attachment=False)

if __name__=="__main__":
    print("VIC dashboard starting...")
    threading.Timer(1.2,lambda:webbrowser.open("http://127.0.0.1:8765")).start()
    app.run(host="0.0.0.0",port=8765,threaded=True)
