<!-- claude-backend:generated:start -->
# mousetraffic

## Overview

- **Files**: 8 (.py (7), .md (1))
- **Entry points**: `__main__.py`
- **Dependencies**: pystray, Pillow
- **Key files**: `CLAUDE.md`, `requirements.txt`

## Structure

```
```

## Conventions

- Type hints are used extensively -- maintain them
- Use `logging.getLogger(__name__)` for all logging

## Modules

- `client.py` -- Python client library for Mouse Traffic Controller
- `human_monitor.py` -- Human mouse override detection
- `protocol.py` -- Wire protocol for Mouse Traffic Controller
- `server.py` -- Mouse Traffic Controller server - manages the FIFO queue for mouse access
- `tray.py` -- System tray icon for Mouse Traffic Controller

<!-- claude-backend:generated:end -->
