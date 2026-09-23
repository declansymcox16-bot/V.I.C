import json
import platform
import socket
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE / "config" / "worker.json"

def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")

def main():
    config = load_config()
    dashboard_url = config["dashboard_url"].rstrip("/")
    worker_id = uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname()).hex
    worker_name = config.get("worker_name") or socket.gethostname()
    poll_seconds = int(config.get("poll_seconds", 3))

    print("VIC Worker")
    print("Worker:", worker_name)
    print("Dashboard:", dashboard_url)

    while True:
        payload = {
            "id": worker_id,
            "name": worker_name,
            "host": socket.gethostname(),
            "platform": platform.platform()
        }
        try:
            result = post_json(
                dashboard_url + "/api/workers/register",
                payload
            )
            print(time.strftime("%H:%M:%S"), "Connected:", result)
        except Exception as exc:
            print(
                time.strftime("%H:%M:%S"),
                "Dashboard connection failed:",
                exc
            )
        time.sleep(poll_seconds)

if __name__ == "__main__":
    main()
