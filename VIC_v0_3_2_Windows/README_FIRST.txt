VIC — VIDEO INGEST CLUSTER
WINDOWS v0.3.2 — SPEAKER LOOPBACK + MASS CAPTURE
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
