from __future__ import annotations

import json
import socket
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from common.discovery import discover_dashboards, probe_dashboard

CONFIG_FILE = BASE / "config" / "worker.json"


class WorkerSetup(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VIC Worker Setup")
        self.geometry("700x430")
        self.minsize(650, 390)
        self.cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="Connect this PC to the VIC main Dashboard",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Automatic discovery normally means you do not need to type an IP. "
                "The manual address remains available as a fallback."
            ),
            wraplength=640,
        ).pack(anchor="w", pady=(4, 16))

        form = ttk.Frame(outer)
        form.pack(fill="x")
        ttk.Label(form, text="Worker name").grid(row=0, column=0, sticky="w", pady=6)
        self.name_var = tk.StringVar(value=str(self.cfg.get("worker_name", "")))
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=6)

        ttk.Label(form, text="Dashboard address").grid(row=1, column=0, sticky="w", pady=6)
        self.url_var = tk.StringVar(value=str(self.cfg.get("dashboard_url", "http://127.0.0.1:8765")))
        ttk.Entry(form, textvariable=self.url_var).grid(row=1, column=1, sticky="ew", pady=6)

        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_discover", True)))
        ttk.Checkbutton(
            form,
            text="Automatically find and re-bond to the Dashboard on this network",
            variable=self.auto_var,
        ).grid(row=2, column=1, sticky="w", pady=8)
        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=14)
        ttk.Button(buttons, text="Auto Find Dashboard", command=self.auto_find).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Test Address", command=self.test_address).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

        self.status = tk.Text(outer, height=9, wrap="word", state="disabled")
        self.status.pack(fill="both", expand=True)
        self.write_status(
            f"This computer: {socket.gethostname()}\n"
            "Press Auto Find Dashboard while START_VIC.bat is running on the main PC."
        )

    def write_status(self, text: str) -> None:
        self.status.configure(state="normal")
        self.status.delete("1.0", "end")
        self.status.insert("end", text)
        self.status.configure(state="disabled")

    def auto_find(self) -> None:
        self.write_status("Searching the local network for VIC...")
        threading.Thread(target=self._auto_find_thread, daemon=True).start()

    def _auto_find_thread(self) -> None:
        found = discover_dashboards(
            dashboard_port=int(self.cfg.get("dashboard_port", 8765)),
            discovery_port=int(self.cfg.get("discovery_port", 8766)),
            include_scan=True,
        )
        self.after(0, lambda: self._show_found(found))

    def _show_found(self, found: list[dict]) -> None:
        if not found:
            self.write_status(
                "No Dashboard was found. Check that START_VIC.bat is running on the main PC and allow Python on Private networks in Windows Firewall."
            )
            return
        selected = found[0]
        self.url_var.set(str(selected["url"]))
        details = "\n".join(
            f"{item.get('hostname', 'VIC Dashboard')} — {item['url']}"
            for item in found
        )
        self.write_status("Found VIC Dashboard(s):\n" + details + "\n\nThe first address has been selected. Press Save.")

    def test_address(self) -> None:
        details = probe_dashboard(self.url_var.get(), timeout=2.0)
        if details:
            self.write_status(
                f"Connection successful.\nDashboard: {details.get('hostname')}\nAddress: {details.get('url')}\nVersion: {details.get('version')}"
            )
        else:
            self.write_status("Could not reach a VIC Dashboard at that address.")

    def save(self) -> None:
        url = self.url_var.get().strip().rstrip("/")
        if url and "://" not in url:
            url = "http://" + url
        self.cfg["dashboard_url"] = url or "http://127.0.0.1:8765"
        self.cfg["worker_name"] = self.name_var.get().strip()
        self.cfg["auto_discover"] = bool(self.auto_var.get())
        CONFIG_FILE.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        messagebox.showinfo(
            "VIC Worker Setup",
            "Settings saved. You can now run START_WORKER.bat.",
        )
        self.write_status("Saved successfully. Run START_WORKER.bat.")


if __name__ == "__main__":
    WorkerSetup().mainloop()
