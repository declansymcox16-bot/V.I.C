# VIC — Video Ingest Cluster

**Process Anywhere. Capture Anywhere. Record Everything.**

VIC is a portable Windows capture and recording system designed to control multiple PCs from one central dashboard. It can discover, preview, record and manage applications, windows, monitors, microphones, application audio, speakers/headphones, cameras, capture cards, RTSP/IP cameras, network streams and supported livestreams across a LAN.

Unlike a traditional recorder that mixes everything into one scene, VIC is built around **independent sources and independent recordings**. A game, Discord, several monitors, cameras and audio sources can all be recorded separately while still being controlled from one place.

Created by and credited to **Declan Allan Dykes**.

## Downloads and versions

| Version | What it is | Source |
| --- | --- | --- |
| **v0.9.0** | Latest experimental version — distributed live capture and processing between workers | [Open version](VIC_v0_9_0_Windows_EXPERIMENTAL_DISTRIBUTED_LIVE_PROCESSING/) |
| v0.8.7 | Embedded audio modes — audio in video, separate files, or both | [Open version](VIC_v0_8_7_Windows_EXPERIMENTAL_EMBEDDED_AUDIO_MODES/) |
| v0.8.6 | Windows 10 process-audio compatibility mode | [Open version](VIC_v0_8_6_Windows_EXPERIMENTAL_WINDOWS10_PROCESS_AUDIO/) |
| v0.8.5 | Multiple application capture with per-process application audio | [Open version](VIC_v0_8_5_Windows_EXPERIMENTAL_MULTI_APP_PROCESS_AUDIO/) |
| v0.8.4 | Application-window picker and live audio setup meters | [Open version](VIC_v0_8_4_Windows_EXPERIMENTAL_WINDOW_PICKER_AUDIO_METERS/) |
| v0.8.3 | Twitch/YouTube website and playlist routing fixes | [Open version](VIC_v0_8_3_Windows_EXPERIMENTAL_WEBSITE_PLAYLIST_ROUTING_FIX/) |
| v0.8.2 | Source Library worker assignment after import | [Open version](VIC_v0_8_2_Windows_EXPERIMENTAL_SOURCE_WORKER_ASSIGNMENT/) |
| v0.8.1 | Second-PC worker setup repair | [Open version](VIC_v0_8_1_Windows_EXPERIMENTAL_WORKER_SETUP_REPAIR/) |
| v0.8.0 | Capture finishing and advanced storage manager | [Open version](VIC_v0_8_0_Windows_EXPERIMENTAL_CAPTURE_STORAGE_MANAGER/) |
| v0.7.1 | Live recording file-size and disk-write-rate monitoring | [Open version](VIC_v0_7_1_Windows_EXPERIMENTAL_LIVE_DISK_RATE/) |

The repository keeps the historical VIC source versions as separate folders so older builds remain available for comparison and rollback.

## Quick start

### Main PC

1. Open the folder for the VIC version you want to run.
2. Run `INSTALL_VIC.bat` once.
3. Run `START_VIC.bat`.
4. Open the VIC Dashboard in your browser.
5. Add or import sources and assign their capture/processing workers.

### Additional worker PCs

1. Copy the **same VIC version** to the second, third or other worker PC.
2. Use `SETUP_WORKER_GUI.bat` or the version-specific worker setup/repair tool.
3. Point the worker at the Main VIC Dashboard.
4. Run `START_WORKER.bat`.
5. Confirm the worker shows **ONLINE** in the Dashboard.

For features that depend on worker-side capture, audio, storage or processing, keep the Main Dashboard and workers on the same VIC version.

## What VIC can capture

- **Application and window capture** — select open applications instead of manually typing exact window titles.
- **Multiple applications at once** — select several application windows and create separate sources automatically.
- **Per-application/process audio** — capture application audio without using the whole speaker/headphone mix where Windows supports it.
- **Desktop and monitor capture** — record individual screens independently.
- **Microphones and audio inputs** — physical and virtual audio devices with live setup meters.
- **Speaker/headphone/HDMI loopback** — optional Windows output-device capture.
- **Cameras and capture cards** — webcams, USB capture devices and compatible video hardware.
- **RTSP/IP cameras** — network cameras alongside local PC capture.
- **Website video and livestreams** — supported Twitch, YouTube and other yt-dlp-compatible pages.
- **YouTube playlists** — single-item or full-playlist recording modes.
- **Direct network streams** — including compatible HLS/DASH and RTSP sources.

## Independent recordings and audio

Every source can be controlled independently.

VIC can keep audio in several ways:

- **Embed audio in the video only**
- **Keep video and separate audio files only**
- **Embed audio in the video and also keep separate files**

The third option gives you a normal playable video **and** separate audio files for editing or backup.

If a merge fails, VIC keeps the original files rather than deleting the only copy.

## Distributed workers

A source can have separate capture and processing workers.

For example:

```text
Capture worker:     PC 3
Processing worker:  PC 1
```

The **capture worker** is the PC that physically has access to the application, window, monitor, camera or audio device.

The **processing worker** can be:

- The same PC as the capture worker
- Another specific VIC worker
- Automatically selected from available workers

Distributed live processing is designed to move the heavier recording/processing work away from the machine where the source exists when appropriate.

Actual capacity depends on CPU, GPU, storage and network bandwidth. VIC does not make hardware capacity unlimited.

## Live control and monitoring

VIC includes central controls for:

- Start / stop recording
- Graceful website **Finish Capture**
- Arm and wait for offline livestreams
- Live previews and Live All
- Per-source preview quality
- Current recording file size
- Actual recording-file write rate
- FPS, bitrate, encoder and dropped-frame health
- Worker status and load
- Moving live processing between capable workers
- Bulk source enable/disable and worker assignment

## Storage management

The Storage Manager can keep track of recording locations on different workers and network paths.

Depending on the VIC version, storage features include:

- Named storage locations
- Worker-owned drives
- Capacity and free-space information
- Read/write speed tests
- Per-source recording destinations
- Verified recording moves
- Safe recording rollover to another storage location

VIC avoids moving a file while that file is still open for recording.

## Portable design

VIC is designed around folders, JSON configuration and BAT launchers rather than requiring a database server or permanently installed service.

That makes it easier to:

- Move a VIC installation
- Keep rollback versions
- Copy a worker installation to another PC
- Back up configuration
- Inspect the actual files being used

## Privacy and safety

- VIC is intended for devices, accounts, streams and systems you are authorized to capture.
- Do not publish real VIC cluster tokens, passwords, stream credentials or private network secrets.
- Public repository configuration files use placeholders rather than live credentials.
- Per-application audio is process-based and does not intentionally capture unrelated applications, but some multi-process applications such as browsers can share process trees.
- Distributed live processing sends capture data across your network, so use it only on networks you trust.
- Historical source folders in this repository intentionally exclude personal recordings, logs, caches and private runtime data.

## Repository layout

Each VIC version is kept in its own folder:

```text
VIC_v0_9_0_Windows_EXPERIMENTAL_DISTRIBUTED_LIVE_PROCESSING/
VIC_v0_8_7_Windows_EXPERIMENTAL_EMBEDDED_AUDIO_MODES/
VIC_v0_8_6_Windows_EXPERIMENTAL_WINDOWS10_PROCESS_AUDIO/
...
```

Typical version folders contain:

```text
dashboard/
worker/
tools/
config/
help/
START_VIC.bat
START_WORKER.bat
STOP_VIC.bat
INSTALL_VIC.bat
README_FIRST.txt
requirements.txt
```

Generated recordings, logs, caches and nested rollback ZIP archives are intentionally not kept in the Git source archive.

## License

Licensed under the [MIT License](LICENSE).

Copyright (c) 2026 Declan Allan Dykes.
