VIC - VIDEO INGEST CLUSTER
WINDOWS FOUNDATION v0.1
=======================

This is the first verified Windows foundation package.

WHAT THIS VERSION DOES
----------------------
- Starts a central web dashboard.
- Lets worker PCs register with the dashboard.
- Lets you add RTSP sources.
- Lets the dashboard PC record RTSP sources independently using FFmpeg.
- Stores recordings in separate source folders.
- Includes Windows launchers and system checks.
- Opens the dashboard automatically in your browser.

QUICK START
-----------
1. Extract the ZIP.
2. Double-click INSTALL_DEPENDENCIES.bat
3. Double-click CHECK_SYSTEM.bat
4. Install FFmpeg if the check says it is missing.
5. Double-click START_DASHBOARD.bat

Dashboard address:
http://127.0.0.1:8765

ADDING ANOTHER WORKER PC
------------------------
1. Copy the extracted VIC folder to the other PC.
2. Open config\worker.json in Notepad.
3. Change dashboard_url to the dashboard PC IP, for example:
   http://192.168.1.100:8765
4. Double-click START_WORKER.bat

IMPORTANT
---------
This is the foundation release.

In this version:
- RTSP recording runs on the dashboard PC.
- Worker PCs can register and appear online.
- Remote workers do not yet execute recording jobs.

The next development step is remote worker recording and automatic assignment.
