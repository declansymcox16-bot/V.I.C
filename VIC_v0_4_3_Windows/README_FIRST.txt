VIC — VIDEO INGEST CLUSTER
WINDOWS v0.4.3 — BULK CONTROLS + PHYSICAL DISPLAY MODES
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


SCREEN AUDIO DROPDOWN — v0.4.3
------------------------------
The Optional desktop/loopback audio device dropdown now contains:
• Microphones and DirectShow audio inputs
• Speakers, headphones, HDMI outputs and USB outputs detected through Windows loopback

Microphone/input choices are encoded into the screen MKV.
Speaker/output choices are recorded as a companion WAV file beside the screen MKV.
The Live view volume meter follows the selected speaker output.


SHARED SPEAKER LOOPBACK — v0.4.3
--------------------------------
VIC now opens each selected speaker/headphone output only once per worker.
Screen sources, standalone speaker sources, live meters and mass capture jobs
subscribe to that one stream. This prevents VIC sources from competing with
one another and fixes the common 0x8889000A error caused by duplicate VIC opens.

If 0x8889000A still appears before any VIC speaker source is running, an outside
program or driver has the playback device in exclusive mode. VIC cannot bypass
an external exclusive lock; disable exclusive mode or close that program.


BULK DASHBOARD BUTTONS — v0.4.3
-------------------------------
The Dashboard now includes:
• Test All — queues a short test for every configured source
• Start All — starts every source that is not already active
• Stop All — stops every active recording or test job

PHYSICAL MONITOR RESOLUTION — v0.4.3
------------------------------------
VIC is now per-monitor DPI aware and asks Windows for each display's active
physical signal mode. For example, a 4096x2160 display using 150% Windows
scaling should now appear as 4096x2160 rather than the scaled 2731x1440 size.

The worker also reports the active refresh rate where Windows provides it,
for example: Display 1 (Primary) — 4096x2160, 60 Hz at (0,0).


FASTER AUDIO METERS — v0.4.3
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


LIVE ALL CONTROL ROOM — v0.4.3
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


CLEAR / DELETE CONTROLS — v0.4.3
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

YOUTUBE / WEBSITE MODES — v0.4.3
--------------------------------
A website source now supports:
• Single video or livestream
• Playlist, saved as separate files
• Upcoming scheduled live event that waits until the event starts

Playlist mode stores downloaded-items.txt in the source's recording folder.
Starting the same playlist later skips items already recorded and captures new
entries. Upcoming-event mode uses a configurable retry interval and can request
the livestream from the beginning when supported.


EDIT SOURCES — v0.4.3
---------------------
Every Dashboard source now has an Edit button. Live All cards also include
Edit source. You can change the source name, assigned worker and all settings
without deleting and recreating it. Stop an active source before saving edits,
then run Test again.

OPTIONAL LOGIN PER WEBSITE SOURCE — v0.4.3
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


EDIT PAGE REPAIR — v0.4.3
-------------------------
The Edit button now safely transfers saved Windows device IDs, paths, URLs and
names to the browser using Base64-encoded JSON. This fixes the Internal Server
Error caused by certain source settings. Opening Edit does not require the
assigned worker to be online.

Future Dashboard HTTP 500 errors are written to:

    logs\dashboard_error.log

AUTOMATIC WORKER DISCOVERY AND BONDING
--------------------------------------
On a second PC, copy the whole VIC v0.4.3 folder and run START_WORKER.bat.
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


VERIFIED FILE TRANSFERS — v0.4.3
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


MASS RECORDING TRANSFERS — v0.4.3
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


TRANSFER QUEUE REPAIR — v0.4.3
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
unless the source and destination computers are both running VIC v0.4.3. This
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
