"""Mouse Traffic Controller server - manages the FIFO queue for mouse access.

Includes human override detection: when the user moves the mouse,
all automation pauses until the mouse is idle for 10s or moved to
the top-right corner of the screen.
"""

import asyncio
import json
import logging
import time
from typing import Optional

from . import DEFAULT_HOST, DEFAULT_PORT
from .protocol import (
    AcquireRequest, Priority, QueueEntry, ServerStatus,
    parse_message, format_ok, format_wait, format_granted,
    format_error, format_status, format_released, format_heartbeat_ok,
)
from .human_monitor import HumanMouseMonitor

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 30.0
CLEANUP_INTERVAL = 5.0


class TrafficQueue:
    """Priority FIFO queue for mouse access with dead-client detection
    and human override support."""

    def __init__(self) -> None:
        self._holder: Optional[QueueEntry] = None
        self._waiting: list[QueueEntry] = []
        self._total_served: int = 0
        self._start_time: float = time.time()
        self._waiters: dict[str, asyncio.Event] = {}
        self._on_change: Optional[callable] = None
        self._human_override: bool = False
        self._human_override_event = asyncio.Event()
        self._human_override_event.set()  # Start clear (not overridden)

    @property
    def current_holder(self) -> Optional[QueueEntry]:
        return self._holder

    @property
    def queue_depth(self) -> int:
        return len(self._waiting)

    @property
    def is_free(self) -> bool:
        return self._holder is None and not self._human_override

    @property
    def human_override(self) -> bool:
        return self._human_override

    def set_human_override(self, active: bool) -> None:
        """Called by the human monitor when override state changes."""
        was = self._human_override
        self._human_override = active
        if active and not was:
            self._human_override_event.clear()
            logger.info("HUMAN OVERRIDE ACTIVE - automation paused")
        elif not active and was:
            self._human_override_event.set()
            logger.info("HUMAN OVERRIDE CLEARED - automation resumed")
            # Now that human is done, promote next waiting client
            if self._holder is None:
                self._promote_next()
        self._notify_change()

    def set_on_change(self, callback: callable) -> None:
        self._on_change = callback

    def _notify_change(self) -> None:
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass

    def acquire(self, request: AcquireRequest) -> tuple[QueueEntry, bool]:
        """Request mouse access. Returns (entry, granted_immediately).

        If human override is active, nobody gets granted — everyone waits.
        """
        entry = QueueEntry(
            client_name=request.client_name,
            priority=request.priority,
            timeout=request.timeout,
            requested_at=time.time(),
            last_heartbeat=time.time(),
        )

        # If human is using the mouse, nobody gets through
        if self._human_override:
            logger.info("QUEUED %s (ticket=%s) - human override active",
                        entry.client_name, entry.ticket_id)
            self._waiting.insert(0, entry)
            waiter_event = asyncio.Event()
            self._waiters[entry.ticket_id] = waiter_event
            self._notify_change()
            return entry, False

        if self._holder is None:
            entry.is_holding = True
            entry.granted_at = time.time()
            self._holder = entry
            self._total_served += 1
            logger.info("GRANTED to %s (ticket=%s) - no queue",
                        entry.client_name, entry.ticket_id)
            self._notify_change()
            return entry, True

        insert_pos = len(self._waiting)
        for i, existing in enumerate(self._waiting):
            if request.priority < existing.priority:
                insert_pos = i
                break
        self._waiting.insert(insert_pos, entry)

        waiter_event = asyncio.Event()
        self._waiters[entry.ticket_id] = waiter_event

        logger.info("QUEUED %s (ticket=%s) at position %d",
                     entry.client_name, entry.ticket_id, insert_pos + 1)
        self._notify_change()
        return entry, False

    def release(self, ticket_id: str) -> bool:
        """Release mouse access. Returns True if found and released."""
        if self._holder and self._holder.ticket_id == ticket_id:
            old_name = self._holder.client_name
            self._holder = None
            logger.info("RELEASED by %s (ticket=%s)", old_name, ticket_id)
            self._promote_next()
            self._notify_change()
            return True

        for i, entry in enumerate(self._waiting):
            if entry.ticket_id == ticket_id:
                self._waiting.pop(i)
                self._waiters.pop(ticket_id, None)
                logger.info("DEQUEUED %s (ticket=%s)", entry.client_name, ticket_id)
                self._notify_change()
                return True

        return False

    def heartbeat(self, ticket_id: str) -> bool:
        """Update heartbeat timestamp. Returns True if found."""
        if self._holder and self._holder.ticket_id == ticket_id:
            self._holder.last_heartbeat = time.time()
            return True
        for entry in self._waiting:
            if entry.ticket_id == ticket_id:
                entry.last_heartbeat = time.time()
                return True
        return False

    def force_release(self) -> bool:
        """Force-release the current holder."""
        if self._holder:
            logger.warning("FORCE RELEASE of %s (ticket=%s)",
                           self._holder.client_name, self._holder.ticket_id)
            self._holder = None
            self._promote_next()
            self._notify_change()
            return True
        return False

    def cleanup_dead_clients(self) -> int:
        """Remove clients that haven't sent a heartbeat. Returns count removed."""
        now = time.time()
        removed = 0

        if self._holder:
            elapsed = now - self._holder.last_heartbeat
            if elapsed > HEARTBEAT_TIMEOUT:
                logger.warning("TIMEOUT: %s (ticket=%s) no heartbeat for %.0fs",
                               self._holder.client_name, self._holder.ticket_id, elapsed)
                self._holder = None
                removed += 1
                self._promote_next()

        dead_indices = []
        for i, entry in enumerate(self._waiting):
            elapsed = now - entry.last_heartbeat
            if elapsed > HEARTBEAT_TIMEOUT:
                logger.warning("TIMEOUT: queued %s (ticket=%s) no heartbeat for %.0fs",
                               entry.client_name, entry.ticket_id, elapsed)
                dead_indices.append(i)
                self._waiters.pop(entry.ticket_id, None)

        for i in reversed(dead_indices):
            self._waiting.pop(i)
            removed += 1

        if removed:
            self._notify_change()
        return removed

    def _promote_next(self) -> None:
        """Grant access to the next waiting client.

        Does nothing if human override is active — waits until human is done.
        """
        if self._holder is not None or not self._waiting:
            return
        if self._human_override:
            return  # Human is using mouse, don't promote anyone

        entry = self._waiting.pop(0)
        entry.is_holding = True
        entry.granted_at = time.time()
        entry.last_heartbeat = time.time()
        self._holder = entry
        self._total_served += 1

        waiter_event = self._waiters.pop(entry.ticket_id, None)
        if waiter_event:
            waiter_event.set()

        logger.info("PROMOTED %s (ticket=%s) - %d still waiting",
                     entry.client_name, entry.ticket_id, len(self._waiting))

    def get_status(self) -> ServerStatus:
        status = ServerStatus(
            current_holder=self._holder.ticket_id if self._holder else None,
            current_holder_name=self._holder.client_name if self._holder else None,
            queue_depth=len(self._waiting),
            queue=[e.to_status_dict() for e in self._waiting],
            total_served=self._total_served,
            uptime_seconds=time.time() - self._start_time,
        )
        # Inject human override state into status
        status.human_override = self._human_override
        return status

    def get_wait_event(self, ticket_id: str) -> Optional[asyncio.Event]:
        return self._waiters.get(ticket_id)

    def get_queue_position(self, ticket_id: str) -> int:
        for i, entry in enumerate(self._waiting):
            if entry.ticket_id == ticket_id:
                return i + 1
        return -1


class TrafficServer:
    """Async TCP server for the Mouse Traffic Controller.

    Includes a HumanMouseMonitor that detects when the user is moving
    the mouse and pauses all automation until they're done.
    """

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        self._queue = TrafficQueue()
        self._server: Optional[asyncio.Server] = None
        self._running = False
        self._on_status_change: Optional[callable] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Human override monitor
        self._human_monitor = HumanMouseMonitor(
            on_override_change=self._on_human_override,
        )

    @property
    def queue(self) -> TrafficQueue:
        return self._queue

    @property
    def human_monitor(self) -> HumanMouseMonitor:
        return self._human_monitor

    def set_on_status_change(self, callback: callable) -> None:
        self._on_status_change = callback
        self._queue.set_on_change(callback)

    def _on_human_override(self, active: bool) -> None:
        """Called by the monitor when human override state changes.

        This runs from the monitor's background thread, so we use
        call_soon_threadsafe to safely update the asyncio event loop.
        """
        self._queue.set_human_override(active)
        # Safely wake up asyncio waiters from this thread
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(lambda: None)  # poke the loop
            except RuntimeError:
                pass  # Loop closed

    async def start(self) -> None:
        """Start the server."""
        self._loop = asyncio.get_running_loop()
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        self._running = True
        logger.info("Mouse Traffic Controller listening on %s:%d", self._host, self._port)

        # Start human mouse monitor
        self._human_monitor.start()
        logger.info("Human mouse monitor active")

        asyncio.create_task(self._cleanup_loop())

        async with self._server:
            await self._server.serve_forever()

    def stop(self) -> None:
        """Stop the server."""
        self._running = False
        self._human_monitor.stop()
        if self._server:
            self._server.close()

    async def _cleanup_loop(self) -> None:
        """Periodically clean up dead clients."""
        while self._running:
            await asyncio.sleep(CLEANUP_INTERVAL)
            removed = self._queue.cleanup_dead_clients()
            if removed:
                logger.info("Cleaned up %d dead clients", removed)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single client connection."""
        addr = writer.get_extra_info("peername")
        logger.debug("Client connected from %s", addr)

        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=60.0)
                except asyncio.TimeoutError:
                    continue

                if not data:
                    break

                line = data.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                response = await self._process_message(line)
                writer.write(response.encode("utf-8"))
                await writer.drain()

        except (ConnectionResetError, BrokenPipeError):
            logger.debug("Client %s disconnected", addr)
        except Exception as e:
            logger.error("Client handler error: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _process_message(self, line: str) -> str:
        """Process a single protocol message and return response."""
        try:
            command, parts = parse_message(line)
        except ValueError as e:
            return format_error(str(e))

        if command == "ACQUIRE":
            return await self._handle_acquire(parts)
        elif command == "RELEASE":
            return self._handle_release(parts)
        elif command == "HEARTBEAT":
            return self._handle_heartbeat(parts)
        elif command == "STATUS":
            return self._handle_status()
        elif command == "FORCE_RELEASE":
            return self._handle_force_release()
        elif command == "MARK_MOVE":
            return self._handle_mark_move()
        else:
            return format_error(f"Unknown command: {command}")

    def _handle_mark_move(self) -> str:
        """Tell the human monitor that automation is about to move the mouse."""
        self._human_monitor.mark_automation_move()
        return format_ok()

    async def _handle_acquire(self, parts: list[str]) -> str:
        try:
            request = AcquireRequest.parse(parts)
        except ValueError as e:
            return format_error(str(e))

        entry, granted = self._queue.acquire(request)

        if granted:
            return format_ok(entry.ticket_id)

        wait_event = self._queue.get_wait_event(entry.ticket_id)
        if wait_event:
            try:
                await asyncio.wait_for(wait_event.wait(), timeout=request.timeout)
                return format_granted(entry.ticket_id)
            except asyncio.TimeoutError:
                self._queue.release(entry.ticket_id)
                return format_error("TIMEOUT waiting for mouse access")

        position = self._queue.get_queue_position(entry.ticket_id)
        return format_wait(position)

    def _handle_release(self, parts: list[str]) -> str:
        if len(parts) < 2:
            return format_error("RELEASE requires a ticket_id")
        ticket_id = parts[1]
        if self._queue.release(ticket_id):
            return format_released()
        return format_error(f"Unknown ticket: {ticket_id}")

    def _handle_heartbeat(self, parts: list[str]) -> str:
        if len(parts) < 2:
            return format_error("HEARTBEAT requires a ticket_id")
        ticket_id = parts[1]
        if self._queue.heartbeat(ticket_id):
            return format_heartbeat_ok()
        return format_error(f"Unknown ticket: {ticket_id}")

    def _handle_status(self) -> str:
        status = self._queue.get_status()
        return format_status(status)

    def _handle_force_release(self) -> str:
        if self._queue.force_release():
            return format_released()
        return format_error("No one is holding the mouse")
