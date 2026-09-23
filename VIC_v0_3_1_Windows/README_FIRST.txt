VIC — VIDEO INGEST CLUSTER
WINDOWS v0.3.1
=========================

This update repairs dependency installation and adds clearer local-PC screen
selection, live source monitoring and the Recordings tab.

START
-----
1. Extract this ZIP into a new folder.
2. Double-click START_VIC.bat.
3. If packages are missing, START_VIC automatically runs INSTALL_VIC.bat.
4. Install FFmpeg with INSTALL_FFMPEG.bat if required.
5. Wait for "Local PC (this computer)" to appear online.

SCREEN SOURCES
--------------
When adding a screen, choose:
• Automatic
• Local PC (this computer)
• Another connected worker

Then click Load worker devices. You can select the entire desktop, a specific
monitor, or an application window.

AUDIO METER
-----------
For screen audio, choose a Windows audio input reported by the worker. Desktop
sound normally requires Stereo Mix or a loopback/virtual-audio device. The Live
tab displays an audio meter for running sources when FFmpeg can measure audio.

LIVE TAB
--------
The Live tab displays a refreshed preview image, status, output path and audio
meter. It is a lightweight monitoring preview rather than zero-delay live video.
Some capture devices may not provide a preview while another program has them
locked.

RECORDINGS TAB
--------------
The Recordings tab shows files reported by each worker and their complete paths.
The Open folder button opens Explorer on that worker PC. Remote files remain on
the worker unless you record to a shared network folder.

SECURITY
--------
Use VIC only on a trusted local network. Do not expose dashboard port 8765
directly to the public internet.
