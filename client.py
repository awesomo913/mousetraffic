"""Python client library for Mouse Traffic Controller.

Usage:
    from mousetraffic.client import TrafficClient

    # As a context manager (recommended):
    with TrafficClient("my-app") as lock:
        # You have exclusive mouse access here
        pyautogui.click(100, 200)

    # Manual control:
    client = TrafficClient("my-app")
    ticket = client.acquire()
    try:
        do_mouse_stuff()
    finally:
        client.release(ticket)
"""

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import DEFAULT_HOST, DEFAULT_PORT
from .protocol import Priority

logger = logging.getLogger(__name__)


@dataclass
class LockInfo:
    """Info about a currently held lock."""
    ticket_id: str
    client_name: str
    acquired_at: float


class TrafficClient:
    """Client for the Mouse Traffic Controller service.

    Can be used as a context manager for automatic acquire/release:
        with TrafficClient("my-app") as lock:
            # mouse is yours
            pass
    """

    def __init__(
        self,
        client_name: str,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        priority: Priority = Priority.NORMAL,
        timeout: float = 60.0,
        heartbeat_interval: float = 10.0,
    ) -> None:
        self._name = client_name
        self._host = host
        self._port = port
        self._priority = priority
        self._timeout = timeout
        self._heartbeat_interval = heartbeat_interval
        self._socket: Optional[socket.socket] = None
        self._ticket: Optional[str] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def is_holding(self) -> bool:
        return self._ticket is not None

    @property
    def ticket_id(self) -> Optional[str]:
        return self._ticket

    def _connect(self) -> socket.socket:
        """Create a new socket connection to the server."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout + 10)
        try:
            sock.connect((self._host, self._port))
        except ConnectionRefusedError:
            raise ConnectionError(
                f"Mouse Traffic Controller not running on {self._host}:{self._port}. "
                f"Start it first with: python -m mousetraffic"
            )
        return sock

    def _send_recv(self, sock: socket.socket, message: str) -> str:
        """Send a message and receive the response."""
        sock.sendall((message + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection")
            data += chunk
        return data.decode("utf-8", errors="ignore").strip()

    def acquire(self) -> str:
        """Acquire mouse access. Blocks until granted. Returns ticket_id."""
        with self._lock:
            if self._ticket:
                return self._ticket

            self._socket = self._connect()

            msg = f"ACQUIRE {self._name} priority={self._priority.name.lower()} timeout={self._timeout}"
            response = self._send_recv(self._socket, msg)

            if response.startswith("OK "):
                self._ticket = response.split(" ", 1)[1].strip()
            elif response.startswith("GRANTED "):
                self._ticket = response.split(" ", 1)[1].strip()
            elif response.startswith("ERROR"):
                self._close_socket()
                raise RuntimeError(f"Failed to acquire: {response}")
            elif response.startswith("WAIT"):
                response2 = self._wait_for_grant()
                if response2.startswith("GRANTED "):
                    self._ticket = response2.split(" ", 1)[1].strip()
                else:
                    self._close_socket()
                    raise RuntimeError(f"Failed while waiting: {response2}")
            else:
                self._close_socket()
                raise RuntimeError(f"Unexpected response: {response}")

            self._start_heartbeat()
            logger.info("Acquired mouse lock: %s (ticket=%s)", self._name, self._ticket)
            return self._ticket

    def _wait_for_grant(self) -> str:
        """Wait for the server to send GRANTED."""
        data = b""
        while True:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise ConnectionError("Server closed connection while waiting")
            data += chunk
            if b"\n" in data:
                return data.decode("utf-8", errors="ignore").strip()

    def mark_move(self) -> None:
        """Tell the server that automation is about to move the mouse.

        Call this right before any pyautogui.click/move/etc. so the
        human monitor knows this movement is from a bot, not a human.
        """
        try:
            if self._socket:
                self._send_recv(self._socket, "MARK_MOVE")
        except Exception:
            pass  # Non-critical, don't crash if server missed it

    def release(self, ticket_id: Optional[str] = None) -> None:
        """Release mouse access."""
        with self._lock:
            tid = ticket_id or self._ticket
            if not tid:
                return

            self._stop_heartbeat()

            try:
                if self._socket:
                    self._send_recv(self._socket, f"RELEASE {tid}")
            except Exception as e:
                logger.warning("Error releasing: %s", e)
            finally:
                self._ticket = None
                self._close_socket()
                logger.info("Released mouse lock: %s (ticket=%s)", self._name, tid)

    def _start_heartbeat(self) -> None:
        """Start background heartbeat thread."""
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        """Stop the heartbeat thread."""
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=2)
            self._heartbeat_thread = None

    def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to keep the lock alive."""
        while not self._heartbeat_stop.is_set():
            self._heartbeat_stop.wait(self._heartbeat_interval)
            if self._heartbeat_stop.is_set():
                break
            try:
                if self._socket and self._ticket:
                    self._send_recv(self._socket, f"HEARTBEAT {self._ticket}")
            except Exception as e:
                logger.warning("Heartbeat failed: %s", e)
                break

    def _close_socket(self) -> None:
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    @staticmethod
    def get_status(
        host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
    ) -> dict:
        """Get current server status without acquiring."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((host, port))
            sock.sendall(b"STATUS\n")
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            response = data.decode("utf-8", errors="ignore").strip()
            if response.startswith("STATUS "):
                return json.loads(response[7:])
            return {"error": response}
        except ConnectionRefusedError:
            return {"error": "Server not running"}
        except Exception as e:
            return {"error": str(e)}
        finally:
            sock.close()

    @staticmethod
    def is_server_running(
        host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
    ) -> bool:
        """Check if the traffic server is running."""
        try:
            status = TrafficClient.get_status(host, port)
            return "error" not in status
        except Exception:
            return False

    def __enter__(self) -> "TrafficClient":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()

    def __del__(self) -> None:
        if self._ticket:
            try:
                self.release()
            except Exception:
                pass
