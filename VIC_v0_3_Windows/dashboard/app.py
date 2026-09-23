from __future__ import annotations

import html
import json
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, flash, jsonify, redirect, render_template_string, request, send_from_directory, url_for

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config"
HELP = BASE / "help"
SOURCES_FILE = CONFIG / "sources.json"
JOBS_FILE = CONFIG / "jobs.json"
WORKERS_FILE = CONFIG / "workers.json"
SETTINGS_FILE = CONFIG / "dashboard.json"
LOCK = threading.RLock()

app = Flask(__name__)
app.secret_key = "vic-v0.3-local-dashboard"


def load_json(path: Path, default: Any) -> Any:
    with LOCK:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default


def save_json(path: Path, data: Any) -> None:
    with LOCK:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)


def settings() -> dict[str, Any]:
    return load_json(SETTINGS_FILE, {"port": 8765, "cluster_token": "", "worker_offline_seconds": 12})


def sources() -> list[dict[str, Any]]:
    return load_json(SOURCES_FILE, [])


def jobs() -> list[dict[str, Any]]:
    return load_json(JOBS_FILE, [])


def workers() -> list[dict[str, Any]]:
    items = load_json(WORKERS_FILE, [])
    cutoff = float(settings().get("worker_offline_seconds", 12))
    now = time.time()
    for item in items:
        item["online"] = now - float(item.get("last_seen_ts", 0)) <= cutoff
    return items


def token_ok() -> bool:
    expected = str(settings().get("cluster_token", ""))
    supplied = request.headers.get("X-VIC-Token", "")
    return bool(expected) and supplied == expected


def source_by_id(source_id: str) -> dict[str, Any] | None:
    return next((item for item in sources() if item.get("id") == source_id), None)


def worker_by_id(worker_id: str) -> dict[str, Any] | None:
    return next((item for item in workers() if item.get("id") == worker_id), None)


def active_job_for_source(source_id: str) -> dict[str, Any] | None:
    candidates = [item for item in jobs() if item.get("source_id") == source_id]
    candidates.sort(key=lambda item: item.get("created_ts", 0), reverse=True)
    return next((item for item in candidates if item.get("state") not in {"finished", "failed", "stopped"}), None)


def latest_job_for_source(source_id: str) -> dict[str, Any] | None:
    candidates = [item for item in jobs() if item.get("source_id") == source_id]
    candidates.sort(key=lambda item: item.get("created_ts", 0), reverse=True)
    return candidates[0] if candidates else None


def choose_worker(worker_id: str) -> dict[str, Any] | None:
    online = [item for item in workers() if item.get("online")]
    if worker_id and worker_id != "auto":
        return next((item for item in online if item.get("id") == worker_id), None)
    if not online:
        return None
    return sorted(online, key=lambda item: (float(item.get("cpu", 100)), -float(item.get("disk_free_gb", 0))))[0]


def masked_summary(source: dict[str, Any]) -> str:
    summary = str(source.get("summary", ""))
    if source.get("type") in {"rtsp", "network", "website"}:
        if "@" in summary and "://" in summary:
            scheme, rest = summary.split("://", 1)
            rest = rest.split("@", 1)[-1]
            return f"{scheme}://***:***@{rest}"
        return summary[:100]
    return summary


STYLE = """
<style>
:root{--bg:#0d1015;--panel:#171b22;--panel2:#202630;--line:#343c49;--text:#f3f6f9;--muted:#aeb7c3;--accent:#69adff;--good:#79e18b;--bad:#ff7f7f;--warn:#ffd06c}
*{box-sizing:border-box}body{font-family:Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--text);margin:0}
header{display:flex;align-items:center;gap:20px;padding:18px 28px;background:#151920;border-bottom:1px solid var(--line)}header h1{font-size:22px;margin:0}
nav{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}a,button{color:var(--text)}nav a,.btn,button{background:var(--panel2);border:1px solid #4a5666;border-radius:8px;padding:9px 13px;text-decoration:none;cursor:pointer}
nav a:hover,.btn:hover,button:hover{border-color:var(--accent)}main{max-width:1320px;margin:auto;padding:25px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:14px}.choice{display:block;background:var(--panel2);border:1px solid #3a4452;border-radius:11px;padding:18px;text-decoration:none;min-height:130px}.choice:hover{border-color:var(--accent)}
.choice strong{display:block;font-size:17px;margin-bottom:7px}.muted{color:var(--muted)}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse}th,td{padding:11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}input,select,textarea{width:100%;background:#0f1319;color:var(--text);border:1px solid #4a5666;border-radius:7px;padding:10px;margin:5px 0 14px}
label{font-weight:600}.inline{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.inline>*{margin-top:0}.flash{padding:12px 15px;border:1px solid #475773;background:#222936;border-radius:9px;margin-bottom:15px;white-space:pre-wrap}
.help-tip{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:#36455c;font-size:12px;cursor:help;position:relative;margin-left:5px}.help-tip:hover:after{content:attr(data-tip);position:absolute;z-index:20;left:22px;top:-8px;width:310px;background:#050608;border:1px solid #5b687b;border-radius:8px;padding:10px;font-weight:400}
.tag{display:inline-block;border:1px solid #4c596a;border-radius:999px;padding:3px 8px;font-size:12px;color:var(--muted)}code{word-break:break-all}.small{font-size:13px}.meter{height:9px;background:#0c0f14;border-radius:6px;overflow:hidden}.meter span{display:block;height:100%;background:var(--accent)}
</style>
"""


def page(title: str, body: str, script: str = "") -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)} — VIC</title>{STYLE}</head><body>
<header><h1>VIC — Video Ingest Cluster v0.3</h1><nav><a href='/'>Dashboard</a><a href='/add'>+ Add source</a><a href='/workers'>Workers</a><a href='/jobs'>Jobs</a><a href='/help'>Help</a></nav></header><main>
{{% with messages=get_flashed_messages(with_categories=true) %}}{{% for category,message in messages %}}<div class='flash {{{{category}}}}'>{{{{message}}}}</div>{{% endfor %}}{{% endwith %}}{body}</main>{script}</body></html>"""


SOURCE_TYPES = [
    ("media_file", "🎞️", "Video or audio file", "A file path on the selected worker or a shared network path."),
    ("screen", "🖥️", "Desktop or application window", "Capture the selected worker's desktop, region or application window."),
    ("camera", "🎥", "Camera / capture card / OBS", "DirectShow webcam, USB camera, HDMI capture card or OBS Virtual Camera."),
    ("audio_device", "🎙️", "Microphone or audio device", "Record a DirectShow audio input independently."),
    ("rtsp", "📹", "RTSP / IP camera", "An IP camera or RTSP-producing application."),
    ("network", "📡", "Direct network stream", "HLS/HTTP, SRT, UDP, RTP or another direct FFmpeg-readable stream."),
    ("website", "🌐", "Website video or livestream", "YouTube and other supported pages resolved by yt-dlp."),
    ("folder_watch", "📁", "Watched media folder", "Copy new media files from a folder on the selected worker."),
]


@app.get("/")
def dashboard():
    srcs = sources()
    worker_map = {item["id"]: item for item in workers()}
    for source in srcs:
        job = latest_job_for_source(source["id"])
        source["job"] = job
        source["display_summary"] = masked_summary(source)
        assigned = source.get("worker_id", "auto")
        source["worker_name"] = "Automatic" if assigned == "auto" else worker_map.get(assigned, {}).get("name", "Unavailable worker")
    body = """
<div class='grid'>
<div class='card'><strong>Online workers</strong><p class='{{"good" if online else "bad"}}'>{{online}} online</p></div>
<div class='card'><strong>Sources</strong><p>{{srcs|length}} configured</p></div>
<div class='card'><strong>Active jobs</strong><p>{{active}} running or pending</p></div>
<div class='card'><strong>Cluster mode</strong><p class='good'>Real worker dispatch enabled</p></div>
</div>
<div class='card'><div class='inline' style='justify-content:space-between'><div><h2 style='margin:0'>Media sources</h2><p class='muted'>Test and recording commands run on the selected worker.</p></div><a class='btn' href='/add'>+ Add source</a></div>
<table><tr><th>Source</th><th>Worker</th><th>Latest job</th><th>Actions</th></tr>
{% for source in srcs %}<tr><td><strong>{{source.name}}</strong><br><span class='tag'>{{source.type_label}}</span><br><span class='small muted'>{{source.display_summary}}</span></td><td>{{source.worker_name}}</td>
<td>{% if source.job %}<span class='{{"good" if source.job.state=="running" else "bad" if source.job.state=="failed" else "muted"}}'>{{source.job.state|upper}}</span><br><span class='small muted'>{{source.job.message}}</span>{% else %}<span class='muted'>Never run</span>{% endif %}</td>
<td><form style='display:inline' method='post' action='/sources/{{source.id}}/test'><button>Test</button></form>
<form style='display:inline' method='post' action='/sources/{{source.id}}/start'><button>Start</button></form>
<form style='display:inline' method='post' action='/sources/{{source.id}}/stop'><button>Stop</button></form>
<form style='display:inline' method='post' action='/sources/{{source.id}}/delete' onsubmit='return confirm("Delete this source?")'><button>Delete</button></form></td></tr>
{% else %}<tr><td colspan='4' class='muted'>No sources yet. Start the local worker, then add a video file or desktop source.</td></tr>{% endfor %}</table></div>
<div class='card'><h2>How workers work</h2><p>A source is assigned to Automatic or a named PC. Clicking Start creates a job. The worker receives that job, runs FFmpeg or yt-dlp, and sends its status and output path back here.</p></div>
"""
    worker_items = workers()
    all_jobs = jobs()
    active = sum(1 for item in all_jobs if item.get("state") not in {"finished", "failed", "stopped"})
    return render_template_string(page("Dashboard", body), srcs=srcs, online=sum(1 for w in worker_items if w.get("online")), active=active)


@app.get("/add")
def add_choice():
    body = """<div class='card'><h2>What would you like to ingest?</h2><p class='muted'>Choose a type. Every source can then be assigned to Automatic or a specific worker.</p><div class='grid'>{% for key,icon,name,description in types %}<a class='choice' href='/add/{{key}}'><strong>{{icon}} {{name}}</strong><span class='muted'>{{description}}</span></a>{% endfor %}</div></div>"""
    return render_template_string(page("Add source", body), types=SOURCE_TYPES)


def source_form(source_type: str) -> tuple[str, str, str]:
    common_device_help = "Choose a worker first, then press Load worker devices."
    if source_type == "media_file":
        return "Video or audio file", "The path must exist on the selected worker.", """<label>File path <span class='help-tip' data-tip='For another PC, use a path on that worker or a shared network path.'>?</span></label><input name='path' required placeholder='C:\\Videos\\clip.mp4'><label><input style='width:auto' type='checkbox' name='realtime' checked> Read at normal speed</label><br><label><input style='width:auto' type='checkbox' name='loop'> Loop until stopped</label>"""
    if source_type == "screen":
        return "Desktop or application window", "Captures the selected worker's Windows desktop.", f"""<label>Target</label><select name='target' id='target' onchange='targetChanged()'><option value='desktop'>Entire desktop / all monitors</option><option value='window'>Application window</option></select><div id='windowBox' style='display:none'><label>Exact window title</label><input name='window_title' placeholder='Exact title bar text'></div><label>Frames per second</label><input type='number' name='fps' value='30' min='1' max='120'><label>Optional desktop/loopback audio device <span class='help-tip' data-tip='{common_device_help} Use Stereo Mix or a virtual loopback device for normal speaker sound.'>?</span></label><div class='inline'><input name='audio_device' list='audioDevices' placeholder='Leave blank for no audio'><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='audioDevices'></datalist><details><summary>Advanced capture region</summary><div class='grid'><div><label>X offset</label><input type='number' name='offset_x' value='0'></div><div><label>Y offset</label><input type='number' name='offset_y' value='0'></div><div><label>Width</label><input type='number' name='width' value='0' min='0'></div><div><label>Height</label><input type='number' name='height' value='0' min='0'></div></div></details>"""
    if source_type == "camera":
        return "Camera / capture card / OBS", "Uses a DirectShow video device on the selected worker.", f"""<label>Video device <span class='help-tip' data-tip='{common_device_help}'>?</span></label><div class='inline'><input name='video_device' list='videoDevices' required><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='videoDevices'></datalist><label>Optional audio device</label><input name='audio_device' list='audioDevices'><datalist id='audioDevices'></datalist><label>Resolution</label><input name='resolution' placeholder='1920x1080'><label>Frames per second</label><input type='number' name='fps' value='30' min='1' max='120'>"""
    if source_type == "audio_device":
        return "Microphone or audio device", "Records a DirectShow audio input on the selected worker.", f"""<label>Audio device <span class='help-tip' data-tip='{common_device_help}'>?</span></label><div class='inline'><input name='audio_device' list='audioDevices' required><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='audioDevices'></datalist>"""
    if source_type == "rtsp":
        return "RTSP / IP camera", "The worker connects to the RTSP address.", """<label>RTSP URL</label><input name='url' required placeholder='rtsp://user:password@192.168.1.50:554/stream'><label>Transport</label><select name='transport'><option value='tcp'>TCP — reliable</option><option value='udp'>UDP — lower delay</option></select>"""
    if source_type == "network":
        return "Direct network stream", "For direct HLS, SRT, UDP, RTP or HTTP media addresses.", """<label>Stream address</label><input name='url' required placeholder='https://example/stream.m3u8'>"""
    if source_type == "website":
        return "Website video or livestream", "Uses yt-dlp on the selected worker.", """<label>Website URL <span class='help-tip' data-tip='Use a normal YouTube or other supported webpage URL. Only record media you are entitled to access.'>?</span></label><input name='url' required placeholder='https://www.youtube.com/watch?v=...'><label><input style='width:auto' type='checkbox' name='live_from_start'> For supported livestreams, request from the beginning</label>"""
    if source_type == "folder_watch":
        return "Watched media folder", "Copies new media appearing in a folder on the selected worker.", """<label>Folder path</label><input name='path' required placeholder='D:\\Incoming Media'>"""
    raise KeyError(source_type)


@app.get("/add/<source_type>")
def add_form(source_type: str):
    try:
        name, description, fields = source_form(source_type)
    except KeyError:
        return "Unknown source type", 404
    online_workers = [w for w in workers() if w.get("online")]
    body = """<div class='card'><h2>Add {{type_name}}</h2><p class='muted'>{{description}}</p><form method='post' action='/add/{{source_type}}'><label>Source name</label><input name='name' required placeholder='Friendly source name'><label>Worker <span class='help-tip' data-tip='Automatic chooses an online worker with lower CPU use and more free disk.'>?</span></label><select id='workerSelect' name='worker_id'><option value='auto'>Automatic</option>{% for worker in workers %}<option value='{{worker.id}}'>{{worker.name}} — {{worker.host}}</option>{% endfor %}</select>{{fields|safe}}<div class='inline'><button>Save source</button><a class='btn' href='/add'>Back</a></div></form></div>"""
    script = """<script>
function targetChanged(){const box=document.getElementById('windowBox');if(box)box.style.display=document.getElementById('target').value==='window'?'block':'none';}
async function loadDevices(){let id=document.getElementById('workerSelect').value;if(id==='auto'){const r=await fetch('/api/automatic-worker');const d=await r.json();if(!d.id){alert('No online worker. Start a worker first.');return;}id=d.id;}const r=await fetch('/api/workers/'+id+'/inventory');const d=await r.json();if(d.error){alert(d.error);return;}const v=document.getElementById('videoDevices'),a=document.getElementById('audioDevices');if(v){v.innerHTML='';(d.video||[]).forEach(x=>v.appendChild(new Option(x,x)));}if(a){a.innerHTML='';(d.audio||[]).forEach(x=>a.appendChild(new Option(x,x)));}alert('Loaded '+(d.video||[]).length+' video and '+(d.audio||[]).length+' audio devices.');}
</script>"""
    return render_template_string(page("Add source", body, script), type_name=name, description=description, source_type=source_type, fields=fields, workers=online_workers)


@app.post("/add/<source_type>")
def save_source(source_type: str):
    try:
        type_label, _, _ = source_form(source_type)
    except KeyError:
        return "Unknown source type", 404
    name = request.form.get("name", "").strip()
    worker_id = request.form.get("worker_id", "auto").strip() or "auto"
    if not name:
        flash("A source name is required.", "bad")
        return redirect(url_for("add_form", source_type=source_type))
    form = request.form
    if source_type == "media_file":
        options = {"path": form.get("path", "").strip(), "realtime": "realtime" in form, "loop": "loop" in form}; summary = options["path"]
    elif source_type == "screen":
        options = {"target": form.get("target", "desktop"), "window_title": form.get("window_title", "").strip(), "fps": int(form.get("fps", 30)), "audio_device": form.get("audio_device", "").strip(), "offset_x": int(form.get("offset_x", 0)), "offset_y": int(form.get("offset_y", 0)), "width": int(form.get("width", 0)), "height": int(form.get("height", 0))}; summary = options["window_title"] or "Entire desktop"
    elif source_type == "camera":
        options = {"video_device": form.get("video_device", "").strip(), "audio_device": form.get("audio_device", "").strip(), "resolution": form.get("resolution", "").strip(), "fps": int(form.get("fps", 30))}; summary = options["video_device"]
    elif source_type == "audio_device":
        options = {"audio_device": form.get("audio_device", "").strip()}; summary = options["audio_device"]
    elif source_type == "rtsp":
        options = {"url": form.get("url", "").strip(), "transport": form.get("transport", "tcp")}; summary = options["url"]
    elif source_type == "network":
        options = {"url": form.get("url", "").strip()}; summary = options["url"]
    elif source_type == "website":
        options = {"url": form.get("url", "").strip(), "live_from_start": "live_from_start" in form}; summary = options["url"]
    elif source_type == "folder_watch":
        options = {"path": form.get("path", "").strip()}; summary = options["path"]
    else:
        return "Unsupported source", 400
    data = sources(); data.append({"id": uuid.uuid4().hex[:12], "name": name, "type": source_type, "type_label": type_label, "worker_id": worker_id, "options": options, "summary": summary, "created": time.strftime("%Y-%m-%d %H:%M:%S")}); save_json(SOURCES_FILE, data)
    flash(f'Source "{name}" added. Click Test before Start.', "good")
    return redirect("/")


def create_job(source: dict[str, Any], mode: str) -> tuple[bool, str]:
    selected = choose_worker(source.get("worker_id", "auto"))
    if not selected:
        return False, "No suitable online worker is available. Start a worker and wait for it to appear online."
    current = active_job_for_source(source["id"])
    if current:
        return False, f'An active job already exists: {current.get("state", "unknown")}'
    now = time.time(); item = {"id": uuid.uuid4().hex, "source_id": source["id"], "source_name": source["name"], "worker_id": selected["id"], "worker_name": selected["name"], "source": source, "mode": mode, "desired_state": "running", "state": "pending", "message": "Waiting for worker", "output": "", "created_ts": now, "updated_ts": now}
    data = jobs(); data.append(item); save_json(JOBS_FILE, data)
    return True, f'{mode.title()} job sent to {selected["name"]}.'


@app.post("/sources/<source_id>/test")
def test_source(source_id: str):
    source = source_by_id(source_id)
    if not source: return "Source not found", 404
    ok, message = create_job(source, "test"); flash(message, "good" if ok else "bad"); return redirect("/")


@app.post("/sources/<source_id>/start")
def start_source(source_id: str):
    source = source_by_id(source_id)
    if not source: return "Source not found", 404
    ok, message = create_job(source, "record"); flash(message, "good" if ok else "bad"); return redirect("/")


@app.post("/sources/<source_id>/stop")
def stop_source(source_id: str):
    data = jobs(); candidates = [j for j in data if j.get("source_id") == source_id and j.get("state") not in {"finished", "failed", "stopped"}]
    if not candidates: flash("No active job for this source.", "bad"); return redirect("/")
    target = sorted(candidates, key=lambda j: j.get("created_ts", 0), reverse=True)[0]
    for item in data:
        if item.get("id") == target["id"]: item["desired_state"] = "stopped"; item["state"] = "stopping"; item["message"] = "Stop requested"; item["updated_ts"] = time.time()
    save_json(JOBS_FILE, data); flash("Stop command sent to the worker.", "good"); return redirect("/")


@app.post("/sources/<source_id>/delete")
def delete_source(source_id: str):
    if active_job_for_source(source_id): flash("Stop the active job before deleting this source.", "bad"); return redirect("/")
    data = [item for item in sources() if item.get("id") != source_id]; save_json(SOURCES_FILE, data); flash("Source deleted. Existing recordings were kept on the worker.", "good"); return redirect("/")


@app.get("/workers")
def workers_page():
    body = """<div class='card'><h2>Worker PCs</h2><p class='muted'>Click a worker to view devices and system details.</p><div class='grid'>{% for worker in workers %}<a class='choice' href='/workers/{{worker.id}}'><strong>{{worker.name}}</strong><span class='{{"good" if worker.online else "bad"}}'>{{"ONLINE" if worker.online else "OFFLINE"}}</span><p class='muted'>{{worker.host}}<br>CPU {{worker.cpu}}% · Memory {{worker.memory}}%<br>{{worker.disk_free_gb}} GB free</p></a>{% else %}<div class='card muted'>No workers have registered.</div>{% endfor %}</div></div>"""
    return render_template_string(page("Workers", body), workers=workers())


@app.get("/workers/<worker_id>")
def worker_detail(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker: return "Worker not found", 404
    devices = worker.get("devices", {})
    body = """<div class='card'><h2>{{worker.name}}</h2><p class='{{"good" if worker.online else "bad"}}'>{{"ONLINE" if worker.online else "OFFLINE"}}</p><p><strong>Host:</strong> {{worker.host}}<br><strong>Platform:</strong> {{worker.platform}}<br><strong>CPU:</strong> {{worker.cpu}}%<br><strong>Memory:</strong> {{worker.memory}}%<br><strong>Free disk:</strong> {{worker.disk_free_gb}} GB<br><strong>FFmpeg:</strong> <code>{{worker.ffmpeg or "Not found"}}</code></p></div><div class='grid'><div class='card'><h3>Video devices</h3><ul>{% for x in devices.video or [] %}<li>{{x}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Audio devices</h3><ul>{% for x in devices.audio or [] %}<li>{{x}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Screens</h3><ul>{% for x in devices.screens or [] %}<li>{{x}}</li>{% else %}<li class='muted'>No screen details</li>{% endfor %}</ul></div></div>"""
    return render_template_string(page("Worker details", body), worker=worker, devices=devices)


@app.get("/jobs")
def jobs_page():
    data = sorted(jobs(), key=lambda j: j.get("created_ts", 0), reverse=True)[:100]
    body = """<div class='card'><h2>Recent jobs</h2><table><tr><th>Source</th><th>Worker</th><th>Mode</th><th>State</th><th>Message / output</th></tr>{% for job in jobs %}<tr><td>{{job.source_name}}</td><td>{{job.worker_name}}</td><td>{{job.mode}}</td><td class='{{"good" if job.state=="running" else "bad" if job.state=="failed" else "muted"}}'>{{job.state}}</td><td>{{job.message}}{% if job.output %}<br><code>{{job.output}}</code>{% endif %}</td></tr>{% else %}<tr><td colspan='5' class='muted'>No jobs yet.</td></tr>{% endfor %}</table></div>"""
    return render_template_string(page("Jobs", body), jobs=data)


@app.get("/help")
def help_page():
    body = """<div class='card'><h2>Help</h2><div class='grid'><a class='choice' href='/help-file/START_HERE.html'><strong>Start here</strong><span class='muted'>Install and run a first job.</span></a><a class='choice' href='/help-file/WORKERS.html'><strong>Worker PCs</strong><span class='muted'>How distributed jobs work.</span></a><a class='choice' href='/help-file/WEBSITE_VIDEO.html'><strong>Website video</strong><span class='muted'>YouTube and yt-dlp support.</span></a><a class='choice' href='/help-file/FFMPEG_HELP.html'><strong>FFmpeg</strong><span class='muted'>Install the media engine.</span></a></div></div>"""
    return render_template_string(page("Help", body))


@app.get("/help-file/<path:filename>")
def help_file(filename: str): return send_from_directory(HELP, filename)


@app.get("/api/automatic-worker")
def automatic_worker():
    item = choose_worker("auto")
    return jsonify({"id": item.get("id"), "name": item.get("name")}) if item else jsonify({"id": ""})


@app.get("/api/workers/<worker_id>/inventory")
def worker_inventory(worker_id: str):
    item = worker_by_id(worker_id)
    if not item or not item.get("online"): return jsonify({"error": "Worker is not online."}), 404
    return jsonify(item.get("devices", {"video": [], "audio": [], "screens": []}))


@app.post("/api/worker/heartbeat")
def worker_heartbeat():
    if not token_ok(): return jsonify({"error": "Invalid cluster token"}), 403
    payload = request.get_json(force=True); worker_id = str(payload.get("id", "")).strip()
    if not worker_id: return jsonify({"error": "Missing worker id"}), 400
    data = load_json(WORKERS_FILE, []); now = time.time()
    entry = {"id": worker_id, "name": payload.get("name") or worker_id, "host": payload.get("host", ""), "platform": payload.get("platform", ""), "cpu": payload.get("cpu", 0), "memory": payload.get("memory", 0), "disk_free_gb": payload.get("disk_free_gb", 0), "ffmpeg": payload.get("ffmpeg", ""), "devices": payload.get("devices", {}), "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"), "last_seen_ts": now}
    replaced = False
    for index, old in enumerate(data):
        if old.get("id") == worker_id: data[index] = entry; replaced = True; break
    if not replaced: data.append(entry)
    save_json(WORKERS_FILE, data)
    assigned = [item for item in jobs() if item.get("worker_id") == worker_id and item.get("state") not in {"finished", "failed", "stopped"}]
    return jsonify({"ok": True, "jobs": assigned})


@app.post("/api/worker/job-update")
def job_update():
    if not token_ok(): return jsonify({"error": "Invalid cluster token"}), 403
    payload = request.get_json(force=True); job_id = payload.get("job_id"); data = jobs(); found = False
    allowed = {"state", "message", "output", "pid"}
    for item in data:
        if item.get("id") == job_id:
            for key in allowed:
                if key in payload: item[key] = payload[key]
            item["updated_ts"] = time.time(); found = True; break
    if not found: return jsonify({"error": "Job not found"}), 404
    save_json(JOBS_FILE, data); return jsonify({"ok": True})


@app.get("/api/health")
def health(): return jsonify({"ok": True, "version": "0.3", "workers": len(workers()), "jobs": len(jobs())})


if __name__ == "__main__":
    cfg = settings(); port = int(cfg.get("port", 8765)); address = f"http://127.0.0.1:{port}"
    print("VIC dashboard v0.3"); print("Dashboard:", address); print("Keep this window open.")
    if cfg.get("open_browser_on_start", True): threading.Timer(1.5, lambda: webbrowser.open(address)).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
