VIC — VIDEO INGEST CLUSTER
WINDOWS v0.3 — REAL WORKER BUILD
================================

VIC v0.3 adds real worker assignment and remote job execution.

WHAT IS NEW
-----------
• Choose Automatic or a named worker when adding a source.
• Start/stop commands are sent to the selected worker.
• Workers report CPU, memory, free disk, screens and DirectShow devices.
• Click a worker to view its details and available devices.
• Website video/livestream sources use yt-dlp.
• Desktop/screen sources can include a selected DirectShow audio device.
• Worker errors and output file paths appear on the dashboard.
• START_VIC.bat launches both the dashboard and a local worker.

IMPORTANT AUDIO NOTE
--------------------
FFmpeg can capture Windows DirectShow audio inputs. For normal desktop sound,
Windows must expose a loopback device such as Stereo Mix or a virtual audio
cable. Select that device as the screen source audio input. Per-application
audio capture is not universal in this build.

IMPORTANT FILE NOTE
-------------------
A local file path belongs to the selected worker. If you choose another PC,
that same path must exist there, or use a shared network path such as:
\\SERVER\Media\video.mp4

QUICK START
-----------
1. Extract the ZIP.
2. Run INSTALL_VIC.bat.
3. Run INSTALL_FFMPEG.bat if FFmpeg is missing.
4. Run CHECK_SYSTEM.bat.
5. Run START_VIC.bat.
6. Wait for Local Worker to appear online.
7. Add a source and select Local Worker or Automatic.
8. Click Test, then Start.

OTHER PCS
---------
Copy this complete folder to another PC. Edit config\worker.json:
• dashboard_url = dashboard PC address, e.g. http://192.168.0.202:8765
• keep cluster_token the same
• optionally set worker_name
Then run START_WORKER.bat on that PC.

SECURITY
--------
This prototype is for a trusted home/LAN network. Do not expose port 8765
straight to the public internet.
