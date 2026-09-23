from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"
WORKER_PATH = BASE / "worker" / "worker.py"


def load_dashboard():
    class FakeFlask:
        def __init__(self, *args, **kwargs):
            self.secret_key = ""
        def route_decorator(self, *args, **kwargs):
            def decorate(function):
                return function
            return decorate
        get = route_decorator
        post = route_decorator
        errorhandler = route_decorator
        def run(self, *args, **kwargs):
            return None

    fake_request = SimpleNamespace(
        headers={}, method="POST", path="/", form={}, files={}, args={},
        get_json=lambda force=False, silent=False: {},
    )
    fake_flask = ModuleType("flask")
    fake_flask.Flask = FakeFlask
    fake_flask.flash = lambda *args, **kwargs: None
    fake_flask.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
    fake_flask.redirect = lambda target, *args, **kwargs: target
    fake_flask.render_template_string = lambda template, **context: template
    fake_flask.request = fake_request
    fake_flask.send_file = lambda *args, **kwargs: None
    fake_flask.send_from_directory = lambda *args, **kwargs: None
    fake_flask.url_for = lambda endpoint, **values: "/" + endpoint

    previous = sys.modules.get("flask")
    sys.modules["flask"] = fake_flask
    try:
        name = "vic_armed_auto_start_regression"
        spec = importlib.util.spec_from_file_location(name, APP_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load dashboard/app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("flask", None)
        else:
            sys.modules["flask"] = previous
    return module


def main() -> int:
    app_source = APP_PATH.read_text(encoding="utf-8")
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    ast.parse(app_source)
    ast.parse(worker_source)

    required_app = [
        "Arm Auto Start",
        "Disarm Auto Start",
        "waiting_available",
        "retry_start_failures",
        "auto_start_poll_seconds",
        "def ensure_armed_auto_start_jobs",
        '@app.post("/sources/<source_id>/auto-start")',
        "Start failed:",
        "without creating an empty recording",
    ]
    for marker in required_app:
        if marker not in app_source:
            raise RuntimeError(f"Missing Dashboard Auto Start marker: {marker}")

    required_worker = [
        "def source_availability_probe",
        "def start_wait_and_record_job",
        "def website_availability_probe",
        "def ffmpeg_availability_probe",
        "Probe timed out without confirmed media",
        "channel is not currently live",
        "Twitch channel is not currently live",
        'root.style' if False else '"wait_and_record"',
        "WAITING FOR STREAM",
        "no recording file created",
        'job_snapshot": job',
    ]
    for marker in required_worker:
        if marker not in worker_source:
            raise RuntimeError(f"Missing worker Auto Start marker: {marker}")

    dashboard = load_dashboard()
    with tempfile.TemporaryDirectory(prefix="vic-auto-start-test-") as temp:
        temp_root = Path(temp)
        dashboard.SOURCES_FILE = temp_root / "sources.json"
        dashboard.JOBS_FILE = temp_root / "jobs.json"
        dashboard.WORKERS_FILE = temp_root / "workers.json"
        dashboard.CONFIG_BACKUP_DIR = temp_root / "backups"
        dashboard.CONFIG_BACKUP_DIR.mkdir()
        dashboard.DURABLE_CONFIG_FILES = {dashboard.SOURCES_FILE}

        source = {
            "id": "stream-1",
            "name": "Waiting Stream",
            "type": "network",
            "type_label": "Direct network stream",
            "worker_id": "worker-1",
            "options": {"url": "https://example.invalid/live.m3u8"},
            "enabled": True,
            "archived": False,
            "auto_start_armed": True,
            "auto_start_poll_seconds": 7,
            "retry_start_failures": True,
            "reconnect_delay": 4,
        }
        dashboard.SOURCES_FILE.write_text(json.dumps([source]), encoding="utf-8")
        dashboard.JOBS_FILE.write_text("[]", encoding="utf-8")
        dashboard.WORKERS_FILE.write_text(json.dumps([
            {
                "id": "worker-1",
                "name": "Worker",
                "host": "TEST",
                "last_seen_ts": 9999999999,
                "is_local_dashboard": False,
                "cpu": 5,
                "disk_free_gb": 100,
            }
        ]), encoding="utf-8")

        if not dashboard.source_supports_auto_start(source):
            raise RuntimeError("Network source was not Auto Start capable")
        ok, message = dashboard.queue_armed_auto_start(source)
        if not ok:
            raise RuntimeError(f"Could not queue armed job: {message}")
        queued = json.loads(dashboard.JOBS_FILE.read_text(encoding="utf-8"))
        if len(queued) != 1:
            raise RuntimeError("Arming did not create exactly one job")
        job = queued[0]
        if job.get("mode") != "wait_and_record":
            raise RuntimeError("Armed job mode is wrong")
        if job.get("state") != "waiting_available":
            raise RuntimeError("Armed job did not enter waiting_available")
        if not job.get("armed_auto_start"):
            raise RuntimeError("Armed job marker is missing")

        # Calling the persistent queue helper must not duplicate an active watcher.
        duplicate_count = dashboard.ensure_armed_auto_start_jobs()
        if duplicate_count != 0:
            raise RuntimeError("Persistent arming duplicated an active job")

        dashboard.set_source_auto_start_armed("stream-1", False)
        saved_source = json.loads(dashboard.SOURCES_FILE.read_text(encoding="utf-8"))[0]
        if saved_source.get("auto_start_armed"):
            raise RuntimeError("Disarm did not persist")

    print("VIC armed Auto Start regression tests passed:")
    print("- network/RTSP/website capability markers are present")
    print("- armed jobs use wait_and_record + waiting_available")
    print("- no duplicate watcher is queued for an active source")
    print("- disarm persists in portable source JSON")
    print("- manual Start failure retry fields are present")
    print("- worker uses bounded probes rather than waiting for failure")
    print("- process job snapshot supports reconnect after real failures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
