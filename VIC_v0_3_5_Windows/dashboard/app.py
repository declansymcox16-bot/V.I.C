from __future__ import annotations

import base64
import html
import json
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    send_from_directory,
    url_for,
)

BASE = Path(__file__).resolve().parent.parent
CONFIG = BASE / "config"
HELP = BASE / "help"
PREVIEW_DIR = BASE / "dashboard_previews"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
SOURCES_FILE = CONFIG / "sources.json"
JOBS_FILE = CONFIG / "jobs.json"
WORKERS_FILE = CONFIG / "workers.json"
SETTINGS_FILE = CONFIG / "dashboard.json"
LOCK = threading.RLock()

app = Flask(__name__)
app.secret_key = "vic-v0.3.5-local-dashboard"


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
    return load_json(
        SETTINGS_FILE,
        {
            "port": 8765,
            "cluster_token": "",
            "worker_offline_seconds": 12,
            "open_browser_on_start": True,
        },
    )


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
        item["display_name"] = (
            f'Local PC (this computer) — {item.get("host", item.get("name", ""))}'
            if item.get("is_local_dashboard")
            else item.get("name", item.get("id", "Worker"))
        )
    items.sort(key=lambda item: (not bool(item.get("is_local_dashboard")), not bool(item.get("online")), str(item.get("display_name", ""))))
    return items


def token_ok() -> bool:
    expected = str(settings().get("cluster_token", ""))
    supplied = request.headers.get("X-VIC-Token", "")
    return bool(expected) and supplied == expected


def source_by_id(source_id: str) -> dict[str, Any] | None:
    return next((item for item in sources() if item.get("id") == source_id), None)


def worker_by_id(worker_id: str) -> dict[str, Any] | None:
    return next((item for item in workers() if item.get("id") == worker_id), None)


def job_by_id(job_id: str) -> dict[str, Any] | None:
    return next((item for item in jobs() if item.get("id") == job_id), None)


def active_job_for_source(source_id: str) -> dict[str, Any] | None:
    candidates = [item for item in jobs() if item.get("source_id") == source_id]
    candidates.sort(key=lambda item: item.get("created_ts", 0), reverse=True)
    return next(
        (
            item
            for item in candidates
            if item.get("state") not in {"finished", "failed", "stopped"}
        ),
        None,
    )


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
    return sorted(
        online,
        key=lambda item: (
            not bool(item.get("is_local_dashboard")),
            float(item.get("cpu", 100)),
            -float(item.get("disk_free_gb", 0)),
        ),
    )[0]


def masked_summary(source: dict[str, Any]) -> str:
    summary = str(source.get("summary", ""))
    if source.get("type") in {"rtsp", "network", "website"}:
        if "@" in summary and "://" in summary:
            scheme, rest = summary.split("://", 1)
            rest = rest.split("@", 1)[-1]
            return f"{scheme}://***:***@{rest}"
        return summary[:100]
    return summary


def audio_percent(level: Any) -> int:
    if level is None:
        return 0
    try:
        value = float(level)
    except (TypeError, ValueError):
        return 0
    return int(max(0, min(100, ((value + 60.0) / 60.0) * 100.0)))


STYLE = """
<style>
:root{--bg:#0d1015;--panel:#171b22;--panel2:#202630;--line:#343c49;--text:#f3f6f9;--muted:#aeb7c3;--accent:#69adff;--good:#79e18b;--bad:#ff7f7f;--warn:#ffd06c}
*{box-sizing:border-box}body{font-family:Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--text);margin:0}
header{display:flex;align-items:center;gap:20px;padding:18px 28px;background:#151920;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}header h1{font-size:22px;margin:0}
nav{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}a,button{color:var(--text)}nav a,.btn,button{background:var(--panel2);border:1px solid #4a5666;border-radius:8px;padding:9px 13px;text-decoration:none;cursor:pointer}
nav a:hover,.btn:hover,button:hover{border-color:var(--accent)}main{max-width:1380px;margin:auto;padding:25px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:14px}.choice{display:block;background:var(--panel2);border:1px solid #3a4452;border-radius:11px;padding:18px;text-decoration:none;min-height:130px}.choice:hover{border-color:var(--accent)}
.choice strong{display:block;font-size:17px;margin-bottom:7px}.muted{color:var(--muted)}.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
table{width:100%;border-collapse:collapse}th,td{padding:11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}input,select,textarea{width:100%;background:#0f1319;color:var(--text);border:1px solid #4a5666;border-radius:7px;padding:10px;margin:5px 0 14px}
label{font-weight:600}.inline{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.inline>*{margin-top:0}.flash{padding:12px 15px;border:1px solid #475773;background:#222936;border-radius:9px;margin-bottom:15px;white-space:pre-wrap}
.help-tip{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:#36455c;font-size:12px;cursor:help;position:relative;margin-left:5px}.help-tip:hover:after{content:attr(data-tip);position:absolute;z-index:20;left:22px;top:-8px;width:320px;background:#050608;border:1px solid #5b687b;border-radius:8px;padding:10px;font-weight:400}
.tag{display:inline-block;border:1px solid #4c596a;border-radius:999px;padding:3px 8px;font-size:12px;color:var(--muted)}code{word-break:break-all}.small{font-size:13px}
.meter{height:18px;background:#080a0d;border:1px solid #3c4654;border-radius:10px;overflow:hidden}.meter span{display:block;height:100%;width:0;background:linear-gradient(90deg,#4fd06b,#e3d64b,#ef5e5e);transition:width .25s}
.preview{display:block;width:100%;max-height:65vh;object-fit:contain;background:#06080b;border:1px solid #3b4654;border-radius:10px}.worker-local{border-color:#4d8acb}.recording-row td{font-size:14px}
</style>
"""


def page(title: str, body: str, script: str = "") -> str:
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)} — VIC</title>{STYLE}</head><body>
<header><h1>VIC — Video Ingest Cluster v0.3.5</h1><nav><a href='/'>Dashboard</a><a href='/add'>+ Add source</a><a href='/mass-capture'>Capture Everything</a><a href='/workers'>Workers</a><a href='/live'>Live</a><a href='/recordings'>Recordings</a><a href='/jobs'>Jobs</a><a href='/help'>Help</a></nav></header><main>
{{% with messages=get_flashed_messages(with_categories=true) %}}{{% for category,message in messages %}}<div class='flash {{{{category}}}}'>{{{{message}}}}</div>{{% endfor %}}{{% endwith %}}{body}</main>{script}</body></html>"""


SOURCE_TYPES = [
    ("media_file", "🎞️", "Video or audio file", "A file path on the selected worker or a shared network path."),
    ("screen", "🖥️", "Desktop, monitor or application window", "Capture the selected PC's whole desktop, one monitor, a region or one window."),
    ("camera", "🎥️", "Camera / capture card / OBS", "DirectShow webcam, USB camera, HDMI capture card or OBS Virtual Camera."),
    ("audio_device", "🎙️", "Microphone or audio input", "Record a DirectShow microphone or audio input independently."),
    ("speaker_output", "🔊", "Speaker or headphone output", "Record audio played through one Windows output using WASAPI loopback."),
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
        source["worker_name"] = (
            "Automatic"
            if assigned == "auto"
            else worker_map.get(assigned, {}).get("display_name", "Unavailable worker")
        )

    all_jobs = jobs()
    body = """
<div class='grid'>
<div class='card'><strong>Online workers</strong><p class='{{"good" if online else "bad"}}'>{{online}} online</p></div>
<div class='card'><strong>Sources</strong><p>{{srcs|length}} configured</p></div>
<div class='card'><strong>Active jobs</strong><p>{{active}} running or pending</p></div>
<div class='card'><strong>Recordings reported</strong><p>{{recording_count}} files</p></div>
</div>
<div class='card'><div class='inline' style='justify-content:space-between'><div><h2 style='margin:0'>Media sources</h2><p class='muted'>Commands run on Automatic, Local PC, or the worker you selected.</p></div>
<div class='inline'>
<form method='post' action='/sources/test-all'><button title='Send a short test job for every configured source'>Test All</button></form>
<form method='post' action='/sources/start-all' onsubmit='return confirm("Start every configured source?")'><button title='Start every source that is not already active'>Start All</button></form>
<form method='post' action='/sources/stop-all'><button title='Stop every active recording and test job'>Stop All</button></form>
<a class='btn' href='/add'>+ Add source</a>
</div></div>
<table><tr><th>Source</th><th>Worker</th><th>Latest job</th><th>Actions</th></tr>
{% for source in srcs %}<tr><td><strong>{{source.name}}</strong><br><span class='tag'>{{source.type_label}}</span><br><span class='small muted'>{{source.display_summary}}</span></td><td>{{source.worker_name}}</td>
<td>{% if source.job %}<span class='{{"good" if source.job.state=="running" else "bad" if source.job.state=="failed" else "muted"}}'>{{source.job.state|upper}}</span><br><span class='small muted'>{{source.job.message}}</span>{% if source.job.audio_level_db is not none %}<br><span class='small'>Audio {{source.job.audio_level_db}} dB</span>{% endif %}{% else %}<span class='muted'>Never run</span>{% endif %}</td>
<td><form style='display:inline' method='post' action='/sources/{{source.id}}/test'><button>Test</button></form>
<form style='display:inline' method='post' action='/sources/{{source.id}}/start'><button>Start</button></form>
<form style='display:inline' method='post' action='/sources/{{source.id}}/stop'><button>Stop</button></form>
{% if source.job %}<a class='btn' target='_blank' href='/live/{{source.job.id}}'>Live view</a>{% endif %}
<form style='display:inline' method='post' action='/sources/{{source.id}}/delete' onsubmit='return confirm("Delete this source?")'><button>Delete</button></form></td></tr>
{% else %}<tr><td colspan='4' class='muted'>No sources yet. Start the local worker, then add a source.</td></tr>{% endfor %}</table></div>
<div class='card'><h2>Local PC</h2><p>When <strong>START_VIC.bat</strong> is running, the worker list should contain <strong>Local PC (this computer)</strong>. Select it for this computer's screens, microphones, speakers and cameras.</p><p><a class='btn' href='/mass-capture'>Capture every selected local device as its own source</a></p></div>
"""
    worker_items = workers()
    active = sum(
        1
        for item in all_jobs
        if item.get("state") not in {"finished", "failed", "stopped"}
    )
    recording_count = sum(len(item.get("recordings", [])) for item in worker_items)
    return render_template_string(
        page("Dashboard", body),
        srcs=srcs,
        online=sum(1 for item in worker_items if item.get("online")),
        active=active,
        recording_count=recording_count,
    )


@app.get("/add")
def add_choice():
    body = """<div class='card'><h2>What would you like to ingest?</h2><p class='muted'>Choose a type. You can assign it to Automatic, Local PC, or another connected worker.</p><div class='grid'>{% for key,icon,name,description in types %}<a class='choice' href='/add/{{key}}'><strong>{{icon}} {{name}}</strong><span class='muted'>{{description}}</span></a>{% endfor %}</div></div>"""
    return render_template_string(page("Add source", body), types=SOURCE_TYPES)


def source_form(source_type: str) -> tuple[str, str, str]:
    common_device_help = "Choose a worker first, then press Load worker devices."
    if source_type == "media_file":
        return (
            "Video or audio file",
            "The path must exist on the selected worker.",
            """<label>File path <span class='help-tip' data-tip='For another PC, use a path on that worker or a shared network path.'>?</span></label><input name='path' required placeholder='C:\\Videos\\clip.mp4'><label><input style='width:auto' type='checkbox' name='realtime' checked> Read at normal speed</label><br><label><input style='width:auto' type='checkbox' name='loop'> Loop until stopped</label>""",
        )
    if source_type == "screen":
        return (
            "Desktop, monitor or application window",
            "Captures the selected worker's Windows display.",
            f"""<label>Capture target</label><select name='target' id='target' onchange='targetChanged()'><option value='desktop'>Entire desktop / all monitors</option><option value='monitor'>One specific monitor</option><option value='window'>Application window</option></select>
<div id='monitorBox' style='display:none'><label>Monitor <span class='help-tip' data-tip='{common_device_help}'>?</span></label><select id='screenSelect' name='screen_id'><option value=''>Load worker devices first</option></select></div>
<div id='windowBox' style='display:none'><label>Exact window title</label><input name='window_title' placeholder='Exact title bar text'></div>
<label>Frames per second</label><input type='number' name='fps' value='30' min='1' max='120'>
<label>Optional desktop/loopback audio device <span class='help-tip' data-tip='{common_device_help} The list now includes microphones/audio inputs and speaker/headphone outputs. Speaker outputs use Windows loopback capture.'>?</span></label>
<div class='inline'><select id='screenAudioSelect' name='audio_choice'><option value=''>No audio</option></select><button type='button' onclick='loadDevices()'>Load worker devices</button></div>
<input type='hidden' id='screenSpeakerName' name='screen_speaker_name'>
<p class='muted small'>Microphone/input choices are added into the screen MKV. Speaker/headphone loopback choices use one shared VIC capture that can feed several screen and speaker sources at once. Each source still receives its own companion WAV and live meter.</p>
<div id='deviceSummary' class='muted small'></div>
<details><summary>Advanced custom region</summary><p class='muted small'>Used with Entire desktop. A selected monitor automatically uses its own coordinates.</p><div class='grid'><div><label>X offset</label><input type='number' name='offset_x' value='0'></div><div><label>Y offset</label><input type='number' name='offset_y' value='0'></div><div><label>Width</label><input type='number' name='width' value='0' min='0'></div><div><label>Height</label><input type='number' name='height' value='0' min='0'></div></div></details>""",
        )
    if source_type == "camera":
        return (
            "Camera / capture card / OBS",
            "Uses a DirectShow video device on the selected worker.",
            f"""<label>Video device <span class='help-tip' data-tip='{common_device_help}'>?</span></label><div class='inline'><input name='video_device' list='videoDevices' required><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='videoDevices'></datalist><label>Optional audio device</label><input name='audio_device' list='audioDevices'><datalist id='audioDevices'></datalist><div id='deviceSummary' class='muted small'></div><label>Resolution</label><input name='resolution' placeholder='1920x1080'><label>Frames per second</label><input type='number' name='fps' value='30' min='1' max='120'>""",
        )
    if source_type == "audio_device":
        return (
            "Microphone or audio device",
            "Records a DirectShow audio input on the selected worker.",
            f"""<label>Audio device <span class='help-tip' data-tip='{common_device_help}'>?</span></label><div class='inline'><input name='audio_device' list='audioDevices' required><button type='button' onclick='loadDevices()'>Load worker devices</button></div><datalist id='audioDevices'></datalist><div id='deviceSummary' class='muted small'></div>""",
        )
    if source_type == "speaker_output":
        return (
            "Speaker or headphone output",
            "Records sound played through one Windows output using WASAPI loopback.",
            f"""<label>Speaker/output device <span class='help-tip' data-tip='{common_device_help} This is different from a microphone.'>?</span></label><div class='inline'><select id='speakerSelect' name='speaker_id' required><option value=''>Load worker devices first</option></select><button type='button' onclick='loadDevices()'>Load worker devices</button></div><input type='hidden' id='speakerName' name='speaker_name'><div id='deviceSummary' class='muted small'></div><p class='muted small'>This captures whatever Windows plays through the selected speakers, headphones, HDMI TV or USB headset. It creates a separate WAV recording.</p>""",
        )
    if source_type == "rtsp":
        return (
            "RTSP / IP camera",
            "The worker connects to the RTSP address.",
            """<label>RTSP URL</label><input name='url' required placeholder='rtsp://user:password@192.168.1.50:554/stream'><label>Transport</label><select name='transport'><option value='tcp'>TCP — reliable</option><option value='udp'>UDP — lower delay</option></select>""",
        )
    if source_type == "network":
        return (
            "Direct network stream",
            "For direct HLS, SRT, UDP, RTP or HTTP media addresses.",
            """<label>Stream address</label><input name='url' required placeholder='https://example/stream.m3u8'>""",
        )
    if source_type == "website":
        return (
            "Website video or livestream",
            "Uses yt-dlp on the selected worker.",
            """<label>Website URL <span class='help-tip' data-tip='Use a normal YouTube or other supported webpage URL. Only record media you are entitled to access.'>?</span></label><input name='url' required placeholder='https://www.youtube.com/watch?v=...'><label><input style='width:auto' type='checkbox' name='live_from_start'> For supported livestreams, request from the beginning</label>""",
        )
    if source_type == "folder_watch":
        return (
            "Watched media folder",
            "Copies new media appearing in a folder on the selected worker.",
            """<label>Folder path</label><input name='path' required placeholder='D:\\Incoming Media'>""",
        )
    raise KeyError(source_type)


@app.get("/add/<source_type>")
def add_form(source_type: str):
    try:
        name, description, fields = source_form(source_type)
    except KeyError:
        return "Unknown source type", 404
    online_workers = [item for item in workers() if item.get("online")]
    body = """<div class='card'><h2>Add {{type_name}}</h2><p class='muted'>{{description}}</p><form method='post' action='/add/{{source_type}}'><label>Source name</label><input name='name' required placeholder='Friendly source name'><label>Worker <span class='help-tip' data-tip='Local PC means the computer running this dashboard. Automatic usually prefers the local PC, then chooses by CPU and disk.'>?</span></label><select id='workerSelect' name='worker_id' onchange='workerChanged()'><option value='auto'>Automatic</option>{% for worker in workers %}<option value='{{worker.id}}'>{{worker.display_name}} — {{worker.host}}</option>{% endfor %}</select>{% if not workers %}<p class='bad'>No online workers. START_VIC.bat should start Local PC automatically.</p>{% endif %}{{fields|safe}}<div class='inline'><button>Save source</button><a class='btn' href='/add'>Back</a></div></form></div>"""
    script = """<script>
function targetChanged(){const target=document.getElementById('target');const win=document.getElementById('windowBox');const mon=document.getElementById('monitorBox');if(win)win.style.display=target.value==='window'?'block':'none';if(mon)mon.style.display=target.value==='monitor'?'block':'none';}
function workerChanged(){const summary=document.getElementById('deviceSummary');if(summary)summary.textContent='Worker changed. Press Load worker devices again.';}
async function loadDevices(){let id=document.getElementById('workerSelect').value;if(id==='auto'){const r=await fetch('/api/automatic-worker');const d=await r.json();if(!d.id){alert('No online worker. Start VIC and wait for Local PC to appear.');return;}id=d.id;}const r=await fetch('/api/workers/'+id+'/inventory');const d=await r.json();if(d.error){alert(d.error);return;}const inputs=(d.audio_inputs||d.audio||[]),speakers=(d.speakers||[]),v=document.getElementById('videoDevices'),a=document.getElementById('audioDevices'),s=document.getElementById('screenSelect'),sp=document.getElementById('speakerSelect'),screenAudio=document.getElementById('screenAudioSelect');if(v){v.innerHTML='';(d.video||[]).forEach(x=>v.appendChild(new Option(x,x)));}if(a){a.innerHTML='';inputs.forEach(x=>a.appendChild(new Option(x,x)));}if(screenAudio){screenAudio.innerHTML='';screenAudio.appendChild(new Option('No audio',''));if(inputs.length){const group=document.createElement('optgroup');group.label='Microphones / audio inputs — mixed into screen recording';inputs.forEach(x=>group.appendChild(new Option(x,'input:'+encodeURIComponent(x))));screenAudio.appendChild(group);}if(speakers.length){const group=document.createElement('optgroup');group.label='Speakers / headphones — Windows loopback companion WAV';speakers.forEach(x=>group.appendChild(new Option(x.label||x.name,'speaker:'+encodeURIComponent(x.id||x.name))));screenAudio.appendChild(group);}screenAudio.onchange=()=>{const hidden=document.getElementById('screenSpeakerName');if(!hidden)return;const selected=screenAudio.options[screenAudio.selectedIndex];hidden.value=screenAudio.value.startsWith('speaker:')?selected.text:'';};screenAudio.dispatchEvent(new Event('change'));}if(s){s.innerHTML='';(d.screens||[]).forEach(x=>s.appendChild(new Option(x.label||x.name,x.id)));if(!(d.screens||[]).length)s.appendChild(new Option('No monitors reported',''));}if(sp){sp.innerHTML='';speakers.forEach(x=>sp.appendChild(new Option(x.label||x.name,x.id)));if(!speakers.length)sp.appendChild(new Option('No speaker outputs reported',''));sp.onchange=()=>{const chosen=speakers.find(x=>x.id===sp.value);const hidden=document.getElementById('speakerName');if(hidden)hidden.value=chosen?chosen.name:'';};sp.dispatchEvent(new Event('change'));}const summary=document.getElementById('deviceSummary');if(summary)summary.textContent='Loaded '+(d.screens||[]).length+' monitor(s), '+(d.video||[]).length+' video device(s), '+inputs.length+' microphone/input device(s), and '+speakers.length+' speaker/output device(s).';}
</script>"""
    return render_template_string(
        page("Add source", body, script),
        type_name=name,
        description=description,
        source_type=source_type,
        fields=fields,
        workers=online_workers,
    )


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
    try:
        if source_type == "media_file":
            options = {
                "path": form.get("path", "").strip(),
                "realtime": "realtime" in form,
                "loop": "loop" in form,
            }
            summary = options["path"]
        elif source_type == "screen":
            audio_choice = form.get("audio_choice", "").strip()
            audio_mode = "none"
            audio_device = ""
            speaker_id = ""
            speaker_name = form.get("screen_speaker_name", "").strip()
            if audio_choice.startswith("input:"):
                from urllib.parse import unquote
                audio_mode = "input"
                audio_device = unquote(audio_choice[len("input:"):])
            elif audio_choice.startswith("speaker:"):
                from urllib.parse import unquote
                audio_mode = "speaker"
                speaker_id = unquote(audio_choice[len("speaker:"):])
            options = {
                "target": form.get("target", "desktop"),
                "screen_id": form.get("screen_id", "").strip(),
                "window_title": form.get("window_title", "").strip(),
                "fps": int(form.get("fps", 30)),
                "audio_mode": audio_mode,
                "audio_device": audio_device,
                "speaker_id": speaker_id,
                "speaker_name": speaker_name,
                "samplerate": 48000,
                "offset_x": int(form.get("offset_x", 0)),
                "offset_y": int(form.get("offset_y", 0)),
                "width": int(form.get("width", 0)),
                "height": int(form.get("height", 0)),
            }
            if options["target"] == "monitor" and not options["screen_id"]:
                flash("Choose a monitor after loading worker devices.", "bad")
                return redirect(url_for("add_form", source_type=source_type))
            summary = (
                options["window_title"]
                if options["target"] == "window"
                else options["screen_id"]
                if options["target"] == "monitor"
                else "Entire desktop"
            )
        elif source_type == "camera":
            options = {
                "video_device": form.get("video_device", "").strip(),
                "audio_device": form.get("audio_device", "").strip(),
                "resolution": form.get("resolution", "").strip(),
                "fps": int(form.get("fps", 30)),
            }
            summary = options["video_device"]
        elif source_type == "audio_device":
            options = {"audio_device": form.get("audio_device", "").strip()}
            summary = options["audio_device"]
        elif source_type == "speaker_output":
            options = {
                "speaker_id": form.get("speaker_id", "").strip(),
                "speaker_name": form.get("speaker_name", "").strip(),
                "samplerate": 48000,
            }
            if not options["speaker_id"]:
                flash("Load worker devices and choose a speaker/output device.", "bad")
                return redirect(url_for("add_form", source_type=source_type))
            summary = options["speaker_name"] or options["speaker_id"]
        elif source_type == "rtsp":
            options = {
                "url": form.get("url", "").strip(),
                "transport": form.get("transport", "tcp"),
            }
            summary = options["url"]
        elif source_type == "network":
            options = {"url": form.get("url", "").strip()}
            summary = options["url"]
        elif source_type == "website":
            options = {
                "url": form.get("url", "").strip(),
                "live_from_start": "live_from_start" in form,
            }
            summary = options["url"]
        elif source_type == "folder_watch":
            options = {"path": form.get("path", "").strip()}
            summary = options["path"]
        else:
            return "Unsupported source", 400
    except ValueError:
        flash("One of the number settings is invalid.", "bad")
        return redirect(url_for("add_form", source_type=source_type))

    data = sources()
    data.append(
        {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "type": source_type,
            "type_label": type_label,
            "worker_id": worker_id,
            "options": options,
            "summary": summary,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_json(SOURCES_FILE, data)
    flash(f'Source "{name}" added. Click Test before Start.', "good")
    return redirect("/")


def create_job(source: dict[str, Any], mode: str) -> tuple[bool, str]:
    selected = choose_worker(source.get("worker_id", "auto"))
    if not selected:
        return False, "No suitable online worker is available. Start VIC and wait for Local PC to appear online."
    current = active_job_for_source(source["id"])
    if current:
        return False, f'An active job already exists: {current.get("state", "unknown")}'
    now = time.time()
    item = {
        "id": uuid.uuid4().hex,
        "source_id": source["id"],
        "source_name": source["name"],
        "worker_id": selected["id"],
        "worker_name": selected.get("display_name", selected.get("name", "Worker")),
        "source": source,
        "mode": mode,
        "desired_state": "running",
        "state": "pending",
        "message": "Waiting for worker",
        "output": "",
        "audio_level_db": None,
        "preview_available": False,
        "created_ts": now,
        "updated_ts": now,
    }
    data = jobs()
    data.append(item)
    save_json(JOBS_FILE, data)
    return True, f'{mode.title()} job sent to {item["worker_name"]}.'


def queue_all_sources(mode: str) -> tuple[int, list[str]]:
    configured = sources()
    queued = 0
    failures: list[str] = []

    for source in configured:
        ok, message = create_job(source, mode)
        if ok:
            queued += 1
        else:
            failures.append(f'{source.get("name", "Source")}: {message}')

    return queued, failures


@app.post("/sources/test-all")
def test_all_sources():
    configured = sources()
    if not configured:
        flash("There are no configured sources to test.", "bad")
        return redirect("/")

    queued, failures = queue_all_sources("test")
    message = f"Queued tests for {queued} of {len(configured)} source(s)."
    if failures:
        message += "\nSkipped or failed:\n" + "\n".join(failures[:20])
    flash(message, "good" if queued else "bad")
    return redirect("/")


@app.post("/sources/start-all")
def start_all_sources():
    configured = sources()
    if not configured:
        flash("There are no configured sources to start.", "bad")
        return redirect("/")

    queued, failures = queue_all_sources("record")
    message = f"Queued recordings for {queued} of {len(configured)} source(s)."
    if failures:
        message += "\nSkipped or failed:\n" + "\n".join(failures[:20])
    flash(message, "good" if queued else "bad")
    return redirect("/")


@app.post("/sources/stop-all")
def stop_all_sources():
    data = jobs()
    stopped = 0
    now = time.time()

    for item in data:
        if item.get("state") not in {"finished", "failed", "stopped"}:
            item["desired_state"] = "stopped"
            item["state"] = "stopping"
            item["message"] = "Stop All requested"
            item["updated_ts"] = now
            stopped += 1

    if stopped:
        save_json(JOBS_FILE, data)
        flash(f"Stop command sent for {stopped} active job(s).", "good")
    else:
        flash("There are no active jobs to stop.", "bad")
    return redirect("/")


@app.post("/sources/<source_id>/test")
def test_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    ok, message = create_job(source, "test")
    flash(message, "good" if ok else "bad")
    return redirect("/")


@app.post("/sources/<source_id>/start")
def start_source(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    ok, message = create_job(source, "record")
    flash(message, "good" if ok else "bad")
    return redirect("/")


@app.post("/sources/<source_id>/stop")
def stop_source(source_id: str):
    data = jobs()
    candidates = [
        item
        for item in data
        if item.get("source_id") == source_id
        and item.get("state") not in {"finished", "failed", "stopped"}
    ]
    if not candidates:
        flash("No active job for this source.", "bad")
        return redirect("/")
    target = sorted(candidates, key=lambda item: item.get("created_ts", 0), reverse=True)[0]
    for item in data:
        if item.get("id") == target["id"]:
            item["desired_state"] = "stopped"
            item["state"] = "stopping"
            item["message"] = "Stop requested"
            item["updated_ts"] = time.time()
    save_json(JOBS_FILE, data)
    flash("Stop command sent to the worker.", "good")
    return redirect("/")


@app.post("/sources/<source_id>/delete")
def delete_source(source_id: str):
    if active_job_for_source(source_id):
        flash("Stop the active job before deleting this source.", "bad")
        return redirect("/")
    save_json(SOURCES_FILE, [item for item in sources() if item.get("id") != source_id])
    flash("Source deleted. Existing recordings were kept on the worker.", "good")
    return redirect("/")


@app.get("/mass-capture")
def mass_capture_page():
    online_workers = [item for item in workers() if item.get("online")]
    body = """<div class='card'><h2>Capture Everything</h2><p class='muted'>Load one worker's inventory, review the checklist, then create one independent source for every checked device.</p>
<form method='post' action='/mass-capture' id='massForm'><label>Worker</label><select id='massWorker' name='worker_id'><option value=''>Choose a worker</option>{% for worker in workers %}<option value='{{worker.id}}'>{{worker.display_name}} — {{worker.host}}</option>{% endfor %}</select>
<div class='inline'><button type='button' onclick='loadMassDevices()'>Load all devices</button><button type='button' onclick='setAll(true)'>Select all</button><button type='button' onclick='setAll(false)'>Clear all</button></div>
<p id='massSummary' class='muted'>Nothing loaded yet.</p><div id='massDevices'></div>
<div class='card'><h3>What should VIC do?</h3><label><input style='width:auto' type='radio' name='action' value='create' checked> Create selected sources only</label><br><label><input style='width:auto' type='radio' name='action' value='start'> Create and immediately start selected sources</label><p class='warn'>Starting everything can use substantial CPU, USB bandwidth and disk space.</p><button>Create selected sources</button></div></form></div>"""
    script = """<script>
function esc(s){return String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#039;'}[c]));}
function section(title,kind,items,labelFn){if(!items.length)return `<div class='card'><h3>${title}</h3><p class='muted'>None reported.</p></div>`;return `<div class='card'><h3>${title}</h3>`+items.map((x,i)=>{const id=typeof x==='string'?x:x.id;const label=labelFn(x);const value=kind+':'+btoa(unescape(encodeURIComponent(JSON.stringify({id:id,name:typeof x==='string'?x:(x.name||label),label:label}))));return `<label style='display:block;margin:8px 0'><input class='massCheck' style='width:auto' type='checkbox' name='device' value='${value}' checked> ${esc(label)}</label>`;}).join('')+'</div>';}
async function loadMassDevices(){const id=document.getElementById('massWorker').value;if(!id){alert('Choose a worker first.');return;}const r=await fetch('/api/workers/'+id+'/inventory');const d=await r.json();if(d.error){alert(d.error);return;}let html='';html+=section('Monitors','screen',d.screens||[],x=>x.label||x.name);html+=section('Microphones / audio inputs','mic',d.audio_inputs||d.audio||[],x=>x);html+=section('Speakers / headphones','speaker',d.speakers||[],x=>x.label||x.name);html+=section('Cameras / capture devices','camera',d.video||[],x=>x);document.getElementById('massDevices').innerHTML=html;document.querySelectorAll('.massCheck').forEach(x=>x.addEventListener('change',updateEstimate));updateEstimate();}
function updateEstimate(){const selected=[...document.querySelectorAll('.massCheck:checked')];const counts={screen:0,mic:0,speaker:0,camera:0};selected.forEach(x=>{const k=x.value.split(':',1)[0];if(k in counts)counts[k]++;});const disk=(counts.screen*4+counts.camera*3+counts.mic*.7+counts.speaker*.7).toFixed(1);const cpu=counts.screen*15+counts.camera*10+counts.mic+counts.speaker;document.getElementById('massSummary').textContent='Selected '+selected.length+' source(s): '+counts.screen+' screen(s), '+counts.mic+' microphone(s), '+counts.speaker+' speaker output(s), '+counts.camera+' camera(s). Rough upper estimate: '+cpu+'% CPU load score and '+disk+' GB per hour. Actual use depends on resolution, frame rate and activity.';}
function setAll(value){document.querySelectorAll('.massCheck').forEach(x=>x.checked=value);updateEstimate();}
</script>"""
    return render_template_string(page("Capture Everything", body, script), workers=online_workers)


@app.post("/mass-capture")
def mass_capture_create():
    worker_id = request.form.get("worker_id", "").strip()
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("Choose an online worker and load its devices first.", "bad")
        return redirect("/mass-capture")
    raw_devices = request.form.getlist("device")
    if not raw_devices:
        flash("No devices were selected.", "bad")
        return redirect("/mass-capture")
    created: list[dict[str, Any]] = []
    data = sources()
    for raw in raw_devices:
        try:
            kind, encoded = raw.split(":", 1)
            payload = json.loads(base64.b64decode(encoded).decode("utf-8"))
            device_id = str(payload.get("id", ""))
            device_name = str(payload.get("name", "") or payload.get("label", "") or device_id)
            label = str(payload.get("label", "") or device_name)
        except Exception:
            continue
        if kind == "screen":
            source_type = "screen"
            type_label = "Desktop, monitor or application window"
            options = {"target": "monitor", "screen_id": device_id, "window_title": "", "fps": 30, "audio_device": "", "offset_x": 0, "offset_y": 0, "width": 0, "height": 0}
            name = label
            summary = label
        elif kind == "mic":
            source_type = "audio_device"
            type_label = "Microphone or audio input"
            options = {"audio_device": device_name}
            name = device_name
            summary = device_name
        elif kind == "speaker":
            source_type = "speaker_output"
            type_label = "Speaker or headphone output"
            options = {"speaker_id": device_id, "speaker_name": device_name, "samplerate": 48000}
            name = device_name + " output"
            summary = label
        elif kind == "camera":
            source_type = "camera"
            type_label = "Camera / capture card / OBS"
            options = {"video_device": device_name, "audio_device": "", "resolution": "", "fps": 30}
            name = device_name
            summary = device_name
        else:
            continue
        source = {"id": uuid.uuid4().hex[:12], "name": name, "type": source_type, "type_label": type_label, "worker_id": worker_id, "options": options, "summary": summary, "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        data.append(source)
        created.append(source)
    save_json(SOURCES_FILE, data)
    started = 0
    errors: list[str] = []
    if request.form.get("action") == "start":
        for source in created:
            ok, message = create_job(source, "record")
            if ok:
                started += 1
            else:
                errors.append(f'{source["name"]}: {message}')
    message = f"Created {len(created)} separate source(s)."
    if request.form.get("action") == "start":
        message += f" Started {started}."
    if errors:
        message += "\n" + "\n".join(errors[:10])
    flash(message, "good" if created else "bad")
    return redirect("/")


@app.get("/workers")
def workers_page():
    body = """<div class='card'><h2>Worker PCs</h2><p class='muted'>Local PC means the computer running the dashboard. Click any worker to view its monitors and devices.</p><div class='grid'>{% for worker in workers %}<a class='choice {{"worker-local" if worker.is_local_dashboard else ""}}' href='/workers/{{worker.id}}'><strong>{{worker.display_name}}</strong><span class='{{"good" if worker.online else "bad"}}'>{{"ONLINE" if worker.online else "OFFLINE"}}</span><p class='muted'>{{worker.host}}<br>CPU {{worker.cpu}}% · Memory {{worker.memory}}%<br>{{worker.disk_free_gb}} GB free<br>{{worker.recordings|length}} recording file(s)</p></a>{% else %}<div class='card muted'>No workers have registered. Run START_VIC.bat.</div>{% endfor %}</div></div>"""
    return render_template_string(page("Workers", body), workers=workers())


@app.get("/workers/<worker_id>")
def worker_detail(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker:
        return "Worker not found", 404
    devices = worker.get("devices", {})
    body = """<div class='card'><h2>{{worker.display_name}}</h2><p class='{{"good" if worker.online else "bad"}}'>{{"ONLINE" if worker.online else "OFFLINE"}}</p><p><strong>Host:</strong> {{worker.host}}<br><strong>Platform:</strong> {{worker.platform}}<br><strong>CPU:</strong> {{worker.cpu}}%<br><strong>Memory:</strong> {{worker.memory}}%<br><strong>Free disk:</strong> {{worker.disk_free_gb}} GB<br><strong>Recordings:</strong> <code>{{worker.recordings_root or "Not reported"}}</code><br><strong>FFmpeg:</strong> <code>{{worker.ffmpeg or "Not found"}}</code></p><form method='post' action='/workers/{{worker.id}}/open-recordings'><button>Open recording folder on this PC</button></form></div><div class='grid'><div class='card'><h3>Video devices</h3><ul>{% for item in devices.video or [] %}<li>{{item}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Microphones / audio inputs</h3><ul>{% for item in devices.audio_inputs or devices.audio or [] %}<li>{{item}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Speakers / audio outputs</h3><ul>{% for item in devices.speakers or [] %}<li>{{item.label or item.name}}</li>{% else %}<li class='muted'>None reported</li>{% endfor %}</ul></div><div class='card'><h3>Monitors</h3><ul>{% for item in devices.screens or [] %}<li>{{item.label}}</li>{% else %}<li class='muted'>No monitor details</li>{% endfor %}</ul></div></div>"""
    return render_template_string(page("Worker details", body), worker=worker, devices=devices)


@app.post("/workers/<worker_id>/open-recordings")
def open_recordings(worker_id: str):
    worker = worker_by_id(worker_id)
    if not worker or not worker.get("online"):
        flash("That worker is not online.", "bad")
        return redirect("/recordings")
    now = time.time()
    job = {
        "id": uuid.uuid4().hex,
        "source_id": f"system-open-{worker_id}",
        "source_name": "Open recordings folder",
        "worker_id": worker_id,
        "worker_name": worker.get("display_name", worker.get("name", "Worker")),
        "source": {"id": "system", "name": "System", "type": "control", "options": {}},
        "mode": "open_recordings",
        "desired_state": "running",
        "state": "pending",
        "message": "Waiting for worker",
        "output": "",
        "created_ts": now,
        "updated_ts": now,
    }
    data = jobs()
    data.append(job)
    save_json(JOBS_FILE, data)
    flash(f'Open-folder command sent to {job["worker_name"]}.', "good")
    return redirect("/recordings")


@app.get("/live")
def live_page():
    data = sorted(jobs(), key=lambda item: item.get("created_ts", 0), reverse=True)[:100]
    body = """<div class='card'><h2>Live source monitor</h2><p class='muted'>Open a source in a separate tab for a refreshed preview, recording status and audio meter.</p><table><tr><th>Source</th><th>Worker</th><th>State</th><th>Audio</th><th>Open</th></tr>{% for job in jobs %}<tr><td>{{job.source_name}}</td><td>{{job.worker_name}}</td><td class='{{"good" if job.state=="running" else "bad" if job.state=="failed" else "muted"}}'>{{job.state}}</td><td>{% if job.audio_level_db is not none %}{{job.audio_level_db}} dB{% else %}<span class='muted'>No meter</span>{% endif %}</td><td><a class='btn' target='_blank' href='/live/{{job.id}}'>Open live tab</a></td></tr>{% else %}<tr><td colspan='5' class='muted'>No jobs yet.</td></tr>{% endfor %}</table></div>"""
    return render_template_string(page("Live", body), jobs=data)


@app.get("/live/<job_id>")
def live_job(job_id: str):
    job = job_by_id(job_id)
    if not job:
        return "Job not found", 404
    body = """<div class='card'><h2>{{job.source_name}}</h2><p><strong>Worker:</strong> {{job.worker_name}}<br><strong>Status:</strong> <span id='state'>{{job.state}}</span><br><strong>Message:</strong> <span id='message'>{{job.message}}</span><br><strong>Output:</strong> <code id='output'>{{job.output}}</code></p></div>
<div class='card'><h3>Audio level</h3><div class='meter'><span id='meter'></span></div><p id='audioText' class='muted'>Waiting for meter data...</p><p class='small muted'>A moving bar confirms that the selected audio input is producing a measurable signal. No bar can mean silence, the wrong device, or a source type without a live meter.</p></div>
<div class='card'><h3>Refreshed video preview</h3><img id='preview' class='preview' src='/previews/{{job.id}}.jpg?t={{stamp}}' onerror='this.style.display="none"'><p id='previewText' class='muted'>The image refreshes about every two seconds when the worker can create a preview. This is monitoring, not zero-delay video.</p></div>"""
    script = """<script>
const jobId={{job_id|tojson}};
async function refreshStatus(){try{const r=await fetch('/api/jobs/'+jobId+'/status');const d=await r.json();document.getElementById('state').textContent=d.state||'';document.getElementById('message').textContent=d.message||'';document.getElementById('output').textContent=d.output||'';const meter=document.getElementById('meter');meter.style.width=(d.audio_percent||0)+'%';document.getElementById('audioText').textContent=d.audio_level_db===null?'No live audio meter is available for this source.':d.audio_level_db.toFixed(1)+' dB';const img=document.getElementById('preview');if(d.preview_available){img.style.display='block';img.src='/previews/'+jobId+'.jpg?t='+Date.now();}else{document.getElementById('previewText').textContent='No preview has been received yet. Audio-only, website, folder and some locked capture sources may not provide one.';}}catch(e){document.getElementById('message').textContent='Dashboard status error: '+e;}}
refreshStatus();setInterval(refreshStatus,1000);
</script>"""
    return render_template_string(
        page("Live source", body, script),
        job=job,
        stamp=time.time(),
        job_id=job_id,
    )


@app.get("/recordings")
def recordings_page():
    worker_items = workers()
    body = """<div class='card'><h2>Recordings</h2><p class='muted'>These files are reported by each worker. Remote files stay on the remote PC unless the source records to a shared network folder.</p></div>
{% for worker in workers %}<div class='card'><div class='inline' style='justify-content:space-between'><div><h3 style='margin:0'>{{worker.display_name}}</h3><p class='muted'><code>{{worker.recordings_root or "Recording folder not reported"}}</code> · {{"ONLINE" if worker.online else "OFFLINE"}}</p></div><form method='post' action='/workers/{{worker.id}}/open-recordings'><button>Open folder on worker</button></form></div>
<table><tr><th>File</th><th>Folder</th><th>Size</th><th>Modified</th><th>Complete path</th></tr>{% for item in worker.recordings or [] %}<tr class='recording-row'><td>{{item.name}}</td><td>{{item.relative}}</td><td>{{item.size_mb}} MB</td><td>{{item.modified}}</td><td><code>{{item.path}}</code></td></tr>{% else %}<tr><td colspan='5' class='muted'>No recordings reported by this worker.</td></tr>{% endfor %}</table></div>{% else %}<div class='card muted'>No worker information is available.</div>{% endfor %}"""
    return render_template_string(page("Recordings", body), workers=worker_items)


@app.get("/jobs")
def jobs_page():
    data = sorted(jobs(), key=lambda item: item.get("created_ts", 0), reverse=True)[:150]
    body = """<div class='card'><h2>Recent jobs</h2><table><tr><th>Source</th><th>Worker</th><th>Mode</th><th>State</th><th>Message / output</th><th>Live</th></tr>{% for job in jobs %}<tr><td>{{job.source_name}}</td><td>{{job.worker_name}}</td><td>{{job.mode}}</td><td class='{{"good" if job.state=="running" else "bad" if job.state=="failed" else "muted"}}'>{{job.state}}</td><td>{{job.message}}{% if job.output %}<br><code>{{job.output}}</code>{% endif %}</td><td><a class='btn' target='_blank' href='/live/{{job.id}}'>View</a></td></tr>{% else %}<tr><td colspan='6' class='muted'>No jobs yet.</td></tr>{% endfor %}</table></div>"""
    return render_template_string(page("Jobs", body), jobs=data)


@app.get("/help")
def help_page():
    body = """<div class='card'><h2>Help</h2><div class='grid'><a class='choice' href='/help-file/START_HERE.html'><strong>Start here</strong><span class='muted'>Install and run a first job.</span></a><a class='choice' href='/help-file/MASS_CAPTURE.html'><strong>Capture Everything</strong><span class='muted'>Create separate sources for all selected devices.</span></a><a class='choice' href='/help-file/WORKERS.html'><strong>Worker PCs</strong><span class='muted'>How distributed jobs work.</span></a><a class='choice' href='/help-file/LIVE_AND_RECORDINGS.html'><strong>Live and Recordings</strong><span class='muted'>Preview, audio meter and recording locations.</span></a><a class='choice' href='/help-file/WEBSITE_VIDEO.html'><strong>Website video</strong><span class='muted'>YouTube and yt-dlp support.</span></a><a class='choice' href='/help-file/FFMPEG_HELP.html'><strong>FFmpeg</strong><span class='muted'>Install the media engine.</span></a></div></div>"""
    return render_template_string(page("Help", body))


@app.get("/help-file/<path:filename>")
def help_file(filename: str):
    return send_from_directory(HELP, filename)


@app.get("/previews/<job_id>.jpg")
def preview_image(job_id: str):
    return send_from_directory(PREVIEW_DIR, f"{job_id}.jpg", max_age=0)


@app.get("/api/jobs/<job_id>/status")
def job_status(job_id: str):
    job = job_by_id(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    preview = PREVIEW_DIR / f"{job_id}.jpg"
    level = job.get("audio_level_db")
    return jsonify(
        {
            "id": job_id,
            "state": job.get("state", "unknown"),
            "message": job.get("message", ""),
            "output": job.get("output", ""),
            "audio_level_db": level,
            "audio_percent": audio_percent(level),
            "preview_available": preview.is_file(),
            "updated_ts": job.get("updated_ts", 0),
        }
    )


@app.get("/api/automatic-worker")
def automatic_worker():
    item = choose_worker("auto")
    return (
        jsonify({"id": item.get("id"), "name": item.get("display_name")})
        if item
        else jsonify({"id": ""})
    )


@app.get("/api/workers/<worker_id>/inventory")
def worker_inventory(worker_id: str):
    item = worker_by_id(worker_id)
    if not item or not item.get("online"):
        return jsonify({"error": "Worker is not online."}), 404
    return jsonify(item.get("devices", {"video": [], "audio": [], "audio_inputs": [], "speakers": [], "screens": []}))


@app.post("/api/worker/heartbeat")
def worker_heartbeat():
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    payload = request.get_json(force=True)
    worker_id = str(payload.get("id", "")).strip()
    if not worker_id:
        return jsonify({"error": "Missing worker id"}), 400

    active_status = payload.pop("active_status", {}) or {}
    data = load_json(WORKERS_FILE, [])
    now = time.time()
    entry = {
        "id": worker_id,
        "name": payload.get("name") or worker_id,
        "host": payload.get("host", ""),
        "platform": payload.get("platform", ""),
        "is_local_dashboard": bool(payload.get("is_local_dashboard")),
        "cpu": payload.get("cpu", 0),
        "memory": payload.get("memory", 0),
        "disk_free_gb": payload.get("disk_free_gb", 0),
        "recordings_root": payload.get("recordings_root", ""),
        "ffmpeg": payload.get("ffmpeg", ""),
        "devices": payload.get("devices", {}),
        "recordings": payload.get("recordings", []),
        "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_seen_ts": now,
    }
    replaced = False
    for index, old in enumerate(data):
        if old.get("id") == worker_id:
            data[index] = entry
            replaced = True
            break
    if not replaced:
        data.append(entry)
    save_json(WORKERS_FILE, data)

    job_data = jobs()
    jobs_changed = False
    for job_id, status in active_status.items():
        preview_b64 = status.get("preview_b64")
        if preview_b64:
            try:
                (PREVIEW_DIR / f"{job_id}.jpg").write_bytes(base64.b64decode(preview_b64))
            except (OSError, ValueError):
                pass
        for item in job_data:
            if item.get("id") == job_id:
                if "audio_level_db" in status:
                    item["audio_level_db"] = status.get("audio_level_db")
                if status.get("output"):
                    item["output"] = status.get("output")
                if preview_b64 or (PREVIEW_DIR / f"{job_id}.jpg").is_file():
                    item["preview_available"] = True
                item["updated_ts"] = now
                jobs_changed = True
                break
    if jobs_changed:
        save_json(JOBS_FILE, job_data)

    assigned = [
        item
        for item in jobs()
        if item.get("worker_id") == worker_id
        and item.get("state") not in {"finished", "failed", "stopped"}
    ]
    return jsonify({"ok": True, "jobs": assigned})


@app.post("/api/worker/job-update")
def job_update():
    if not token_ok():
        return jsonify({"error": "Invalid cluster token"}), 403
    payload = request.get_json(force=True)
    job_id = payload.get("job_id")
    data = jobs()
    found = False
    allowed = {
        "state",
        "message",
        "output",
        "pid",
        "audio_level_db",
        "preview_available",
    }
    for item in data:
        if item.get("id") == job_id:
            for key in allowed:
                if key in payload:
                    item[key] = payload[key]
            item["updated_ts"] = time.time()
            found = True
            break
    if not found:
        return jsonify({"error": "Job not found"}), 404
    save_json(JOBS_FILE, data)
    return jsonify({"ok": True})


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "version": "0.3.1",
            "workers": len(workers()),
            "jobs": len(jobs()),
        }
    )


if __name__ == "__main__":
    cfg = settings()
    port = int(cfg.get("port", 8765))
    address = f"http://127.0.0.1:{port}"
    print("VIC dashboard v0.3.5")
    print("Dashboard:", address)
    print("Keep this window open.")
    if cfg.get("open_browser_on_start", True):
        threading.Timer(1.5, lambda: webbrowser.open(address)).start()
    app.run(host="0.0.0.0", port=port, threaded=True)
