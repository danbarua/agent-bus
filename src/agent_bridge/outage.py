"""Log a run of cloud failures once, not once per failure.

A cloud that is down fails every call the same way, on every pass.
One `Outage` per cloud operation. It says when a run of failures began, again
each time the run doubles in length, and when it ended; between those it is
silent. It also decides when the next attempt is due, so a dead cloud is asked
less often the longer it stays dead.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from agent_bus import log as bus_log
from agent_bus.logevents import describe_error

from . import events as ev

T = TypeVar("T")

RETRY_CAP_SECONDS = 300.0
JITTER = 0.2
PERMANENT_STATUSES = frozenset({400, 401, 403, 404})


class CloudError(RuntimeError):
    """The cloud answered, and refused. The text is what callers already match on."""

    def __init__(self, op: str, status: int, detail: str) -> None:
        super().__init__(f"cloud refused {op}: HTTP {status} {detail}".strip())
        self.op = op
        self.status = status
        self.detail = detail


def is_permanent(exc: BaseException) -> bool:
    return isinstance(exc, CloudError) and exc.status in PERMANENT_STATUSES


@dataclass(frozen=True, slots=True)
class Attempt(Generic[T]):
    value: T | None = None
    error: Exception | None = None
    skipped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.skipped


class Outage:
    def __init__(
        self,
        op: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.op = op
        self._clock = clock
        self._wall = wall
        self._rng = rng
        self._reset()

    def _reset(self) -> None:
        self.consecutive = 0
        self._suppressed = 0
        self._began = 0.0
        self._since = ""
        self._retry_at = 0.0
        self._was_permanent: bool | None = None

    def due(self) -> bool:
        return self.consecutive == 0 or self._clock() >= self._retry_at

    def attempt(self, fn: Callable[[], T], *, interval: float, force: bool = False) -> Attempt[T]:
        """Run `fn` unless a failing operation is not yet due. `force` is for a
        caller that makes exactly one pass and must not skip it."""
        if not (force or self.due()):
            return Attempt(skipped=True)
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001  # a Protocol implementation can raise anything
            self._failed(exc, interval)
            return Attempt(error=exc)
        self._succeeded()
        return Attempt(value=value)

    def _retry_in(self, interval: float) -> float:
        exponent = min(self.consecutive - 1, 32)
        base = min(RETRY_CAP_SECONDS, interval * 2 ** exponent)
        return base * (1 + JITTER * (2 * self._rng() - 1))

    def _failed(self, exc: Exception, interval: float) -> None:
        now = self._clock()
        self.consecutive += 1
        if self.consecutive == 1:
            self._began = now
            self._since = bus_log.iso_utc(self._wall())
        retry = self._retry_in(interval)
        self._retry_at = now + retry
        permanent = is_permanent(exc)
        flipped = self._was_permanent is not None and permanent != self._was_permanent
        self._was_permanent = permanent
        power_of_two = (self.consecutive & (self.consecutive - 1)) == 0
        if not (power_of_two or flipped):
            self._suppressed += 1
            return
        fields: dict[str, Any] = {
            "op": self.op, "consecutive": self.consecutive, "since": self._since,
            "suppressed": self._suppressed, "retry_in_seconds": round(retry, 1),
            **describe_error(exc),
        }
        self._suppressed = 0
        if permanent:
            bus_log.emit(ev.CloudCallRefused(**fields))
        else:
            bus_log.emit(ev.CloudCallFailed(**fields))

    def _succeeded(self) -> None:
        if self.consecutive:
            bus_log.emit(ev.CloudCallRecovered(
                op=self.op, failures=self.consecutive, since=self._since,
                outage_seconds=round(self._clock() - self._began, 1)))
        self._reset()


@dataclass(frozen=True, slots=True)
class Paced:
    """One gate with the pace of the loop that is calling it right now."""

    gate: Outage
    interval: float
    force: bool

    def run(self, fn: Callable[[], T]) -> Attempt[T]:
        return self.gate.attempt(fn, interval=self.interval, force=self.force)


@dataclass(frozen=True, slots=True)
class Gates:
    roster: Outage
    pull: Outage
    push: Outage
    ack: Outage

    def paced(self, op: str, interval: float, force: bool) -> Paced:
        return Paced(getattr(self, op), interval, force)

    @classmethod
    def new(cls, **clocks: Callable[[], float]) -> Gates:
        return cls(*(Outage(op, **clocks) for op in ("roster", "pull", "push", "ack")))
