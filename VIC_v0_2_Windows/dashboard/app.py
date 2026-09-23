from __future__ import annotations

import html
import os
import socket
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from flask import (
    Flask, flash, jsonify, redirect, render_template_string,
    request, send_file, send_from_directory, url_for
)

import media

BASE = Path(__file__).resolve().parent.parent
HELP_DIR = BASE / "help"
LOGS_DIR = BASE / "logs"

app = Flask(__name__)
app.secret_key = "vic-local-dashboard-v0.2"

workers: dict[str, dict[str, Any]] = {}
workers_lock = threading.Lock()


def worker_items() -> list[dict[str, Any]]:
    now = time.time()
    with workers_lock:
        return [
            {**worker, "online": now - worker["last_seen_ts"] < 10}
            for worker in workers.values()
        ]


def source_by_id(source_id: str) -> dict[str, Any] | None:
    return next(
        (source for source in media.load_sources() if source["id"] == source_id),
        None,
    )


def bool_field(name: str) -> bool:
    return request.form.get(name) in {"1", "true", "on", "yes"}


STYLE = """
<style>
:root{--bg:#0e1014;--panel:#171a20;--panel2:#20242c;--line:#333946;--text:#f3f5f7;--muted:#aab0bb;--accent:#61a8ff;--good:#76df88;--bad:#ff7777}
*{box-sizing:border-box}
body{font-family:Segoe UI,Arial,sans-serif;background:var(--bg);color:var(--text);margin:0}
header{background:#17191f;border-bottom:1px solid var(--line);padding:18px 28px;display:flex;gap:24px;align-items:center}
header h1{font-size:22px;margin:0}
nav{margin-left:auto;display:flex;gap:9px;flex-wrap:wrap}
nav a,.button,button{background:var(--panel2);color:var(--text);border:1px solid #4a5160;border-radius:8px;padding:9px 13px;text-decoration:none;cursor:pointer;font-size:14px}
nav a:hover,.button:hover,button:hover{border-color:var(--accent)}
main{max-width:1280px;margin:auto;padding:25px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}
.source-choice{display:block;background:var(--panel2);border:1px solid #3b414d;border-radius:11px;padding:18px;text-decoration:none;color:var(--text);min-height:132px}
.source-choice:hover{border-color:var(--accent);transform:translateY(-1px)}
.source-choice strong{font-size:17px;display:block;margin-bottom:7px}
.muted{color:var(--muted)}.good{color:var(--good)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse}
th,td{padding:11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
input,select,textarea{width:100%;background:#101318;color:var(--text);border:1px solid #4a5160;border-radius:7px;padding:10px;margin:5px 0 14px}
label{font-weight:600}
.inline{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.inline input,.inline select{flex:1;min-width:180px;margin-bottom:0}
.help-tip{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:#364154;color:#fff;font-size:12px;cursor:help;position:relative;margin-left:5px}
.help-tip:hover:after{content:attr(data-tip);position:absolute;z-index:100;left:22px;top:-8px;width:300px;background:#050608;border:1px solid #596272;border-radius:8px;padding:10px;color:#fff;font-weight:400;box-shadow:0 8px 25px #000}
.flash{border-radius:9px;padding:12px 15px;margin-bottom:15px;background:#232936;border:1px solid #46516a;white-space:pre-wrap}
code{word-break:break-all}.small{font-size:13px}
.tag{display:inline-block;border:1px solid #4b5360;border-radius:999px;padding:3px 8px;font-size:12px;color:var(--muted)}
</style>
"""


def page(title: str, body: str, script: str = "") -> str:
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} — VIC</title>{STYLE}</head><body>
<header><h1>VIC — Video Ingest Cluster</h1>
<nav><a href="/">Dashboard</a><a href="/add">+ Add media source</a>
<a href="/recordings">Recordings</a><a href="/workers">Workers</a><a href="/help">Help</a></nav></header>
<main>
{{% with messages = get_flashed_messages(with_categories=true) %}}
{{% for category, message in messages %}}<div class="flash {{{{category}}}}">{{{{message}}}}</div>{{% endfor %}}
{{% endwith %}}
{body}
</main>{script}</body></html>"""


SOURCE_TYPES = [
    ("media_file", "🎞️", "Video or audio file", "MP4, MKV, MOV, AVI, MP3, WAV and most FFmpeg-supported files."),
    ("screen", "🖥️", "Desktop or application window", "Capture the full Windows desktop, a region, monitor coordinates or one program window."),
    ("camera", "🎥", "Camera / capture card / OBS", "Webcams, USB cameras, HDMI capture cards and OBS Virtual Camera."),
    ("audio_device", "🎙️", "Microphone or audio device", "Record a DirectShow microphone or other audio input independently."),
    ("rtsp", "📹", "RTSP / IP camera", "Add an IP camera or another source whose address begins with rtsp://."),
    ("network", "📡", "Network stream", "HTTP/HLS, SRT, UDP, RTP and other FFmpeg-readable addresses."),
    ("folder_watch", "📁", "Watched media folder", "Copy newly appearing media files into VIC storage."),
]


@app.get("/")
def dashboard():
    sources = media.load_sources()
    for source in sources:
        source["status"] = media.source_status(source)

    body = """
<div class="grid">
<div class="card"><strong>FFmpeg</strong><p class="{{'good' if ffmpeg else 'bad'}}">{{ffmpeg or 'Not found'}}</p></div>
<div class="card"><strong>Sources</strong><p>{{sources|length}} configured</p></div>
<div class="card"><strong>Workers</strong><p>{{workers|length}} recently seen</p></div>
<div class="card"><strong>Storage</strong><p><code>{{recordings}}</code></p></div>
</div>

<div class="card">
<div class="inline" style="justify-content:space-between">
<div><h2 style="margin:0">Media sources</h2><p class="muted">Each running source records or ingests independently.</p></div>
<a class="button" href="/add">+ Add media source</a>
</div>
<table><tr><th>Name</th><th>Type</th><th>Status</th><th>Actions</th></tr>
{% for source in sources %}
<tr>
<td><strong>{{source.name}}</strong><br><span class="small muted">{{source.summary}}</span></td>
<td><span class="tag">{{source.type_label}}</span></td>
<td class="{{'good' if source.status in ['RECORDING','RUNNING'] else 'muted'}}">{{source.status}}</td>
<td>
<form style="display:inline" method="post" action="/sources/{{source.id}}/test"><button>Test</button></form>
{% if source.type not in ['audio_device','folder_watch'] %}
<form style="display:inline" method="post" action="/sources/{{source.id}}/preview"><button>Preview image</button></form>
{% endif %}
{% if source.status in ['RECORDING','RUNNING'] %}
<form style="display:inline" method="post" action="/sources/{{source.id}}/stop"><button>Stop</button></form>
{% else %}
<form style="display:inline" method="post" action="/sources/{{source.id}}/start"><button>Start</button></form>
{% endif %}
<form style="display:inline" method="post" action="/sources/{{source.id}}/delete" onsubmit="return confirm('Delete this source?')"><button>Delete</button></form>
</td></tr>
{% else %}
<tr><td colspan="4" class="muted">No sources yet. Add a local video file for the easiest first test.</td></tr>
{% endfor %}
</table></div>

<div class="card"><h2>Start here</h2>
<p>Click <strong>+ Add media source</strong>, choose <strong>Video or audio file</strong>, browse to a file, save it, then click <strong>Test</strong>.</p>
<p><a href="/help">Open built-in help and tutorials</a></p></div>
"""
    return render_template_string(
        page("Dashboard", body),
        sources=sources,
        ffmpeg=media.find_ffmpeg(),
        workers=worker_items(),
        recordings=str(media.recordings_root()),
    )


@app.get("/add")
def add_choice():
    body = """
<div class="card"><h2>What would you like to ingest?</h2>
<p class="muted">Choose a source type. VIC only shows the settings needed for it.</p>
<div class="grid">{% for key, icon, name, description in source_types %}
<a class="source-choice" href="/add/{{key}}"><strong>{{icon}} {{name}}</strong><span class="muted">{{description}}</span></a>
{% endfor %}</div></div>
"""
    return render_template_string(page("Add media source", body), source_types=SOURCE_TYPES)


def form_data(source_type: str) -> tuple[str, str, str, str]:
    if source_type == "media_file":
        return ("Video or audio file", "Select a media file on the dashboard PC.", "Test video", """
<label>Media file <span class="help-tip" data-tip="Use Browse so you do not have to type the full Windows path.">?</span></label>
<div class="inline"><input id="path" name="path" required placeholder="C:\\Videos\\example.mp4"><button type="button" onclick="pickFile()">Browse</button></div><br>
<label><input style="width:auto" type="checkbox" name="realtime" checked> Read at normal playback speed</label><br>
<label><input style="width:auto" type="checkbox" name="loop"> Loop until stopped</label>""")

    if source_type == "screen":
        return ("Desktop or application window", "Capture the Windows desktop, coordinates, or one window.", "Main desktop", """
<label>Capture target <span class="help-tip" data-tip="Entire desktop captures all displays as one desktop. Window capture uses the exact title bar text.">?</span></label>
<select id="target" name="target" onchange="targetChanged()"><option value="desktop">Entire desktop / all monitors</option><option value="window">Application window</option></select>
<div id="window_fields" style="display:none"><label>Exact window title</label><input name="window_title" placeholder="Exact title shown at the top of the window"></div>
<label>Frames per second <span class="help-tip" data-tip="30 is suitable for most recordings. 60 uses more CPU and disk space.">?</span></label>
<input type="number" name="fps" value="30" min="1" max="120">
<details><summary>Advanced region and optional audio</summary>
<p class="muted small">Leave width and height at 0 for the full target.</p>
<div class="grid"><div><label>X offset</label><input type="number" name="offset_x" value="0"></div>
<div><label>Y offset</label><input type="number" name="offset_y" value="0"></div>
<div><label>Width</label><input type="number" name="width" value="0" min="0"></div>
<div><label>Height</label><input type="number" name="height" value="0" min="0"></div></div>
<label>Optional audio device</label>
<div class="inline"><select id="audio_device" name="audio_device"><option value="">No audio</option></select><button type="button" onclick="detectDevices()">Detect devices</button></div>
</details>""")

    if source_type == "camera":
        return ("Camera / capture card / OBS", "Select a Windows DirectShow video device.", "USB camera", """
<label>Video device <span class="help-tip" data-tip="This can be a webcam, HDMI capture card, USB camera or OBS Virtual Camera.">?</span></label>
<div class="inline"><select id="video_device" name="video_device" required><option value="">Click Detect devices</option></select><button type="button" onclick="detectDevices()">Detect devices</button></div><br>
<label>Optional audio device</label><select id="audio_device" name="audio_device"><option value="">No audio</option></select>
<label>Resolution <span class="help-tip" data-tip="Leave blank for automatic. Example: 1920x1080.">?</span></label><input name="resolution" placeholder="1920x1080">
<label>Frames per second</label><input type="number" name="fps" value="30" min="1" max="120">""")

    if source_type == "audio_device":
        return ("Microphone or audio device", "Select a Windows DirectShow audio input.", "Microphone", """
<label>Audio device</label><div class="inline"><select id="audio_device" name="audio_device" required><option value="">Click Detect devices</option></select><button type="button" onclick="detectDevices()">Detect devices</button></div>""")

    if source_type == "rtsp":
        return ("RTSP / IP camera", "Enter the RTSP address supplied by the camera or software.", "Front IP camera", """
<label>RTSP URL <span class="help-tip" data-tip="Usually begins with rtsp:// and may contain a username, password, IP, port and path.">?</span></label>
<input name="url" required placeholder="rtsp://user:password@192.168.1.50:554/stream">
<label>Transport</label><select name="transport"><option value="tcp">TCP — more reliable</option><option value="udp">UDP — lower delay</option></select>""")

    if source_type == "network":
        return ("Network stream", "Enter an HTTP/HLS, SRT, UDP, RTP or other FFmpeg-readable address.", "Network stream", """
<label>Stream URL or address <span class="help-tip" data-tip="Examples: https://site/stream.m3u8, srt://host:port, udp://host:port.">?</span></label>
<input name="url" required placeholder="https://example/stream.m3u8">""")

    if source_type == "folder_watch":
        return ("Watched media folder", "Copy newly appearing media files into VIC storage.", "Incoming media", """
<label>Folder to watch</label><div class="inline"><input id="path" name="path" required placeholder="D:\\Incoming Media"><button type="button" onclick="pickFolder()">Browse</button></div>""")

    raise KeyError(source_type)


@app.get("/add/<source_type>")
def add_form(source_type: str):
    try:
        type_name, description, example, fields = form_data(source_type)
    except KeyError:
        return "Unknown source type", 404

    body = """
<div class="card"><h2>Add {{type_name}}</h2><p class="muted">{{description}}</p>
<form method="post" action="/add/{{source_type}}">
<label>Source name <span class="help-tip" data-tip="A friendly dashboard name also used in the recording folder.">?</span></label>
<input name="name" required placeholder="{{example}}">
{{fields|safe}}
<div class="inline"><button type="submit">Save source</button><a class="button" href="/add">Back</a></div>
</form></div>
<div class="card"><h3>What happens next?</h3><p>After saving, click <strong>Test</strong>. When it succeeds, click <strong>Start</strong>.</p></div>
"""
    script = """
<script>
async function pickFile(){const r=await fetch('/api/pick-file',{method:'POST'});const d=await r.json();if(d.path)document.getElementById('path').value=d.path;else if(d.error)alert(d.error);}
async function pickFolder(){const r=await fetch('/api/pick-folder',{method:'POST'});const d=await r.json();if(d.path)document.getElementById('path').value=d.path;else if(d.error)alert(d.error);}
async function detectDevices(){const r=await fetch('/api/devices');const d=await r.json();const v=document.getElementById('video_device');const a=document.getElementById('audio_device');
if(v){v.innerHTML='<option value="">Choose video device</option>';d.video.forEach(n=>v.add(new Option(n,n)));}
if(a){a.innerHTML='<option value="">No audio / choose audio</option>';d.audio.forEach(n=>a.add(new Option(n,n)));}
if(d.error&&d.error.length)alert(d.error.join('\\n'));}
function targetChanged(){const e=document.getElementById('window_fields');if(e)e.style.display=document.getElementById('target').value==='window'?'block':'none';}
</script>"""
    return render_template_string(
        page("Add source", body, script),
        source_type=source_type,
        type_name=type_name,
        description=description,
        example=example,
        fields=fields,
    )


@app.post("/add/<source_type>")
def save_source(source_type: str):
    try:
        type_name, _, _, _ = form_data(source_type)
    except KeyError:
        return "Unknown source type", 404

    name = request.form.get("name", "").strip()
    if not name:
        flash("A source name is required.", "bad")
        return redirect(url_for("add_form", source_type=source_type))

    if source_type == "media_file":
        options = {"path": request.form.get("path", "").strip(), "realtime": bool_field("realtime"), "loop": bool_field("loop")}
        summary = options["path"]
    elif source_type == "screen":
        options = {
            "target": request.form.get("target", "desktop"),
            "window_title": request.form.get("window_title", "").strip(),
            "fps": media.safe_int(request.form.get("fps"), 30, 1, 120),
            "offset_x": media.safe_int(request.form.get("offset_x"), 0, -16384, 16384),
            "offset_y": media.safe_int(request.form.get("offset_y"), 0, -16384, 16384),
            "width": media.safe_int(request.form.get("width"), 0, 0, 16384),
            "height": media.safe_int(request.form.get("height"), 0, 0, 16384),
            "audio_device": request.form.get("audio_device", "").strip(),
        }
        summary = options["window_title"] if options["target"] == "window" else "Entire Windows desktop"
    elif source_type == "camera":
        options = {
            "video_device": request.form.get("video_device", "").strip(),
            "audio_device": request.form.get("audio_device", "").strip(),
            "resolution": request.form.get("resolution", "").strip(),
            "fps": media.safe_int(request.form.get("fps"), 30, 1, 120),
        }
        summary = options["video_device"]
    elif source_type == "audio_device":
        options = {"audio_device": request.form.get("audio_device", "").strip()}
        summary = options["audio_device"]
    elif source_type == "rtsp":
        options = {"url": request.form.get("url", "").strip(), "transport": request.form.get("transport", "tcp")}
        summary = options["url"]
    elif source_type == "network":
        options = {"url": request.form.get("url", "").strip()}
        summary = options["url"]
    elif source_type == "folder_watch":
        options = {"path": request.form.get("path", "").strip()}
        summary = options["path"]
    else:
        return "Unsupported source type", 400

    sources = media.load_sources()
    sources.append({
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "type": source_type,
        "type_label": type_name,
        "summary": summary,
        "options": options,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    media.save_sources(sources)
    flash(f'Source "{name}" was added. Click Test before starting it.', "good")
    return redirect("/")


@app.post("/sources/<source_id>/test")
def test_route(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    ok, message = media.test_source(source)
    flash(("TEST PASSED\n" if ok else "TEST FAILED\n") + message, "good" if ok else "bad")
    return redirect("/")


@app.post("/sources/<source_id>/preview")
def preview_route(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    ok, result = media.create_snapshot(source)
    if not ok:
        flash("PREVIEW FAILED\n" + str(result), "bad")
        return redirect("/")
    return redirect(url_for("preview_page", source_id=source_id))


@app.get("/preview/<source_id>")
def preview_page(source_id: str):
    source = source_by_id(source_id)
    path = LOGS_DIR / f"preview_{source_id}.jpg"
    if not source or not path.exists():
        return "Preview not found", 404
    body = """
<div class="card"><h2>Preview: {{source.name}}</h2><p class="muted">Single test frame, not live video.</p>
<img src="/preview-image/{{source.id}}?t={{stamp}}" style="max-width:100%;border-radius:9px;border:1px solid #444">
<p><a class="button" href="/">Back</a></p></div>"""
    return render_template_string(page("Preview", body), source=source, stamp=time.time())


@app.get("/preview-image/<source_id>")
def preview_image(source_id: str):
    path = LOGS_DIR / f"preview_{source_id}.jpg"
    return send_file(path, mimetype="image/jpeg", max_age=0) if path.exists() else ("Not found", 404)


@app.post("/sources/<source_id>/start")
def start_route(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    ok, message = media.start_folder_watch(source) if source["type"] == "folder_watch" else media.start_ffmpeg_source(source)
    flash(message, "good" if ok else "bad")
    return redirect("/")


@app.post("/sources/<source_id>/stop")
def stop_route(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    media.stop_source_job(source)
    flash(f'Stopped "{source["name"]}".', "good")
    return redirect("/")


@app.post("/sources/<source_id>/delete")
def delete_route(source_id: str):
    source = source_by_id(source_id)
    if not source:
        return "Source not found", 404
    media.stop_source_job(source)
    media.save_sources([item for item in media.load_sources() if item["id"] != source_id])
    flash(f'Deleted "{source["name"]}". Existing recordings were kept.', "good")
    return redirect("/")


@app.post("/api/pick-file")
def pick_file():
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="Choose media to ingest",
            filetypes=[
                ("Media files", "*.mp4 *.mkv *.avi *.mov *.wmv *.webm *.mp3 *.wav *.flac *.m4a *.aac *.ogg"),
                ("All files", "*.*"),
            ],
        )
        window.destroy()
        return jsonify({"path": path})
    except Exception as exc:
        return jsonify({"path": "", "error": str(exc)}), 500


@app.post("/api/pick-folder")
def pick_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        path = filedialog.askdirectory(title="Choose a folder for VIC to watch")
        window.destroy()
        return jsonify({"path": path})
    except Exception as exc:
        return jsonify({"path": "", "error": str(exc)}), 500


@app.get("/api/devices")
def devices():
    return jsonify(media.detect_dshow_devices())


@app.get("/recordings")
def recordings_page():
    root = media.recordings_root()
    files = [path for path in root.rglob("*") if path.is_file()]
    display = [{
        "relative": str(path.relative_to(root)),
        "url": str(path.relative_to(root)).replace(os.sep, "/"),
        "size": f"{path.stat().st_size / (1024 * 1024):.2f} MB",
    } for path in files]

    body = """
<div class="card"><h2>Recordings and ingested files</h2><p><code>{{root}}</code></p>
<table><tr><th>File</th><th>Size</th><th>Open</th></tr>
{% for item in files %}<tr><td>{{item.relative}}</td><td>{{item.size}}</td>
<td><a class="button" href="/recording-file/{{item.url}}">Open / download</a></td></tr>
{% else %}<tr><td colspan="3" class="muted">No files yet.</td></tr>{% endfor %}
</table></div>"""
    return render_template_string(page("Recordings", body), root=str(root), files=display)


@app.get("/recording-file/<path:filename>")
def recording_file(filename: str):
    return send_from_directory(media.recordings_root(), filename, as_attachment=False)


@app.get("/workers")
def workers_page():
    body = """
<div class="card"><h2>Worker PCs</h2>
<p class="muted">Workers register in v0.2. Remote recording execution comes next.</p>
<table><tr><th>Name</th><th>Host</th><th>Platform</th><th>Status</th><th>Last seen</th></tr>
{% for worker in workers %}<tr><td>{{worker.name}}</td><td>{{worker.host}}</td><td>{{worker.platform}}</td>
<td class="{{'good' if worker.online else 'bad'}}">{{'ONLINE' if worker.online else 'OFFLINE'}}</td><td>{{worker.last_seen}}</td></tr>
{% else %}<tr><td colspan="5" class="muted">No workers connected.</td></tr>{% endfor %}</table></div>"""
    return render_template_string(page("Workers", body), workers=worker_items())


@app.post("/api/workers/register")
def register_worker():
    data = request.get_json(force=True)
    worker_id = data.get("id") or uuid.uuid4().hex
    with workers_lock:
        workers[worker_id] = {
            "id": worker_id,
            "name": data.get("name", worker_id),
            "host": data.get("host", request.remote_addr),
            "platform": data.get("platform", "Unknown"),
            "last_seen_ts": time.time(),
            "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    return jsonify({"ok": True, "worker_id": worker_id})


@app.get("/api/health")
def health():
    return jsonify({
        "ok": True,
        "version": "0.2",
        "hostname": socket.gethostname(),
        "ffmpeg": media.find_ffmpeg(),
        "recordings": str(media.recordings_root()),
    })


@app.get("/help")
def help_page():
    body = """
<div class="card"><h2>Help and tutorials</h2><div class="grid">
<a class="source-choice" href="/help-file/START_HERE.html"><strong>Start here</strong><span class="muted">Install VIC and test a local video.</span></a>
<a class="source-choice" href="/help-file/ADD_SOURCES.html"><strong>Add media sources</strong><span class="muted">What every source type does.</span></a>
<a class="source-choice" href="/help-file/FFMPEG_HELP.html"><strong>FFmpeg help</strong><span class="muted">Install and check VIC's media engine.</span></a>
<a class="source-choice" href="/help-file/TROUBLESHOOTING.html"><strong>Troubleshooting</strong><span class="muted">Common errors and fixes.</span></a>
</div></div>"""
    return render_template_string(page("Help", body))


@app.get("/help-file/<path:filename>")
def help_file(filename: str):
    return send_from_directory(HELP_DIR, filename)


if __name__ == "__main__":
    active = media.settings()
    port = media.safe_int(active.get("port"), 8765, 1, 65535)
    address = f"http://127.0.0.1:{port}"

    print("=" * 58)
    print("VIC — Video Ingest Cluster v0.2")
    print("=" * 58)
    print("Dashboard:", address)
    print("FFmpeg:", media.find_ffmpeg() or "NOT FOUND")
    print("Recordings:", media.recordings_root())
    print("Keep this window open while VIC is running.")

    if active.get("open_browser_on_start", True):
        threading.Timer(1.2, lambda: webbrowser.open(address)).start()

    app.run(host="0.0.0.0", port=port, threaded=True)
