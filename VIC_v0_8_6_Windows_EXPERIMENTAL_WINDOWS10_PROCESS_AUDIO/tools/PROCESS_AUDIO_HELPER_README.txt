VIC process-only application audio helper
=========================================

This small Windows x64 helper activates the Windows Application Loopback API
for one target process ID and its child processes. It writes raw 16-bit PCM
to stdout. VIC worker.py writes the WAV and merges it into the final MKV.

It does not use or capture the whole speaker/headphone mix.
Officially documented minimum Windows build: 20348.
VIC compatibility attempt: Windows 10 build 19041 or later.
Builds 19041-20347 are experimental and depend on the real API activation result.
Source is included as vic_process_audio.go.
SHA-256: 61c26f8a5fe3383204365c821a0a6c5b402fecb00b6aebf36bbd960d22457266
