VIC — VIDEO INGEST CLUSTER
WINDOWS v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO — BULK CONTROLS + PHYSICAL DISPLAY MODES
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


SCREEN AUDIO DROPDOWN — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
------------------------------
The Optional desktop/loopback audio device dropdown now contains:
• Microphones and DirectShow audio inputs
• Speakers, headphones, HDMI outputs and USB outputs detected through Windows loopback

Microphone/input choices are encoded into the screen MKV.
Speaker/output choices are recorded as a companion WAV file beside the screen MKV.
The Live view volume meter follows the selected speaker output.


SHARED SPEAKER LOOPBACK — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
--------------------------------
VIC now opens each selected speaker/headphone output only once per worker.
Screen sources, standalone speaker sources, live meters and mass capture jobs
subscribe to that one stream. This prevents VIC sources from competing with
one another and fixes the common 0x8889000A error caused by duplicate VIC opens.

If 0x8889000A still appears before any VIC speaker source is running, an outside
program or driver has the playback device in exclusive mode. VIC cannot bypass
an external exclusive lock; disable exclusive mode or close that program.


BULK DASHBOARD BUTTONS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
-------------------------------
The Dashboard now includes:
• Test All — queues a short test for every configured source
• Start All — starts every source that is not already active
• Stop All — stops every active recording or test job

PHYSICAL MONITOR RESOLUTION — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
------------------------------------
VIC is now per-monitor DPI aware and asks Windows for each display's active
physical signal mode. For example, a 4096x2160 display using 150% Windows
scaling should now appear as 4096x2160 rather than the scaled 2731x1440 size.

The worker also reports the active refresh rate where Windows provides it,
for example: Display 1 (Primary) — 4096x2160, 60 Hz at (0,0).


FASTER AUDIO METERS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


LIVE ALL CONTROL ROOM — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


CLEAR / DELETE CONTROLS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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

YOUTUBE / WEBSITE MODES — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
--------------------------------
A website source now supports:
• Single video or livestream
• Playlist, saved as separate files
• Upcoming scheduled live event that waits until the event starts

Playlist mode stores downloaded-items.txt in the source's recording folder.
Starting the same playlist later skips items already recorded and captures new
entries. Upcoming-event mode uses a configurable retry interval and can request
the livestream from the beginning when supported.


EDIT SOURCES — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
---------------------
Every Dashboard source now has an Edit button. Live All cards also include
Edit source. You can change the source name, assigned worker and all settings
without deleting and recreating it. Stop an active source before saving edits,
then run Test again.

OPTIONAL LOGIN PER WEBSITE SOURCE — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


EDIT PAGE REPAIR — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
-------------------------
The Edit button now safely transfers saved Windows device IDs, paths, URLs and
names to the browser using Base64-encoded JSON. This fixes the Internal Server
Error caused by certain source settings. Opening Edit does not require the
assigned worker to be online.

Future Dashboard HTTP 500 errors are written to:

    logs\dashboard_error.log

AUTOMATIC WORKER DISCOVERY AND BONDING
--------------------------------------
On a second PC, copy the whole VIC v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO folder and run START_WORKER.bat.
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


VERIFIED FILE TRANSFERS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


MASS RECORDING TRANSFERS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


TRANSFER QUEUE REPAIR — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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
unless the source and destination computers are both running VIC v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO. This
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


TRACEBACK FIXES — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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
v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO Dashboard. Updating all PCs to v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO is still recommended.


PREVIEW MODE — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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

Before v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO, screen and camera recording used libx264 on the CPU. v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
probes the worker and automatically uses a working NVIDIA NVENC, AMD AMF or
Intel Quick Sync encoder, falling back to CPU x264. The actual encoder appears
on the Health page and the Worker details page. Each source can also request a
specific encoder in Edit.


GPU ENCODER AUTO-DETECTION REPAIR — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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
working GPU encoder after the worker is restarted with v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO.


GPU DETECTION FIX — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
--------------------------
v0.4.6 double-escaped the regular expressions used to read `ffmpeg -encoders`, falsely reporting NVENC, AMF and QSV as missing. v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO fixes the parser and always runs the real 30-frame encoder test. The runtime result is authoritative.


PER-WORKER COMPATIBLE FFMPEG — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


MULTIPLE SCREEN AUDIO DEVICES — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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

LIVE FILE-TRANSFER PROGRESS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
------------------------------------
The Recordings page now fetches transfer status every second. Progress bars,
percentages, bytes transferred, current speed, estimated remaining time, state,
message and destination update without clicking Retry or refreshing the page.


FAST PARALLEL TRANSFERS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


RETRY ALL AND MORE PARALLEL SLOTS — v0.8.5 EXPERIMENTAL MULTI APP PROCESS AUDIO
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


WORKER CONNECTION REPAIR — v0.6.1
---------------------------------
The v0.6.0 Dashboard received worker heartbeats but returned HTTP 500 while
saving workers.json on Windows/Python 3.14:

    OSError: [Errno 9] Bad file descriptor

The temporary JSON file was reopened read-only before os.fsync(). Windows
rejected that descriptor.

v0.6.1 now:
• writes, flushes and fsyncs while the writable handle is still open
• uses atomic os.replace
• treats unsupported fsync as non-fatal
• removes incomplete temporary files
• stops making rotating backups for workers.json, jobs.json and transfers.json
  on every heartbeat/progress update
• continues backing up sources.json, dashboard.json and recording_flags.json

Run TEST_WORKER_CONNECTION_FIX.bat before START_VIC.bat. It uses temporary
files, performs 100 atomic saves and sends 10 authenticated heartbeat requests.

ROLLBACK_TO_V0_5_1.bat remains included and unchanged.


LIVE DISPLAY SETTINGS AND UNLIMITED PREVIEW — v0.6.2
----------------------------------------------------
New Settings tab:
• Audio meter refresh presets: Instant 0.00, Fast 0.20, Mid 0.50, Slow 1.00
• Custom audio refresh from 0.00 to 10.00 seconds
• Optional smooth meter movement between readings
• Preview refresh presets and custom 0.00 to 10.00 seconds
• Preview quality: Low 320, Balanced 640, High 960, Very High 1280,
  plus custom width 160-1920 and JPEG quality 1-31

Preview is unlimited. It stays active until Stop is pressed or Start Recording
changes it into a recording. Test remains a short diagnostic and is separate.

Timing settings apply when a Dashboard page is reloaded. Preview quality and
FFmpeg preview FPS apply when Preview or recording is next started.

ROLLBACK_TO_V0_6_1.bat extracts the complete working v0.6.1 package beside
this folder. The older v0.5.1 rollback remains included too.


SOURCE FORM AND EXPORT REPAIR — v0.6.3
--------------------------------------
v0.6.2 could crash when opening Add Source or Edit Source with:

    TypeError: Object of type Undefined is not JSON serializable

Cause:
The source-form JavaScript expected audio refresh settings to be supplied as
Jinja template variables. The Add/Edit routes did not provide those variables.

Fix:
• source_form_script now embeds concrete audio refresh and smoothing values
  directly from the Dashboard settings
• Add Source and Edit Source no longer depend on extra template context
• the included regression test renders both pages using StrictUndefined
• each source action now says Export Source
• Source Library now includes Export All Sources ZIP
• the ZIP contains one .vicsource.json per source plus manifest.json

Export controls:
• one source: Source Library > Actions > Export Source
• every source: Source Library > Export All Sources ZIP

Rollback:
ROLLBACK_TO_V0_6_2.bat extracts the exact prior v0.6.2 package separately.
The older v0.6.1 and v0.5.1 rollback options remain included.


RESPONSIVE AUDIO METERS + 4K PREVIEW — v0.6.4
------------------------------------------------
Audio meter fixes:
• FFmpeg silence reported as M: -inf now becomes -70 dB / zero meter.
• If a worker stops receiving meter readings, the displayed level falls back
  to silence instead of holding the last loud value.
• Screen recordings with several audio devices no longer combine the current
  reading with the historical maximum.
• Meter movement choices are Raw, Light, Medium and Heavy.
• Light is the default: approximately 20 ms rise and 90 ms fall, so incoming
  audio remains easy to see without the slow floating effect.

Live preview quality presets:
• Lowest 160 px
• Low 320 px
• Balanced 640 px
• HD 720p / 1280 px
• Full HD 1080p / 1920 px
• QHD 1440p / 2560 px
• 4K UHD / 3840 px
• Custom 160-4096 px

4K with a fast refresh rate can use substantial network bandwidth and worker
encoding power, particularly in Live All. Restart an existing Preview after
changing quality. Preview remains unlimited until Stop or Start Recording.

Rollback:
ROLLBACK_TO_V0_6_3.bat extracts the exact previous v0.6.3 package separately.


SOURCE IMPORT FORMAT REPAIR — v0.6.5
------------------------------------
The old importer accepted only one exact JSON shape. It could show:

    Could not import that source: Unsupported or missing source type

when the user selected:
• Export All manifest.json
• the whole Source Library ZIP
• an older source JSON
• a raw sources.json list
• a config backup ZIP

v0.6.5 accepts:
• current .vicsource.json files
• the complete VIC_Source_Library_....zip directly
• raw source JSON objects
• sources.json arrays
• config/backup ZIP files containing config/sources.json
• common older type names such as desktop, webcam, microphone, loopback,
  YouTube, network_stream and watch_folder

Select the original Export All ZIP directly. Do not extract and select
manifest.json by itself. The form now explains this and gives a specific error
when manifest.json is selected alone.

Every imported source:
• receives a new ID
• starts disabled
• is not archived or favourited
• keeps its source options for editing/testing

ROLLBACK_TO_V0_6_4.bat extracts the exact prior v0.6.4 package separately.


FULLSCREEN AND DASHBOARD-WIDE ZOOM — v0.6.6
-------------------------------------------
A fixed display-control bar now appears on every VIC Dashboard screen:

    Fullscreen   −   [zoom slider]   100%   +   Default 100%

Zoom applies to the complete interface:
• Dashboard
• Source Library and Add/Edit Source
• Workers
• Live and Live All
• Health
• Recordings and transfer queue
• Storage
• Settings
• Portable Tools
• Jobs and Help
• previews, audio meters, cards, tables, text and controls

Ways to zoom:
• drag the slider
• press − or +
• hold Ctrl and roll the mouse wheel anywhere
• roll the mouse wheel directly over the slider
• Ctrl + / Ctrl −
• Ctrl 0 or Default 100% to reset

Range: 40% to 200% in 5% steps.

The selected zoom is stored in the browser, not in the VIC folder. It remains
the same when moving between VIC pages and synchronises to other open browser
tabs for the same Dashboard.

Settings now contains a matching Dashboard zoom and fullscreen card.

Fullscreen uses the browser Fullscreen API. The button changes to Exit
Fullscreen while active. F11 remains an alternative if the browser blocks the
Fullscreen API.

ROLLBACK_TO_V0_6_5.bat extracts the exact prior v0.6.5 package separately.


LIVE ALL REFLOW WHILE ZOOMING OUT — v0.6.7
------------------------------------------
v0.6.6 zoomed the Live All page visually, but the page was still laid out
inside the same normal-width container. The result was always about three
source cards per row with unused space at the sides.

v0.6.7 keeps 100% exactly as before. Below 100%, Live All receives extra
pre-zoom layout width equal to the inverse zoom:

• 100% -> original familiar layout
• 80%  -> 125% layout width before scaling
• 70%  -> about 143% layout width before scaling
• 50%  -> 200% layout width before scaling
• 40%  -> 250% layout width before scaling

After browser zoom is applied, the interface still fills the physical screen.
The responsive CSS Grid uses that extra layout width to add more source cards
across each row rather than leaving blank side areas.

Only Live All receives this reflow. Other tabs continue using the normal
all-page zoom behaviour from v0.6.6.

Fullscreen changes and browser resizing automatically recalculate the layout.

ROLLBACK_TO_V0_6_6.bat extracts the exact previous package separately.


LIVE ALL PROPORTIONAL SCALE REPAIR — v0.6.8
-------------------------------------------
v0.6.7 combined CSS zoom with an expanded root width. On some Chrome/Windows
layouts this produced incorrect proportions, a narrow centre area, or page
dimensions that did not match the visible result.

v0.6.8 uses a dedicated transform viewport for Live All:

• 100% keeps the familiar original layout
• below 100%, cards, text, previews and audio meters scale proportionally
• inverse layout width gives CSS Grid real room for additional columns
• the transformed result fills the physical browser width
• a calculated wrapper height prevents a large invisible page area
• ResizeObserver updates the height when previews/cards change
• browser resize and fullscreen changes recalculate automatically

Other VIC pages retain the normal v0.6.6 page zoom behaviour.

ROLLBACK_TO_V0_6_7.bat extracts the exact previous package separately.


SOURCE LIBRARY ENABLE ALL / DISABLE ALL — v0.6.9
------------------------------------------------
Source Library now contains:

    Enable All
    Disable All

Enable All:
• enables every non-archived source
• does not restore or enable archived sources
• reports how many sources changed and how many remained archived

Disable All:
• disables every inactive source
• never interrupts an active Test, Preview or recording
• reports how many active sources were safely skipped
• after stopping those sources, pressing Disable All again disables them too

Both buttons display a confirmation prompt before changing the library.
No source settings, recordings or archived entries are deleted.

ROLLBACK_TO_V0_6_8.bat extracts the exact previous package separately.


ARMED AUTO START / WAIT FOR STREAM — v0.7.0
--------------------------------------------
This is different from retrying after failure. Some offline livestream inputs
remain open forever and therefore never fail. Arm Auto Start uses a separate,
time-limited availability probe:

• supported: RTSP, direct network streams, single/upcoming website streams
• no empty recording is created while offline
• Twitch "The channel is not currently live" is treated as waiting, not failure
• every probe has a timeout, so a hanging stream cannot block VIC
• Live cards show WAITING FOR STREAM and the check count
• when real media is detected, the same job changes into a real recording
• the one-shot armed flag turns off after recording actually starts
• Disarm Auto Start or Stop cancels the watcher
• armed state survives Dashboard/worker restarts and waits for a worker

Source management now also includes:

    Automatically retry when a manual Start fails before recording begins

This uses the same Retry / reconnect delay. It is separate from:

    Automatically reconnect after an unexpected source failure

The first handles a recording that never starts. The second handles a recording
that had started and then unexpectedly stopped.

Auto Start availability interval is configurable from 2 to 300 seconds.
Default: 10 seconds.

ROLLBACK_TO_V0_6_9.bat extracts the exact previous package separately.


LIVE CURRENT SIZE AND ACTUAL DISK WRITE RATE — v0.7.1
------------------------------------------------------
Recording Health now shows Current file size and Actual disk write rate.

Example:

    Current file size:       2.34 GB
    Actual disk write rate:  18.56 Mb/s · 2.21 MB/s

Lowercase b means bits. Uppercase B means bytes. One byte equals eight bits.

The rate comes from real recording-file growth over a short rolling window. It
is separate from FFmpeg's Current bitrate value. The measurement includes the
main recording, companion audio files and Twitch/website temporary .part files.
Older files already present in the same website folder are excluded.

The previous size logic could add FFmpeg total_size to the same Windows file
size. v0.7.1 uses the real file size first and FFmpeg only as a fallback.

Both the Dashboard and workers must use v0.7.1.
ROLLBACK_TO_V0_7_0.bat extracts the exact previous package separately.


V0.8.0 — WEBSITE FINISH, LIVE QUALITY AND STORAGE MANAGER
=========================================================

Finish Capture
--------------
Website recording cards have Finish Capture. This stops only VIC's yt-dlp
capture, not Twitch/YouTube/OBS. VIC requests a graceful stop and then remuxes
or renames new .part files automatically. Force Stop remains available when a
process does not respond.

Website sources in Live All
---------------------------
Every configured source now receives an IDLE card before it has ever run.
Website sources therefore appear while offline and can be Started or Armed.

Active preview quality
----------------------
Global quality changes and each Live All card's quality dropdown update active
images without stopping the recording. The worker resizes/compresses the JPEG
before sending it and the Dashboard also resizes on delivery. This reduces LAN,
Dashboard and browser load immediately. The FFmpeg process's own preview-output
resolution changes when that capture is next started; FFmpeg cannot alter an
already-created scale filter safely without rebuilding that process.

Advanced Storage Manager
------------------------
Storage supports named worker paths, UNC/network paths, enable/disable/remove,
folder opening, per-path 128 MB read/write benchmarks, detected drive details,
and source-specific recording destinations.

Live All offers Roll recording to storage. VIC never moves an open file. It:
1. closes the current segment gracefully
2. starts a new segment on the selected same-worker storage
3. SHA-256 and size verifies the closed segment at the destination
4. deletes the old copy only after verification

This does not stop the remote Twitch/YouTube/OBS broadcast. There can be a short
capture gap while the source is released and reopened.

ROLLBACK_TO_V0_7_1.bat extracts the exact previous v0.7.1 package separately.


SECOND-PC WORKER SETUP REPAIR — v0.8.1
---------------------------------------
v0.8.0 added Pillow for per-source live preview resizing, but START_WORKER.bat
and SETUP_WORKER_GUI.bat still checked only the older dependency list. A second
PC could therefore appear configured and then immediately stop with a missing
PIL/Pillow import.

v0.8.1 fixes this by:
• checking and installing every worker dependency, including Pillow
• allowing the worker to connect even if Pillow is temporarily unavailable
• adding REPAIR_AND_SETUP_WORKER.bat
• adding TEST_SECOND_PC_WORKER_SETUP.bat
• testing the Dashboard address and cluster token separately
• adding a Cluster token field to Worker Setup
• adding Import old worker.json to Worker Setup
• adding a token-test endpoint that does not create a fake worker entry
• updating Prepare Worker Copy so it contains the correct current token and
  clearer second-PC instructions

Recommended second-PC setup:
1. On the Main Dashboard open Portable Tools.
2. Download Prepare Worker Copy.
3. Extract that ZIP on the second PC.
4. Run REPAIR_AND_SETUP_WORKER.bat.
5. Press Test Address + Token.
6. Press Save, then Open Worker BAT Only.

Existing second-PC folder:
1. Extract v0.8.1.
2. Run REPAIR_AND_SETUP_WORKER.bat.
3. Press Import old worker.json and choose the old working
   v0.7.1/config/worker.json.
4. Press Test Address + Token.
5. Save and start the worker.

ROLLBACK_TO_V0_8_0.bat extracts the exact previous release separately.


SOURCE LIBRARY WORKER ASSIGNMENT — v0.8.2
-----------------------------------------
After importing sources, Source Library now has a "Set worker after import"
section.

Bulk scopes:
• Imported sources — recommended
• All disabled, non-archived sources
• Sources using Automatic or a missing worker
• Every non-archived source

Choose Automatic or any registered worker, then press:

    Set Worker for Selected Group

Every source row also has its own worker dropdown and Set button.

Safety:
• assigning a worker does not enable or start a source
• active tests, previews and recordings are skipped
• archived state is unchanged
• offline registered workers remain selectable
• when saved recording storage belongs to a different worker, VIC clears the
  incompatible storage selection and reports it
• existing imported sources ending in "(Imported)" are recognised even when
  they were imported before v0.8.2

Recommended after import:
1. Open Source Library.
2. Leave "Imported sources — recommended" selected.
3. Choose the second PC.
4. Press Set Worker for Selected Group.
5. Test sources, then enable them.

ROLLBACK_TO_V0_8_1.bat extracts the exact previous package separately.


TWITCH / YOUTUBE WEBSITE ROUTING AND PLAYLIST REPAIR — v0.8.3
----------------------------------------------------------------
The v0.8.2 log could show FFmpeg receiving a normal Twitch webpage:

    ffmpeg ... -i https://www.twitch.tv/channel
    Invalid data found when processing input

A Twitch or YouTube webpage is not a direct FFmpeg media URL. It must be
resolved through yt-dlp first.

v0.8.3 repairs this in both the Dashboard and worker:
• existing imported network sources containing Twitch/YouTube page URLs are
  automatically treated as Website video or livestream sources
• the repair happens again at worker runtime, so the current saved source does
  not need to be deleted or re-imported
• direct .m3u8, .mpd and media-file URLs remain direct FFmpeg sources
• the repeated Start retry now retries the correct yt-dlp recorder

Website mode now offers:
• Auto Detect — playlist URLs record every item
• Single item only — deliberately ignore the rest of a playlist
• Entire playlist — record every available item separately
• Upcoming scheduled live event

For old sources saved as Single before this choice existed, a YouTube URL with
a list= value is upgraded automatically to Entire playlist. New explicit
Single item choices are respected.

Playlist recording uses --yes-playlist, continues past unavailable/private
entries, saves each item separately and keeps a download archive so starting
the same source later skips already completed items.

ROLLBACK_TO_V0_8_2.bat extracts the exact previous release separately.


APPLICATION WINDOW PICKER + SOURCE-SETUP AUDIO METERS — v0.8.4
----------------------------------------------------------------
Application-window capture no longer requires manually typing an exact title.
Choose Application window, then press:

    Load Application Windows

VIC asks the selected worker PC for every visible titled application window.
The picker includes:
• search by title or process name
• application process and PID to distinguish similar windows
• Use selected window button
• double-click selection
• Refresh list

The worker refreshes its lightweight window inventory every two seconds. VIC
still stores the exact title because FFmpeg needs that title for capture.

Live setup audio meters are now available on:
• Camera / capture card / OBS optional audio device
• Microphone or audio device
• Speaker or headphone output
• existing screen-capture multi-audio rows

Each single-device page contains Start live meter and Stop meter. Selecting a
loaded device also starts its meter automatically. Setup meters monitor only;
they never save audio and expire automatically after 15 minutes.

Both Dashboard and workers must use v0.8.4 for remote application-window lists.
ROLLBACK_TO_V0_8_3.bat extracts the exact previous package separately.


MULTIPLE APPLICATION WINDOWS + PROCESS-ONLY AUDIO — v0.8.5
-----------------------------------------------------------
+ Add source now includes:

    Multiple application windows

Choose one worker, load its currently open windows, search the list and tick as
many as required. Saving once creates one independent normal screen source for
each selected window. Each can then be tested, previewed, enabled, disabled,
recorded, assigned to storage or moved to another worker separately.

Application audio:
• enabled by default for the multi-window creator
• uses Windows Application Loopback process capture
• includes only the selected process and its child processes
• does not capture the complete speaker/headphone mix
• does not require choosing a speaker device
• displays a live audio meter during Test/Preview/recording
• is merged into the final MKV when Stop is pressed
• if merging fails, VIC retains the video-only MKV and application-audio WAV
  instead of deleting either original

Windows requirement:
• Windows build 20348 or later: officially documented support
• Windows 10 build 19041–20347: experimental compatibility attempt
• the included tools/vic_process_audio.exe helper

Browser limitation:
Chrome may place several tabs/windows under a shared Chrome process tree. In
that case Windows can include audio from another sounding Chrome tab in that
same process tree. It still does not include unrelated applications such as a
game or Discord.

A normal single Application window source now also has a checkbox:

    Capture only this application's process audio

Choose the window from Load Application Windows so VIC saves its current PID.

ROLLBACK_TO_V0_8_4.bat extracts the exact previous package separately.


WINDOWS 10 BUILD 19045 PROCESS-AUDIO REPAIR — v0.8.6
-----------------------------------------------------
v0.8.5 rejected every Windows build below 20348 before trying the included
process-audio helper. This was too strict.

Microsoft officially documents build 20348 as the minimum for
AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS. However, the same API path is known to
work on many fully updated Windows 10 2004+ installations.

v0.8.6 therefore uses these rules:

• build 20348 or later:
  normal officially documented process-audio path

• build 19041 through 20347, including Windows 10 22H2 build 19045:
  Windows 10 compatibility mode; VIC attempts the real API

• build below 19041:
  remains blocked as too old

• build number cannot be read:
  VIC attempts the real API instead of refusing without evidence

Compatibility mode still captures the selected application process tree only.
It does not fall back to speaker/headphone loopback.

When the actual Windows API rejects activation, VIC reports the real HRESULT
or timeout and explains that the compatibility attempt failed on that
particular machine. Video files are not silently replaced with system audio.

Both Dashboard and worker should use v0.8.6 so the picker displays the correct
compatibility status.

Run TEST_WINDOWS10_PROCESS_AUDIO.bat, then use the source's Test button for the
real application-audio check on that PC.

ROLLBACK_TO_V0_8_5.bat extracts the exact previous release separately.


AUDIO FILE ARRANGEMENT — v0.8.7
--------------------------------
Desktop/application-window sources now offer three choices:

1. Embed audio in the video only
   • final MKV contains the selected application/loopback audio
   • temporary companion WAV files are removed only after a successful merge

2. Keep video and separate audio files only
   • preserves the former screen-loopback behaviour
   • video and WAV files remain separate

3. Embed audio in the video and also keep separate files
   • final MKV plays normally with audio included
   • original WAV files remain beside it for editing, backup or remixing

The setting controls:
• Windows application/process audio
• speaker/headphone/HDMI loopback audio selected on screen sources

Microphone/input and camera audio already records as named tracks inside the
MKV and remains embedded.

Compatibility:
• older application-process sources keep their prior Embed-only behaviour
• older screen loopback sources keep their prior Separate-only behaviour
• imported/exported sources carry the selected mode automatically

Safety:
• merging starts only after the recording has stopped and files are closed
• video is stream-copied rather than re-encoded during the audio merge
• when merging fails, VIC keeps the original video and every WAV file
• separate WAV files are deleted only after a verified successful merge and
  only when Embed-only was selected

Both Dashboard and workers must use v0.8.7.

ROLLBACK_TO_V0_8_6.bat extracts the exact prior package separately.


DISTRIBUTED LIVE CAPTURE AND PROCESSING — v0.9.0
-------------------------------------------------
Every source now has two independent worker choices:

    Capture worker
    Processing worker

Capture worker:
• physically sees the application/window/monitor/camera/audio device
• performs the unavoidable device read
• when another PC processes the source, performs a lightweight low-latency
  relay encode only

Processing worker:
• receives the live relay
• performs the final FFmpeg encoding
• creates Live/Live All previews and audio meters
• writes the recording and calculates file/disk statistics
• may be the same PC as the capture worker

Processing choices:
• Same as capture worker — original direct one-PC behaviour
• Auto choose best available worker
• any specific registered worker

Auto selection uses configured processing slots, current active processing
jobs, CPU, memory, GPU availability and free disk space. The capture worker is
included as a normal candidate, so a PC can always process its own sources.

Each worker has Worker Setup settings:
• Live processing slots: 1–64
• Allow this PC to process live sources captured by other workers
• Relay advertised address: normally blank; use a LAN IP only for multi-NIC PCs

Cross-worker relays use TCP ports 39000–39199 on the processing worker.
Run ALLOW_VIC_LIVE_RELAY_FIREWALL.bat once on every PC that will receive live
processing from another worker. It adds a Private-network inbound rule.

Supported cross-worker live relays:
• desktop, monitor and application-window capture
• true per-process application audio
• selected speaker/headphone loopback audio
• camera/capture card and its audio
• microphone/audio device
• speaker/headphone output source
• RTSP, direct network streams and media files

Website sources run yt-dlp directly on the chosen processing worker. When that
source needs browser cookies, the processing worker must have the required
browser profile/cookies. Folder-watch sources stay on their capture worker.

Audio-file arrangement is respected on the processing worker:
• Embed only: playable MKV with audio
• Separate only: video MKV plus separate multitrack MKA
• Embed and separate: playable MKV plus separate multitrack MKA

Live All includes Move live processing. VIC closes the old path and restarts it
on the selected worker. This is safe but produces a short capture/recording gap;
it is not a seamless zero-frame migration.

Practical capacity is limited by capture hardware, processing CPU/GPU, LAN
bandwidth and storage speed. VIC distributes work up to configured slots; it
does not pretend the hardware is unlimited.
