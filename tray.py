"""System tray icon for Mouse Traffic Controller.

Shows the current state with color-coded icons:
  Green  = Mouse is free, no queue
  Yellow = An automation is using the mouse
  Red    = Automation active + others waiting
  Blue   = HUMAN OVERRIDE - user is using mouse, bots paused
  Gray   = Server idle / unknown
"""

import logging
import threading
from typing import Optional

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

logger = logging.getLogger(__name__)

ICON_SIZE = 64


def _create_icon_image(color: str, text: str = "") -> "Image.Image":
    """Create a tray icon with a colored circle and optional text."""
    img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    color_map = {
        "green": (46, 204, 113),
        "yellow": (241, 196, 15),
        "red": (231, 76, 60),
        "blue": (52, 152, 219),
        "gray": (149, 165, 166),
    }
    rgb = color_map.get(color, color_map["gray"])

    margin = 4
    draw.ellipse(
        [margin, margin, ICON_SIZE - margin, ICON_SIZE - margin],
        fill=rgb, outline=(255, 255, 255, 200), width=2
    )

    if text:
        try:
            font = ImageFont.truetype("segoeui.ttf", 24)
        except (OSError, IOError):
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        tx = (ICON_SIZE - tw) // 2
        ty = (ICON_SIZE - th) // 2 - 2
        draw.text((tx, ty), text, fill=(255, 255, 255), font=font)

    return img


class TrayIcon:
    """System tray icon that shows mouse traffic status."""

    def __init__(self, server) -> None:
        self._server = server
        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None

        if not TRAY_AVAILABLE:
            logger.warning("pystray not installed - tray icon disabled")
            return

        server.set_on_status_change(self._on_status_change)

    def start(self) -> None:
        """Start the tray icon in a background thread."""
        if not TRAY_AVAILABLE:
            return

        self._icon = pystray.Icon(
            name="MouseTraffic",
            icon=_create_icon_image("green"),
            title="Mouse Traffic: Free",
            menu=pystray.Menu(
                pystray.MenuItem("Mouse Traffic Controller", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Status: Free", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Force Release", self._on_force_release),
                pystray.MenuItem("Clear Human Override", self._on_clear_human),
                pystray.MenuItem("Quit", self._on_quit),
            ),
        )

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("Tray icon started")

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def _on_status_change(self) -> None:
        """Called when queue state changes."""
        if not self._icon:
            return

        try:
            status = self._server.queue.get_status()
            holder = status.current_holder_name
            depth = status.queue_depth
            human = status.human_override

            if human:
                # Human override - blue icon with hand symbol
                color = "blue"
                title = "Mouse Traffic: YOU (bots paused)"
                text = "\u270B"  # raised hand
                if depth > 0:
                    title += f" | {depth} waiting"
            elif holder is None:
                color = "green"
                title = "Mouse Traffic: Free"
                text = ""
            elif depth == 0:
                color = "yellow"
                title = f"Mouse: {holder}"
                text = "1"
            else:
                color = "red"
                title = f"Mouse: {holder} | {depth} waiting"
                text = str(depth + 1)

            self._icon.icon = _create_icon_image(color, text)
            self._icon.title = title

        except Exception as e:
            logger.error("Tray update error: %s", e)

    def _on_force_release(self, icon, item) -> None:
        self._server.queue.force_release()

    def _on_clear_human(self, icon, item) -> None:
        """Manually clear human override from tray menu."""
        self._server.human_monitor.force_resume()

    def _on_quit(self, icon, item) -> None:
        self._server.stop()
        icon.stop()
