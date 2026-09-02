VIC — VIDEO INGEST CLUSTER
WINDOWS v0.6.0 EXPERIMENTAL — BULK CONTROLS + PHYSICAL DISPLAY MODES
================================================

WHAT THIS UPDATE ADDS
---------------------
• Speaker/headphone output detection through Windows WASAPI loopback.
• Each speaker output can record into its own WAV file without Stereo Mix.
• A Capture Everything wizard with a checklist.
• One independent source for every selected monitor, microphone, speaker output
  and camera/capture device.
• Create sources only, or create and immediately start all selected sources.
• Worker details now separate microphones/inputs from speakers/outputs.
• Live audio meters work for speaker loopback recordings.

QUICK START
-----------
1. Extract this ZIP into a new folder.
2. Double-click START_VIC.bat.
3. The installer automatically adds Flask, psutil, yt-dlp, SoundCard and NumPy
   if they are missing.
4. Install FFmpeg with INSTALL_FFMPEG.bat if required.
5. Wait for Local PC (this computer) to appear online.

CAPTURE EVERYTHING
------------------
1. Open Capture Everything in the top menu.
2. Choose Local PC or another online worker.
3. Click Load all devices.
4. Review the checked monitors, microphones, speaker outputs and cameras.
5. Choose Create selected sources, or Create and start selected.

Every selected device is kept separate. For example:
• Display 1 -> its own MKV recording
• Display 2 -> its own MKV recording
• Microphone -> its own MKA recording
• Speakers/Headphones -> its own WAV loopback recording
• Webcam -> its own MKV recording

SPEAKER OUTPUTS
---------------
Speaker and headphone capture uses Windows WASAPI loopback through the Python
SoundCard library. It records audio being played through that particular output.
This is separate from DirectShow microphones and normally does not require
Stereo Mix.

A device can still be silent when nothing is playing through it. Open the Live
tab to see the audio meter for a running speaker or microphone source.

LOAD WARNING
------------
Capturing every screen, camera, microphone and speaker at once can use a lot of
CPU, USB bandwidth and disk space. The mass-capture page shows an estimate and
lets you untick anything before starting.

WORKERS
-------
The device list belongs to the selected worker. A remote worker detects and
records its own screens, speakers, microphones and cameras.

SECURITY
--------
Use VIC on a trusted LAN. Do not expose port 8765 directly to the internet.


SCREEN AUDIO DROPDOWN — v0.6.0 EXPERIMENTAL
------------------------------
The Optional desktop/loopback audio device dropdown now contains:
• Microphones and DirectShow audio inputs
• Speakers, headphones, HDMI outputs and USB outputs detected through Windows loopback

Microphone/input choices are encoded into the screen MKV.
Speaker/output choices are recorded as a companion WAV file beside the screen MKV.
The Live view volume meter follows the selected speaker output.


SHARED SPEAKER LOOPBACK — v0.6.0 EXPERIMENTAL
--------------------------------
VIC now opens each selected speaker/headphone output only once per worker.
Screen sources, standalone speaker sources, live meters and mass capture jobs
subscribe to that one stream. This prevents VIC sources from competing with
one another and fixes the common 0x8889000A error caused by duplicate VIC opens.

If 0x8889000A still appears before any VIC speaker source is running, an outside
program or driver has the playback device in exclusive mode. VIC cannot bypass
an external exclusive lock; disable exclusive mode or close that program.


BULK DASHBOARD BUTTONS — v0.6.0 EXPERIMENTAL
-------------------------------
The Dashboard now includes:
• Test All — queues a short test for every configured source
• Start All — starts every source that is not already active
• Stop All — stops every active recording or test job

PHYSICAL MONITOR RESOLUTION — v0.6.0 EXPERIMENTAL
------------------------------------
VIC is now per-monitor DPI aware and asks Windows for each display's active
physical signal mode. For example, a 4096x2160 display using 150% Windows
scaling should now appear as 4096x2160 rather than the scaled 2731x1440 size.

The worker also reports the active refresh rate where Windows provides it,
for example: Display 1 (Primary) — 4096x2160, 60 Hz at (0,0).


FASTER AUDIO METERS — v0.6.0 EXPERIMENTAL
----------------------------
Active worker status is now reported four times per second. Device lists and
recording inventories are still sent less often to avoid wasting network
bandwidth. Dashboard, Live list, Live All and individual Live meters refresh
approximately every 250 milliseconds.

VISIBLE DASHBOARD AUDIO
-----------------------
The Dashboard now has a dedicated Audio level column with a larger coloured
meter and a large decibel reading for every latest source job.

LIVE ALL
--------
Use Live All in the top navigation, or select Live > Live All grid. It shows
every latest source in one responsive grid with:
• refreshed video preview
• worker and recording state
• large audio-level meter
• status message
• link to the existing individual Live view

The individual Live pages are still available and have not been removed.


LIVE ALL CONTROL ROOM — v0.6.0 EXPERIMENTAL
------------------------------
Live All now includes global controls beside Full screen:
• Test All
• Start All
• Stop All

Every source card also has:
• Test
• Start
• Stop
• Individual view

Status is easier to see:
• Green pulsing corner dot and green card glow = recording
• Amber pulsing dot = testing, starting or stopping
• Red dot and red border = failed
• Grey dot = inactive, finished or stopped

Using the controls on Live All returns you to Live All instead of taking you
back to the Dashboard. Existing audio meters and individual Live views remain.


CLEAR / DELETE CONTROLS — v0.6.0 EXPERIMENTAL
--------------------------------
Dashboard:
• Clear old history
• Delete All Sources
• Clear history and Delete source on every source row

Live, Live All and Jobs:
• Clear deleted-source history
• Clear inactive history
• Per-source or per-job cleanup controls

Workers:
• Clear all offline worker entries
• Forget one offline worker

Deleting a source now also removes its inactive Live cards, job history and
cached preview images. Saved recording files are kept.

Recordings:
• Delete one recording file
• Delete all files from one worker
• Delete all recording files from every online worker

Recording deletion is permanent and uses strong confirmation prompts. Worker
code refuses to delete paths outside VIC's own recording folders.

YOUTUBE / WEBSITE MODES — v0.6.0 EXPERIMENTAL
--------------------------------
A website source now supports:
• Single video or livestream
• Playlist, saved as separate files
• Upcoming scheduled live event that waits until the event starts

Playlist mode stores downloaded-items.txt in the source's recording folder.
Starting the same playlist later skips items already recorded and captures new
entries. Upcoming-event mode uses a configurable retry interval and can request
the livestream from the beginning when supported.


EDIT SOURCES — v0.6.0 EXPERIMENTAL
---------------------
Every Dashboard source now has an Edit button. Live All cards also include
Edit source. You can change the source name, assigned worker and all settings
without deleting and recreating it. Stop an active source before saving edits,
then run Test again.

OPTIONAL LOGIN PER WEBSITE SOURCE — v0.6.0 EXPERIMENTAL
------------------------------------------
Website/YouTube sources now have five account modes:
• No account — signed out
• Automatic browser fallback
• Always use browser login for this source
• Automatic cookies.txt fallback
• Always use cookies.txt for this source

Automatic fallback starts signed out. VIC uses the selected account method
only when the first yt-dlp attempt reports a login, age-verification, private,
members-only or anti-bot authentication error.

VIC never asks for or stores a username or password. Browser mode reads the
existing login cookies from the browser on the selected worker PC at runtime.
Cookie-file mode stores only the path to a Netscape-format cookies.txt file.

For a remote worker, the browser login or cookie file must exist on that remote
worker PC. Account access is applied only to that configured source and is not
automatically used for other sources.


EDIT PAGE REPAIR — v0.6.0 EXPERIMENTAL
-------------------------
The Edit button now safely transfers saved Windows device IDs, paths, URLs and
names to the browser using Base64-encoded JSON. This fixes the Internal Server
Error caused by certain source settings. Opening Edit does not require the
assigned worker to be online.

Future Dashboard HTTP 500 errors are written to:

    logs\dashboard_error.log

AUTOMATIC WORKER DISCOVERY AND BONDING
--------------------------------------
On a second PC, copy the whole VIC v0.6.0 EXPERIMENTAL folder and run START_WORKER.bat.
The worker now:
1. Tries its saved Dashboard address.
2. Broadcasts a VIC discovery request across the private LAN.
3. Falls back to a fast local-subnet scan if broadcast is blocked.
4. Saves the main Dashboard address it finds.
5. Searches again automatically if the main PC's IP later changes.

No manual IP editing is normally required. SETUP_WORKER_GUI.bat is included as
a simple fallback with Auto Find Dashboard, Test Address and Save buttons.
Copying a used VIC folder to another PC also creates a new worker ID instead of
cloning the first PC's worker identity.

RELIABLE START / STOP / STATUS
------------------------------
START_VIC.bat and START_WORKER.bat now save process IDs and verify that the
Dashboard/worker stayed running. STOP_VIC.bat stops the worker first, including
its FFmpeg/yt-dlp child processes, then stops the Dashboard. It scans again and
prints STOPPED AND VERIFIED or a clear failure.

Additional files:
    STOP_WORKER.bat
    CHECK_VIC_STATUS.bat

YOUTUBE LOGIN WINDOW
--------------------
The website source form now has:

    Open YouTube login window on selected worker

This opens the real Google/YouTube sign-in page in Edge, Chrome, Firefox or the
chosen browser on the PC that will perform the recording. Sign in in that normal
browser window, return to VIC, select Automatic browser fallback or Always use
browser login, then press Test.

VIC never receives or stores the password. yt-dlp does not provide an embedded
YouTube OAuth login popup; VIC uses the browser's existing login cookies for
that one source only.


VERIFIED FILE TRANSFERS — v0.6.0 EXPERIMENTAL
--------------------------------
The Recordings tab can now copy or move a recording to any other online worker.
Remote files also have a one-click Move to Main PC button.

Transfer safety:
• the source worker checks the source file
• the source uploads it to temporary staging on the main Dashboard PC
• the destination worker downloads it into its recording folder
• VIC verifies the exact byte size and SHA-256 hash
• a Move deletes the original only after destination verification succeeds
• a Copy always keeps the original
• any failed move leaves the original untouched

Transferred files are stored on the destination under:
  worker_recordings\Transferred from <source worker>\...

The Recordings page includes a File transfers table showing the phase, status,
progress, source, destination and final destination path. Finished and failed
transfer-history entries can be cleared without deleting recordings.

Worker-to-worker transfers temporarily use space on the main Dashboard PC. The
staged copy is removed after the destination confirms successful verification.


MASS RECORDING TRANSFERS — v0.6.0 EXPERIMENTAL
---------------------------------
The Recordings page now supports:
• Select individual files with checkboxes
• Select all files on a worker
• Copy Selected
• Move Selected
• Copy All
• Move All
• Move All to Main PC for one remote worker
• Move All Remote Files to Main PC across every online remote worker

Every file remains a separate verified transfer. Each worker processes one
transfer at a time so a mass operation does not overload that PC or network
connection. Duplicate active transfers for the same source file are skipped.

Before upload, the worker checks the file twice to make sure its size and
modified time have stopped changing. A file that is still being recorded is
not moved. As before, a Move deletes the original only after exact file-size
and SHA-256 verification at the destination.


TRANSFER QUEUE REPAIR — v0.6.0 EXPERIMENTAL
------------------------------
The source and destination workers now immediately accept every transfer job
and place it into a visible worker-side queue. The transfer changes from
QUEUED UPLOAD to WORKER QUEUE even while an earlier file is still transferring.
Each worker still transfers only one file at a time.

The previous silent slot check was removed because it could leave later jobs
showing QUEUED UPLOAD without explaining that they had not been accepted.

WORKER VERSION CHECK
--------------------
Every worker now reports its VIC version. The Dashboard blocks new transfers
unless the source and destination computers are both running VIC v0.6.0 EXPERIMENTAL. This
prevents a new Dashboard from silently sending transfer jobs to an older worker
that does not understand the current transfer commands.

The Workers and Recordings pages display each worker version.

RETRY / CANCEL
--------------
Every transfer queue row now includes:
• Retry now — recreates the source upload job and discards incomplete staging
• Cancel — stops related jobs and keeps the original source recording

A queued upload older than a few seconds displays a diagnostic message saying
whether its source worker is offline, outdated, or online but not accepting the
job. Restart START_WORKER.bat on that source PC when instructed.


TRACEBACK FIXES — v0.6.0 EXPERIMENTAL
------------------------
Fixed the two exact errors reported in dashboard_error.log:

• Edit source:
  TypeError: render_template_string() got multiple values for argument 'source'
  The Edit template variable is now named source_item.

• Mass move / transfer batch:
  NameError: name 're' is not defined
  The Dashboard now imports re before parsing worker versions.

Run TEST_DASHBOARD_FIXES.bat for safe regression tests using temporary data.
It tests the Edit source route, Move All Remote Files to Main PC, and worker
version parsing without changing your real VIC data.

Remote workers already running v0.4.3 remain transfer-compatible with the
v0.6.0 EXPERIMENTAL Dashboard. Updating all PCs to v0.6.0 EXPERIMENTAL is still recommended.


PREVIEW MODE — v0.6.0 EXPERIMENTAL
---------------------
Preview is a continuous monitor that saves no recording file. It is available
on the Dashboard, Live, Live All, individual Live and Recording Health pages.
Press Start while Preview is active and VIC stops the Preview, waits for the
capture device to be released, then starts recording automatically.

Test remains the short three-second connection check. Preview continues until
Start or Stop is pressed. Website and watched-folder sources continue to use
Test because they do not provide the same direct continuous capture Preview.

RECORDING HEALTH TAB
--------------------
The new Health tab shows:
• Actual and requested FPS
• Dropped and duplicated frames
• Current bitrate
• Current file size
• Recording or Preview duration
• Estimated disk usage per hour
• Actual encoder being used
• FFmpeg processing speed

Live and Live All show a smaller health summary.

FPS AND HARDWARE ENCODING
-------------------------
New screen sources default to Auto FPS: match the detected display refresh rate
but cap it at 60 FPS. Full display refresh and custom FPS remain available.
New camera sources default to 60 FPS and can use device native/default timing.

Before v0.6.0 EXPERIMENTAL, screen and camera recording used libx264 on the CPU. v0.6.0 EXPERIMENTAL
probes the worker and automatically uses a working NVIDIA NVENC, AMD AMF or
Intel Quick Sync encoder, falling back to CPU x264. The actual encoder appears
on the Health page and the Worker details page. Each source can also request a
specific encoder in Edit.


GPU ENCODER AUTO-DETECTION REPAIR — v0.6.0 EXPERIMENTAL
------------------------------------------
Previous versions used the first ffmpeg.exe they found. If an old CPU-only
FFmpeg appeared earlier in Windows PATH, VIC chose it even when the newer
WinGet FFmpeg supported NVIDIA NVENC.

VIC now:
• finds every usable FFmpeg installation
• checks whether NVENC, AMD AMF and Intel Quick Sync are included
• performs a real 30-frame hardware encoding test
• prefers an FFmpeg whose GPU encoder actually works
• reports the exact reason when Automatic falls back to CPU
• shows all checked FFmpeg paths and detected GPUs on the Worker details page

Run TEST_GPU_ENCODER.bat on the affected PC for the complete FFmpeg error.
On an RTX 3060, NVIDIA NVENC should be included and its runtime test should pass.
If it fails with an NVIDIA/CUDA driver error, update the NVIDIA graphics driver.
If NVENC is not included in the selected FFmpeg, run INSTALL_FFMPEG.bat again.

Existing source encoder settings do not need changing. Automatic will use the
working GPU encoder after the worker is restarted with v0.6.0 EXPERIMENTAL.


GPU DETECTION FIX — v0.6.0 EXPERIMENTAL
--------------------------
v0.4.6 double-escaped the regular expressions used to read `ffmpeg -encoders`, falsely reporting NVENC, AMF and QSV as missing. v0.6.0 EXPERIMENTAL fixes the parser and always runs the real 30-frame encoder test. The runtime result is authoritative.


PER-WORKER COMPATIBLE FFMPEG — v0.6.0 EXPERIMENTAL
-------------------------------------
Worker Setup now includes these modes:

• Automatic compatible — recommended
  Scans installed FFmpeg copies and tools\ffmpeg_compatible recursively.
  It chooses the newest FFmpeg whose GPU encoder actually passes on this PC's
  current driver, then remembers that exact path for this worker.

• Pinned/manual
  Always uses the ffmpeg.exe selected in Worker Setup.

• Newest installed
  Chooses the newest FFmpeg regardless of driver compatibility. This can fall
  back to CPU when that FFmpeg requires a newer GPU driver.

This allows one worker to use a newer FFmpeg while another worker keeps an
older compatible build, without changing either computer's graphics driver.

OPEN WORKER BAT ONLY BUTTON
---------------------------
SETUP_WORKER_GUI.bat now has an Open Worker BAT Only button beside Test Address
and Save. It saves the current settings and opens START_WORKER.bat in its own
window. It does not start the main Dashboard.

Use OPEN_COMPATIBLE_FFMPEG_FOLDER.bat to open the folder where older compatible
FFmpeg builds can be placed. Keep each complete build in its own subfolder.


MULTIPLE SCREEN AUDIO DEVICES — v0.6.0 EXPERIMENTAL
--------------------------------------
The Add/Edit Screen form now has repeatable Optional desktop/loopback audio rows.
Press "+ Add another audio device" to add any number of microphones, capture-card
audio inputs, speakers, HDMI outputs or headphones.

Every row has its own live setup meter. The meter runs as a temporary Preview on
the selected worker, saves no file, stops when removed/closed, and expires after
15 minutes as a safety fallback.

Recording layout:
• Every microphone/input is a separately named audio track inside the screen MKV.
• Every speaker/HDMI/headphone loopback is a separately named companion WAV.
• Older sources with one audio device are automatically understood.

LIVE FILE-TRANSFER PROGRESS — v0.6.0 EXPERIMENTAL
------------------------------------
The Recordings page now fetches transfer status every second. Progress bars,
percentages, bytes transferred, current speed, estimated remaining time, state,
message and destination update without clicking Retry or refreshing the page.


FAST PARALLEL TRANSFERS — v0.6.0 EXPERIMENTAL
--------------------------------
Workers now transfer up to three files simultaneously by default instead of
only one. Open SETUP_WORKER_GUI.bat to choose from 1 to 6 simultaneous
transfers separately on each computer.

Recommended settings:
• 1 — slow disk, Wi-Fi, or maximum recording stability
• 2 — conservative
• 3 — recommended default for gigabit Ethernet
• 4-6 — fast SSD/NVMe storage and a strong wired network

Changes that improve speed and responsiveness:
• 4 MB file chunks instead of 1 MB
• transfer progress sent by a background coalescing thread, so progress
  reporting no longer pauses the file-copy loop
• Dashboard queue polling every 250 ms instead of every second
• upload and download speed samples reset correctly between phases
• no-cache transfer status API
• live speed, bytes and ETA update about four times per second

The current relay design still sends a worker-to-worker file through the Main
PC. This preserves central verification and simple firewall setup. Multiple
parallel transfers can consume substantial disk and network bandwidth, so 3 is
the default rather than 6.


RETRY ALL AND MORE PARALLEL SLOTS — v0.6.0 EXPERIMENTAL
------------------------------------------
The File-transfer queue now includes:

    Retry All Failed / Stuck

It retries:
• failed transfers
• source-delete failures
• queued uploads that have not advanced for 10 seconds
• uploads/downloads whose progress has not updated for 30 seconds

It deliberately skips healthy transfers that are actively progressing, so
pressing Retry All does not restart good uploads or create duplicate traffic.
Completed and deliberately cancelled transfers are ignored.

Worker Setup now allows 1-12 simultaneous transfers per worker. Fresh installs
default to 4. Config files copied from an earlier release keep their existing
value until changed in SETUP_WORKER_GUI.bat.

Suggested limits:
• 1-2: Wi-Fi, HDD, or recording stability first
• 3-4: normal gigabit Ethernet
• 5-8: SSD/NVMe with a strong wired network
• 9-12: fast NVMe and multi-gigabit networking; can heavily load the Main PC

Worker-to-worker files still relay through the Main PC. Increasing both source
and destination limits therefore increases Main PC network and disk load too.


======================================================================
V0.6.0 EXPERIMENTAL — PORTABLE SAFETY & MANAGEMENT BUNDLE
======================================================================
This release is deliberately installed as a separate folder. It includes the
complete original v0.5.1 ZIP in the rollback folder.

ROLLBACK
--------
1. Stop the experimental version.
2. Run ROLLBACK_TO_V0_5_1.bat.
3. Start VIC from the extracted v0.5.1 rollback folder.

The experimental release never modifies the embedded v0.5.1 ZIP.

ADDED IN THIS EXPERIMENTAL BUNDLE
---------------------------------
• Automatic portable config backup before START_VIC.bat / START_WORKER.bat
• Atomic JSON saves plus rotating source/settings backups
• Manual backup and restore BAT files
• Enable, disable, archive, restore, favourite and duplicate sources
• Import/export individual .vicsource.json files
• Source notes and automatic reconnect after an unexpected failure
• Automatic copy or verified move to Main PC after recording completes
• Storage tab with total/used/free space and recording-drive benchmark
• Protected recordings
• VIC recycle bin with restore and permanent-empty controls
• Delete All changed to Recycle All Unprotected
• Portable Worker Copy ZIP generator
• Sanitised Support ZIP generator
• Full v0.5.1 rollback package included

NOT YET REPLACED IN THIS BUILD
------------------------------
Direct worker-to-worker transfer and resumable chunk transfer are intentionally
not replacing the proven relay transfer system in this first experimental
bundle. They need a separate isolated test release because a bug in either
feature could affect very large files. The existing verified relay, parallel
slots, Retry All and SHA-256 checks remain unchanged.
