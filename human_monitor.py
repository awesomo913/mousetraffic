"""Human mouse override detection.

Watches mouse position in a background thread. When the human moves the mouse
(movement not caused by automation), all automation pauses. Resumes when:
  1. Mouse is idle for IDLE_RESUME_SECONDS (default 10s), OR
  2. Mouse moves to the top-right corner of the screen (resume zone)

How it detects human vs. automation movement:
  - Automation clients call mark_automation_move() before moving the mouse
  - Any movement NOT preceded by mark_automation_move() is assumed human
  - Uses a small position-change threshold to ignore jitter

The resume zone (top-right corner) lets the user explicitly signal
"I'm done, bots can have the mouse back" by flicking to the corner.
"""

import ctypes
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# How often to check mouse position (ms)
POLL_INTERVAL_MS = 100

# How far the mouse must move to count as real movement (pixels)
MOVEMENT_THRESHOLD = 5

# Seconds of idle before auto-resuming automation
IDLE_RESUME_SECONDS = 10.0

# Resume zone: top-right corner. Mouse within this box = "I'm done"
RESUME_ZONE_SIZE = 80  # pixels from edge


@dataclass
class ScreenInfo:
    width: int
    height: int


def _get_screen_size() -> ScreenInfo:
    """Get primary monitor resolution."""
    try:
        user32 = ctypes.windll.user32
        return ScreenInfo(
            width=user32.GetSystemMetrics(0),
            height=user32.GetSystemMetrics(1),
        )
    except Exception:
        return ScreenInfo(width=1920, height=1080)


def _get_cursor_pos() -> tuple[int, int]:
    """Get current cursor position."""
    try:
        import ctypes.wintypes
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        return (point.x, point.y)
    except Exception:
        return (0, 0)


class HumanMouseMonitor:
    """Detects when a human is using the mouse and pauses automation.

    Usage:
        monitor = HumanMouseMonitor(on_override_change=my_callback)
        monitor.start()

        # Automation code should call this BEFORE moving the mouse:
        monitor.mark_automation_move()
        pyautogui.click(100, 200)

        # Check if human is using the mouse:
        if monitor.is_human_active:
            print("Waiting for human to finish...")
    """

    def __init__(
        self,
        idle_resume_seconds: float = IDLE_RESUME_SECONDS,
        resume_zone_size: int = RESUME_ZONE_SIZE,
        on_override_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        self._idle_resume = idle_resume_seconds
        self._resume_zone = resume_zone_size
        self._on_change = on_override_change

        self._human_active = False
        self._last_human_move: float = 0.0
        self._last_pos: tuple[int, int] = (0, 0)
        self._automation_moving = False
        self._automation_move_expires: float = 0.0

        self._screen = _get_screen_size()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_human_active(self) -> bool:
        """True if a human is currently using the mouse."""
        return self._human_active

    @property
    def seconds_since_human_move(self) -> float:
        """Seconds since last detected human mouse movement."""
        if self._last_human_move == 0:
            return float("inf")
        return time.time() - self._last_human_move

    def mark_automation_move(self) -> None:
        """Call this BEFORE your automation moves the mouse.

        Gives a 0.5s window where any mouse movement is considered automation,
        not human. This prevents the monitor from triggering on bot moves.
        """
        with self._lock:
            self._automation_moving = True
            self._automation_move_expires = time.time() + 0.5

    def force_resume(self) -> None:
        """Force-clear human override (for the tray menu)."""
        with self._lock:
            if self._human_active:
                self._human_active = False
                self._last_human_move = 0
                logger.info("Human override force-cleared")
                self._notify(False)

    def start(self) -> None:
        """Start monitoring in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._last_pos = _get_cursor_pos()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("Human mouse monitor started (idle=%ds, zone=%dpx)",
                     int(self._idle_resume), self._resume_zone)

    def stop(self) -> None:
        """Stop monitoring."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _notify(self, is_active: bool) -> None:
        """Notify listener of override state change."""
        if self._on_change:
            try:
                self._on_change(is_active)
            except Exception as e:
                logger.error("Override change callback error: %s", e)

    def _is_in_resume_zone(self, x: int, y: int) -> bool:
        """Check if cursor is in the top-right corner resume zone."""
        return (
            x >= self._screen.width - self._resume_zone
            and y <= self._resume_zone
        )

    def _monitor_loop(self) -> None:
        """Main polling loop - runs in background thread."""
        while not self._stop_event.is_set():
            self._stop_event.wait(POLL_INTERVAL_MS / 1000.0)
            if self._stop_event.is_set():
                break

            try:
                self._check_mouse()
            except Exception as e:
                logger.debug("Monitor check error: %s", e)

    def _check_mouse(self) -> None:
        """Single check cycle: detect movement, classify, update state."""
        now = time.time()
        x, y = _get_cursor_pos()
        last_x, last_y = self._last_pos

        # Calculate movement distance
        dx = abs(x - last_x)
        dy = abs(y - last_y)
        moved = (dx + dy) > MOVEMENT_THRESHOLD

        if moved:
            self._last_pos = (x, y)

            with self._lock:
                # Check if this movement is from automation
                if self._automation_moving and now < self._automation_move_expires:
                    # This is an automation move - ignore it
                    return

                # Clear expired automation flag
                if now >= self._automation_move_expires:
                    self._automation_moving = False

            # This is human movement!
            self._last_human_move = now

            if not self._human_active:
                self._human_active = True
                logger.info("HUMAN OVERRIDE: Mouse movement detected at (%d, %d)", x, y)
                self._notify(True)

        # Check resume conditions (only when human is active)
        if self._human_active:
            # Condition 1: Mouse in top-right corner = explicit resume
            if self._is_in_resume_zone(x, y):
                self._human_active = False
                self._last_human_move = 0
                logger.info("HUMAN RESUME: Mouse moved to top-right corner (%d, %d)", x, y)
                self._notify(False)
                return

            # Condition 2: Idle for N seconds = auto resume
            idle_time = now - self._last_human_move
            if idle_time >= self._idle_resume:
                self._human_active = False
                logger.info("HUMAN RESUME: Mouse idle for %.1fs", idle_time)
                self._notify(False)
                return
