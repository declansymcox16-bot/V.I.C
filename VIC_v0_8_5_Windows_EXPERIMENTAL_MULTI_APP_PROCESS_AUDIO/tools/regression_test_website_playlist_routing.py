from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
APP_PATH = BASE / "dashboard" / "app.py"
WORKER_PATH = BASE / "worker" / "worker.py"


def fake_flask_module() -> ModuleType:
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

    module = ModuleType("flask")
    module.Flask = FakeFlask
    module.flash = lambda *args, **kwargs: None
    module.jsonify = lambda value=None, **kwargs: value if value is not None else kwargs
    module.redirect = lambda target, *args, **kwargs: target
    module.render_template_string = lambda template, **context: template
    module.request = SimpleNamespace(
        headers={}, method="GET", path="/", form={}, files={}, args={},
        get_json=lambda force=False: {},
    )
    module.send_file = lambda *args, **kwargs: None
    module.send_from_directory = lambda *args, **kwargs: None
    module.url_for = lambda endpoint, **values: "/" + endpoint
    return module


def load_dashboard():
    previous = sys.modules.get("flask")
    sys.modules["flask"] = fake_flask_module()
    try:
        name = "vic083_dashboard_regression"
        spec = importlib.util.spec_from_file_location(name, APP_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load dashboard app")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("flask", None)
        else:
            sys.modules["flask"] = previous


def load_worker():
    worker_dir = str(WORKER_PATH.parent)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    name = "vic083_worker_regression"
    spec = importlib.util.spec_from_file_location(name, WORKER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load worker")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    app_source = APP_PATH.read_text(encoding="utf-8")
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    ast.parse(app_source)
    ast.parse(worker_source)

    dashboard = load_dashboard()
    worker = load_worker()

    twitch = {
        "id": "twitch-import",
        "name": "Twitch (Imported)",
        "type": "network",
        "type_label": "Direct network stream",
        "options": {"url": "https://www.twitch.tv/coconutb__247"},
    }
    repaired = dashboard.source_with_defaults(twitch)
    if repaired["type"] != "website":
        raise RuntimeError("Dashboard did not repair imported Twitch network source")
    if repaired["options"]["website_mode"] != "single":
        raise RuntimeError("Twitch source did not resolve to single website mode")

    playlist_url = "https://www.youtube.com/watch?v=abc123&list=PL_TEST_ALL"
    playlist = dashboard.source_with_defaults({
        "id": "playlist-import",
        "name": "Playlist (Imported)",
        "type": "network",
        "type_label": "Website video or livestream",
        "options": {"url": playlist_url, "website_mode": "single"},
    })
    if playlist["type"] != "website":
        raise RuntimeError("YouTube playlist import was not routed to website")
    if playlist["options"]["website_mode"] != "playlist":
        raise RuntimeError("Old single-mode playlist was not upgraded to all items")

    explicit_single = dashboard.source_with_defaults({
        "id": "explicit-single",
        "name": "Explicit single",
        "type": "website",
        "type_label": "Website video or livestream",
        "options": {
            "url": playlist_url,
            "website_mode": "single",
            "website_mode_version": 2,
        },
    })
    if explicit_single["options"]["website_mode"] != "single":
        raise RuntimeError("New explicit Single item choice was not respected")

    direct_hls = dashboard.source_with_defaults({
        "id": "hls",
        "name": "HLS",
        "type": "network",
        "type_label": "Direct network stream",
        "options": {"url": "https://cdn.example.test/live/master.m3u8"},
    })
    if direct_hls["type"] != "network":
        raise RuntimeError("Direct m3u8 URL was incorrectly routed to yt-dlp")

    worker_twitch = worker.normalise_runtime_source(twitch)
    if worker_twitch["type"] != "website":
        raise RuntimeError("Worker runtime did not repair Twitch routing")
    worker_playlist = worker.normalise_runtime_source({
        "type": "website",
        "options": {"url": playlist_url, "website_mode": "auto", "website_mode_version": 2},
    })
    if worker_playlist["options"]["website_mode"] != "playlist":
        raise RuntimeError("Worker Auto Detect did not choose entire playlist")

    required_worker = [
        'job["source"] = normalise_runtime_source',
        '"--yes-playlist"',
        '"--ignore-errors"',
        '"--download-archive"',
        '"%(playlist_index)05d_',
    ]
    for marker in required_worker:
        if marker not in worker_source:
            raise RuntimeError(f"Missing worker playlist/routing marker: {marker}")

    required_app = [
        "Auto Detect — playlist URLs record every item",
        "Single item only — ignore any playlist",
        "Entire playlist — save every available item",
        "websiteUrlLooksLikePlaylist",
        "YT-DLP ROUTING REPAIRED",
    ]
    for marker in required_app:
        if marker not in app_source:
            raise RuntimeError(f"Missing Dashboard playlist marker: {marker}")

    print("VIC website/playlist routing regression tests passed:")
    print("- imported Twitch page URLs are routed through yt-dlp")
    print("- direct .m3u8 URLs remain direct FFmpeg streams")
    print("- old YouTube playlist sources upgrade from first item to all items")
    print("- Auto Detect chooses all playlist entries")
    print("- explicit new Single item choice remains available")
    print("- worker dispatch repairs existing saved sources at runtime")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
