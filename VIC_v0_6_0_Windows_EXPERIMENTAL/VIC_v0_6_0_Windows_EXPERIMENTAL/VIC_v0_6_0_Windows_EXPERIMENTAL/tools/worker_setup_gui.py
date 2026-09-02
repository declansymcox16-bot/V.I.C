from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from common.discovery import discover_dashboards, probe_dashboard

CONFIG_FILE = BASE / "config" / "worker.json"
WORKER_BAT = BASE / "START_WORKER.bat"
GPU_TEST_BAT = BASE / "TEST_GPU_ENCODER.bat"
COMPATIBLE_FOLDER = BASE / "tools" / "ffmpeg_compatible"

MODE_LABELS = {
    "auto_compatible": (
        "Automatic compatible — newest FFmpeg that works with this PC's current driver"
    ),
    "pinned": "Pinned/manual — always use the FFmpeg path below",
    "newest": "Newest installed — may require a newer graphics driver",
}
LABEL_TO_MODE = {label: key for key, label in MODE_LABELS.items()}


class WorkerSetup(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("VIC Worker Setup")
        self.geometry("900x700")
        self.minsize(790, 620)
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
                "FFmpeg compatibility is stored separately on every worker PC."
            ),
            wraplength=800,
        ).pack(anchor="w", pady=(4, 16))

        form = ttk.Frame(outer)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Worker name").grid(row=0, column=0, sticky="w", pady=6)
        self.name_var = tk.StringVar(value=str(self.cfg.get("worker_name", "")))
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=6)

        ttk.Label(form, text="Dashboard address").grid(row=1, column=0, sticky="w", pady=6)
        self.url_var = tk.StringVar(value=str(self.cfg.get("dashboard_url", "http://127.0.0.1:8765")))
        ttk.Entry(form, textvariable=self.url_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=6)

        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_discover", True)))
        ttk.Checkbutton(
            form,
            text="Automatically find and re-bond to the Dashboard on this network",
            variable=self.auto_var,
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=8)

        ttk.Separator(form).grid(row=3, column=0, columnspan=3, sticky="ew", pady=12)
        ttk.Label(form, text="FFmpeg mode").grid(row=4, column=0, sticky="w", pady=6)
        current_mode = str(self.cfg.get("ffmpeg_selection_mode", "auto_compatible"))
        self.mode_var = tk.StringVar(value=MODE_LABELS.get(current_mode, MODE_LABELS["auto_compatible"]))
        self.mode_combo = ttk.Combobox(
            form,
            textvariable=self.mode_var,
            state="readonly",
            values=list(MODE_LABELS.values()),
        )
        self.mode_combo.grid(row=4, column=1, columnspan=2, sticky="ew", pady=6)
        self.mode_combo.bind("<<ComboboxSelected>>", lambda _event: self.mode_changed())

        ttk.Label(form, text="Manual/pinned ffmpeg.exe").grid(row=5, column=0, sticky="w", pady=6)
        self.ffmpeg_var = tk.StringVar(value=str(self.cfg.get("ffmpeg_path", "")))
        self.ffmpeg_entry = ttk.Entry(form, textvariable=self.ffmpeg_var)
        self.ffmpeg_entry.grid(row=5, column=1, sticky="ew", pady=6)
        ttk.Button(form, text="Browse…", command=self.browse_ffmpeg).grid(row=5, column=2, sticky="ew", padx=(8, 0), pady=6)

        remembered = str(self.cfg.get("ffmpeg_last_selected_path", "")).strip()
        self.remembered_var = tk.StringVar(
            value=("Last compatible selection: " + remembered) if remembered else "Last compatible selection: not scanned yet"
        )
        ttk.Label(form, textvariable=self.remembered_var, wraplength=780).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(3, 8)
        )

        ttk.Label(form, text="Simultaneous file transfers").grid(
            row=7,
            column=0,
            sticky="w",
            pady=6,
        )
        self.transfer_limit_var = tk.StringVar(
            value=str(self.cfg.get("transfer_parallel_limit", 4))
        )
        self.transfer_limit_combo = ttk.Combobox(
            form,
            textvariable=self.transfer_limit_var,
            state="readonly",
            values=["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"],
            width=8,
        )
        self.transfer_limit_combo.grid(
            row=7,
            column=1,
            sticky="w",
            pady=6,
        )
        ttk.Label(
            form,
            text=(
                "4 is recommended. Use 6-8 for fast SSD/NVMe and wired LAN. "
                "Values 9-12 can heavily load the Main PC, disks and network."
            ),
            wraplength=500,
        ).grid(row=7, column=2, sticky="w", padx=(8, 0), pady=6)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(14, 8))
        ttk.Button(buttons, text="Auto Find Dashboard", command=self.auto_find).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Test Address", command=self.test_address).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left", padx=(0, 8))
        ttk.Button(
            buttons,
            text="Open Worker BAT Only",
            command=self.open_worker_bat_only,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")

        ffmpeg_buttons = ttk.Frame(outer)
        ffmpeg_buttons.pack(fill="x", pady=(0, 10))
        ttk.Button(
            ffmpeg_buttons,
            text="Open Compatible FFmpeg Folder",
            command=self.open_compatible_folder,
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            ffmpeg_buttons,
            text="Run GPU/FFmpeg Test",
            command=self.run_gpu_test,
        ).pack(side="left", padx=(0, 8))

        self.status = tk.Text(outer, height=11, wrap="word", state="disabled")
        self.status.pack(fill="both", expand=True)
        self.write_status(
            f"This computer: {socket.gethostname()}\n"
            "Automatic compatible is recommended. It tests every installed or VIC-compatible "
            "FFmpeg and remembers the newest one that works with this computer's existing driver."
        )
        self.mode_changed()

    def write_status(self, text: str) -> None:
        self.status.configure(state="normal")
        self.status.delete("1.0", "end")
        self.status.insert("end", text)
        self.status.configure(state="disabled")

    def mode_changed(self) -> None:
        mode = LABEL_TO_MODE.get(self.mode_var.get(), "auto_compatible")
        self.ffmpeg_entry.configure(state="normal" if mode == "pinned" else "disabled")

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
                "No Dashboard was found. Check that START_VIC.bat is running on the main PC "
                "and allow Python on Private networks in Windows Firewall."
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
                f"Connection successful.\nDashboard: {details.get('hostname')}\n"
                f"Address: {details.get('url')}\nVersion: {details.get('version')}"
            )
        else:
            self.write_status("Could not reach a VIC Dashboard at that address.")

    def browse_ffmpeg(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose ffmpeg.exe",
            filetypes=[("FFmpeg executable", "ffmpeg.exe"), ("Executables", "*.exe"), ("All files", "*.*")],
        )
        if selected:
            self.ffmpeg_var.set(selected)
            self.mode_var.set(MODE_LABELS["pinned"])
            self.mode_changed()

    def save_settings(self, show_message: bool = True) -> bool:
        url = self.url_var.get().strip().rstrip("/")
        if url and "://" not in url:
            url = "http://" + url
        mode = LABEL_TO_MODE.get(self.mode_var.get(), "auto_compatible")
        manual = self.ffmpeg_var.get().strip()
        if mode == "pinned" and not manual:
            messagebox.showerror("VIC Worker Setup", "Choose an ffmpeg.exe before using Pinned/manual mode.")
            return False
        if mode == "pinned" and not Path(manual).is_file():
            messagebox.showerror("VIC Worker Setup", "The selected ffmpeg.exe does not exist.")
            return False

        self.cfg["dashboard_url"] = url or "http://127.0.0.1:8765"
        self.cfg["worker_name"] = self.name_var.get().strip()
        self.cfg["auto_discover"] = bool(self.auto_var.get())
        self.cfg["ffmpeg_selection_mode"] = mode
        self.cfg["ffmpeg_path"] = manual
        try:
            transfer_limit = max(
                1,
                min(12, int(self.transfer_limit_var.get())),
            )
        except (TypeError, ValueError):
            transfer_limit = 4
        self.cfg["transfer_parallel_limit"] = transfer_limit
        CONFIG_FILE.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        if show_message:
            messagebox.showinfo("VIC Worker Setup", "Settings saved.")
        self.write_status(
            "Saved successfully.\n"
            f"FFmpeg mode: {MODE_LABELS[mode]}\n"
            f"Simultaneous transfers: {transfer_limit}\n"
            "Restart the worker after changing the transfer limit.\n"
            "Press Open Worker BAT Only to start just this worker PC."
        )
        return True

    def save(self) -> None:
        self.save_settings(show_message=True)

    def open_worker_bat_only(self) -> None:
        if not self.save_settings(show_message=False):
            return
        if not WORKER_BAT.is_file():
            messagebox.showerror("VIC Worker Setup", f"Could not find {WORKER_BAT.name}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(WORKER_BAT))  # type: ignore[attr-defined]
            else:
                subprocess.Popen([str(WORKER_BAT)], cwd=str(BASE))
            self.write_status(
                "Settings saved and START_WORKER.bat opened in its own window.\n"
                "This starts only the worker, not the main Dashboard."
            )
        except Exception as exc:
            messagebox.showerror("VIC Worker Setup", f"Could not open START_WORKER.bat:\n{exc}")

    def open_compatible_folder(self) -> None:
        COMPATIBLE_FOLDER.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(COMPATIBLE_FOLDER))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(COMPATIBLE_FOLDER)])
            self.write_status(
                "Opened the compatible FFmpeg folder. Put complete FFmpeg builds in separate "
                "subfolders. VIC scans recursively for ffmpeg.exe."
            )
        except Exception as exc:
            messagebox.showerror("VIC Worker Setup", f"Could not open the folder:\n{exc}")

    def run_gpu_test(self) -> None:
        if not GPU_TEST_BAT.is_file():
            messagebox.showerror("VIC Worker Setup", f"Could not find {GPU_TEST_BAT.name}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(GPU_TEST_BAT))  # type: ignore[attr-defined]
            else:
                subprocess.Popen([str(GPU_TEST_BAT)], cwd=str(BASE))
            self.write_status("Opened the GPU/FFmpeg diagnostic in a separate window.")
        except Exception as exc:
            messagebox.showerror("VIC Worker Setup", f"Could not run the GPU test:\n{exc}")


if __name__ == "__main__":
    WorkerSetup().mainloop()
