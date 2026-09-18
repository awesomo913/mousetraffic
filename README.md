# Mouse Traffic

> A FIFO traffic cop for your mouse cursor, so multiple automation scripts (and you) never fight over control of it at the same time.

Mouse Traffic is a small local server that arbitrates mouse control between multiple automation clients. Each client requests the mouse through a shared queue and gets it in order; if a real person moves the mouse, all automation immediately pauses until it's idle again or flicked to a corner — so background bots never steal the cursor mid-task.

## Features
- **FIFO priority queue server** (`server.py`, asyncio TCP) that hands out exclusive mouse access in order, with dead-client cleanup via heartbeats.
- **Python client library** (`client.py`) for automation scripts to request/release the mouse.
- **Human-override detection** (`human_monitor.py`) — any real mouse movement pauses all automation until it's idle for 10s or moved to the top-right corner.
- **System tray icon** (`tray.py`) showing live server/queue status.
- **Wire protocol module** (`protocol.py`) defining the request/response messages between clients and server.
- **Packaged as a Windows exe** via a PyInstaller spec (`MouseTraffic.spec`).

## Stack
Python 3, asyncio, `pystray` + `Pillow` (tray icon), PyInstaller (Windows exe build).

## Getting started
**Requirements** — Python 3.11+.

**Run**
```bash
pip install -r requirements.txt
python -m mousetraffic
# or on Windows: run_server.bat
# or use the built exe: dist/MouseTraffic.exe
```

## Status
**Unmaintained / archived.** Personal project, published as-is — fork it, adapt it, take it over. No support or guarantees.

## License
[MIT](LICENSE) — free to use, fork, and build on.
