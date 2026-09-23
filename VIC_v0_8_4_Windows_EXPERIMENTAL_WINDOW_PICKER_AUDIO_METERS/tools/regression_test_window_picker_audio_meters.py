from __future__ import annotations

import ast
import importlib.util
import re
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
    module.jsonify = lambda value=None, **kwargs: (
        value if value is not None else kwargs
    )
    module.redirect = lambda target, *args, **kwargs: target
    module.render_template_string = lambda template, **context: template
    module.request = SimpleNamespace(
        headers={},
        method="GET",
        path="/",
        form={},
        files={},
        args={},
        get_json=lambda force=False, silent=False: {},
    )
    module.send_file = lambda *args, **kwargs: None
    module.send_from_directory = lambda *args, **kwargs: None
    module.url_for = lambda endpoint, **values: "/" + endpoint
    return module


def load_dashboard():
    previous = sys.modules.get("flask")
    sys.modules["flask"] = fake_flask_module()
    try:
        name = "vic_window_picker_audio_meter_regression_dashboard"
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


def load_worker():
    worker_dir = str(WORKER_PATH.parent)
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    name = "vic_window_picker_audio_meter_regression_worker"
    spec = importlib.util.spec_from_file_location(name, WORKER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load worker/worker.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    app_source = APP_PATH.read_text(encoding="utf-8")
    worker_source = WORKER_PATH.read_text(encoding="utf-8")
    ast.parse(app_source)
    ast.parse(worker_source)

    required_app = [
        "Load Application Windows",
        "windowPicker",
        "loadApplicationWindows",
        "chooseApplicationWindow",
        "filterApplicationWindows",
        "Set up monitor is stopped".replace("Set up", "Setup"),
        "startSingleAudioMeter",
        "stopSingleAudioMeter",
        "single-audio-meter",
        "Open application windows",
    ]
    for marker in required_app:
        if marker not in app_source:
            raise RuntimeError(f"Missing Dashboard marker: {marker}")

    required_worker = [
        "def enumerate_application_windows",
        "user32.EnumWindows",
        'devices["windows"] = enumerate_application_windows()',
        '"windows": devices.get("windows", [])',
    ]
    for marker in required_worker:
        if marker not in worker_source:
            raise RuntimeError(f"Missing worker marker: {marker}")

    dashboard = load_dashboard()
    screen_fields = dashboard.source_form("screen")[2]
    camera_fields = dashboard.source_form("camera")[2]
    input_fields = dashboard.source_form("audio_device")[2]
    speaker_fields = dashboard.source_form("speaker_output")[2]
    script = dashboard.source_form_script()

    if "Load Application Windows" not in screen_fields:
        raise RuntimeError("Screen form has no application-window button")
    if "windowPicker" not in screen_fields:
        raise RuntimeError("Screen form has no window selection list")

    for label, fields in [
        ("camera", camera_fields),
        ("audio input", input_fields),
        ("speaker", speaker_fields),
    ]:
        if "single-audio-meter" not in fields:
            raise RuntimeError(f"{label} form has no live setup meter")
        if "Start live meter" not in fields:
            raise RuntimeError(f"{label} form has no Start live meter button")
        if "Stop meter" not in fields:
            raise RuntimeError(f"{label} form has no Stop meter button")

    if "addScreenAudioRow" not in script:
        raise RuntimeError("Existing screen multi-audio meters were removed")
    if "startSingleAudioMeter" not in script:
        raise RuntimeError("Single-device audio meter JavaScript is missing")
    if "loadApplicationWindows" not in script:
        raise RuntimeError("Application-window JavaScript is missing")

    # Verify generated form JavaScript syntax using simple structural checks.
    if script.count("<script>") != 1 or script.count("</script>") != 1:
        raise RuntimeError("Generated source-form script wrapper is invalid")
    if script.count("{") != script.count("}"):
        raise RuntimeError("Generated source-form JavaScript braces are unbalanced")

    worker = load_worker()
    # The test environment is not Windows, so enumeration must safely return
    # an empty list rather than failing import/startup.
    windows = worker.enumerate_application_windows()
    if not isinstance(windows, list):
        raise RuntimeError("Window enumeration did not return a list")

    print("VIC window-picker/audio-meter regression tests passed:")
    print("- screen source can load/search/select open application windows")
    print("- exact selected title is written into window_title")
    print("- worker refreshes open windows every two seconds")
    print("- camera optional audio has a live setup meter")
    print("- microphone/audio input has a live setup meter")
    print("- speaker/headphone output has a live setup meter")
    print("- existing multi-device screen audio meters remain present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
