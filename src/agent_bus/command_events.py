"""Records written by the roster commands and `agent-bus watch`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .logevents import Event, Level


@dataclass(frozen=True, slots=True, kw_only=True)
class RosterPolled(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "roster_polled"
    count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxPolled(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "inbox_polled"
    unread_only: bool
    count: int
    target: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeadHolderPolled(Event):
    level: ClassVar[Level] = "trace"
    message: ClassVar[str] = "dead_holder_polled"
    target: str
    found: bool
    holder: str | None = None
    holder_pid: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LeaveHostPidDisagrees(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "leave_host_pid_disagrees"
    name: str
    host_pid: int
    roster_pid: int


@dataclass(frozen=True, slots=True, kw_only=True)
class InboxUnresolved(Event):
    level: ClassVar[Level] = "warning"
    message: ClassVar[str] = "inbox_unresolved"
    target: str | None = None
