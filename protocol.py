"""Wire protocol for Mouse Traffic Controller.

Simple text-based protocol over TCP so ANY language can use it.
Each message is a single line terminated by newline.

Client -> Server:
    ACQUIRE <client_name> [priority=normal] [timeout=60]
    RELEASE <ticket_id>
    HEARTBEAT <ticket_id>
    STATUS
    FORCE_RELEASE
    MARK_MOVE            # Tell server: automation is about to move the mouse

Server -> Client:
    OK <ticket_id>
    OK RELEASED
    OK HEARTBEAT
    WAIT <position_in_queue>
    GRANTED <ticket_id>
    ERROR <message>
    STATUS <json_payload>
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Optional


class Priority(IntEnum):
    URGENT = 0
    NORMAL = 1
    LOW = 2

    @classmethod
    def from_str(cls, s: str) -> "Priority":
        mapping = {"urgent": cls.URGENT, "normal": cls.NORMAL, "low": cls.LOW}
        return mapping.get(s.lower(), cls.NORMAL)


@dataclass
class AcquireRequest:
    client_name: str
    priority: Priority = Priority.NORMAL
    timeout: float = 60.0

    @classmethod
    def parse(cls, parts: list[str]) -> "AcquireRequest":
        """Parse: ACQUIRE my_app priority=normal timeout=60"""
        if len(parts) < 2:
            raise ValueError("ACQUIRE requires a client name")
        name = parts[1]
        priority = Priority.NORMAL
        timeout = 60.0
        for part in parts[2:]:
            if part.startswith("priority="):
                priority = Priority.from_str(part.split("=", 1)[1])
            elif part.startswith("timeout="):
                try:
                    timeout = float(part.split("=", 1)[1])
                except ValueError:
                    pass
        return cls(client_name=name, priority=priority, timeout=timeout)


@dataclass
class QueueEntry:
    """An entry in the mouse access queue."""
    ticket_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    client_name: str = ""
    priority: Priority = Priority.NORMAL
    timeout: float = 60.0
    requested_at: float = 0.0
    granted_at: Optional[float] = None
    last_heartbeat: float = 0.0
    is_holding: bool = False

    def to_status_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "client_name": self.client_name,
            "priority": self.priority.name,
            "is_holding": self.is_holding,
            "requested_at": self.requested_at,
            "granted_at": self.granted_at,
        }


@dataclass
class ServerStatus:
    """Full server status snapshot."""
    current_holder: Optional[str] = None
    current_holder_name: Optional[str] = None
    queue_depth: int = 0
    queue: list[dict] = field(default_factory=list)
    total_served: int = 0
    uptime_seconds: float = 0.0
    human_override: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def parse_message(line: str) -> tuple[str, list[str]]:
    """Parse a protocol message into (command, parts)."""
    parts = line.strip().split()
    if not parts:
        raise ValueError("Empty message")
    return parts[0].upper(), parts


def format_ok(ticket_id: str = "") -> str:
    if ticket_id:
        return f"OK {ticket_id}\n"
    return "OK\n"


def format_wait(position: int) -> str:
    return f"WAIT {position}\n"


def format_granted(ticket_id: str) -> str:
    return f"GRANTED {ticket_id}\n"


def format_error(message: str) -> str:
    return f"ERROR {message}\n"


def format_status(status: ServerStatus) -> str:
    return f"STATUS {status.to_json()}\n"


def format_released() -> str:
    return "OK RELEASED\n"


def format_heartbeat_ok() -> str:
    return "OK HEARTBEAT\n"
