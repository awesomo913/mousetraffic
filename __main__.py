"""Entry point: python -m mousetraffic"""

import asyncio
import logging
import sys

from . import DEFAULT_HOST, DEFAULT_PORT, __app_name__, __version__
from .server import TrafficServer
from .tray import TrayIcon


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    logger = logging.getLogger(__name__)
    logger.info("%s v%s starting on %s:%d", __app_name__, __version__, DEFAULT_HOST, DEFAULT_PORT)

    server = TrafficServer(DEFAULT_HOST, DEFAULT_PORT)

    tray = TrayIcon(server)
    tray.start()

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.stop()
        tray.stop()


if __name__ == "__main__":
    main()
