VIC — VIDEO INGEST CLUSTER
WINDOWS v0.3.5 — BULK CONTROLS + PHYSICAL DISPLAY MODES
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


SCREEN AUDIO DROPDOWN — v0.3.5
------------------------------
The Optional desktop/loopback audio device dropdown now contains:
• Microphones and DirectShow audio inputs
• Speakers, headphones, HDMI outputs and USB outputs detected through Windows loopback

Microphone/input choices are encoded into the screen MKV.
Speaker/output choices are recorded as a companion WAV file beside the screen MKV.
The Live view volume meter follows the selected speaker output.


SHARED SPEAKER LOOPBACK — v0.3.5
--------------------------------
VIC now opens each selected speaker/headphone output only once per worker.
Screen sources, standalone speaker sources, live meters and mass capture jobs
subscribe to that one stream. This prevents VIC sources from competing with
one another and fixes the common 0x8889000A error caused by duplicate VIC opens.

If 0x8889000A still appears before any VIC speaker source is running, an outside
program or driver has the playback device in exclusive mode. VIC cannot bypass
an external exclusive lock; disable exclusive mode or close that program.


BULK DASHBOARD BUTTONS — v0.3.5
-------------------------------
The Dashboard now includes:
• Test All — queues a short test for every configured source
• Start All — starts every source that is not already active
• Stop All — stops every active recording or test job

PHYSICAL MONITOR RESOLUTION — v0.3.5
------------------------------------
VIC is now per-monitor DPI aware and asks Windows for each display's active
physical signal mode. For example, a 4096x2160 display using 150% Windows
scaling should now appear as 4096x2160 rather than the scaled 2731x1440 size.

The worker also reports the active refresh rate where Windows provides it,
for example: Display 1 (Primary) — 4096x2160, 60 Hz at (0,0).
