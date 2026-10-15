"""Shared, deterministic time and action-boundary execution.

Units, labels and boundary stages are configuration. This module does not know
story names, UI modes, candidate weights, stamina rules or ending predicates.
Domain handlers retain those meanings while all execution paths share this
clock, dispatch order and progress loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable


def offset(value: int, name: str = "offset") -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


class BoundaryResult(Enum):
    HALT = "halt"


@dataclass(frozen=True)
class TimeAxis:
    unit: str
    start: int
    end: int
    slots: tuple[str, ...] = ()

    def __post_init__(self):
        if self.unit not in {"minute", "logical_step"}:
            raise ValueError("Unsupported time unit")
        offset(self.start, "start"); offset(self.end, "end")
        if self.end < self.start:
            raise ValueError("Time axis ends before it starts")
        if self.slots and (self.unit != "logical_step" or
                           any(type(s) is not str or not s for s in self.slots)):
            raise ValueError("Slot labels require a logical time axis")

    @classmethod
    def logical(cls, days: int, slots: Iterable[str]) -> "TimeAxis":
        offset(days, "days")
        labels = tuple(slots)
        if not days or not labels:
            raise ValueError("A logical schedule needs days and slot labels")
        return cls("logical_step", 0, days * len(labels) - 1, labels)

    def check(self, at: int) -> int:
        offset(at)
        if not self.start <= at <= self.end:
            raise ValueError("Time outside the observation axis")
        return at

    def label(self, at: int) -> dict[str, Any]:
        self.check(at)
        if not self.slots:
            return {"offset": at, "unit": self.unit}
        day, index = divmod(at, len(self.slots))
        return {"offset": at, "unit": self.unit, "day": day + 1,
                "slot": self.slots[index], "slot_index": index}


@dataclass(frozen=True)
class ActionWindow:
    start: int
    finish: int
    unit: str

    def __post_init__(self):
        offset(self.start, "action start"); offset(self.finish, "action finish")
        if self.finish < self.start or self.unit not in {"minute", "logical_step"}:
            raise ValueError("Invalid action interval")

    def elapsed(self, previous: int, at: int) -> int:
        """Accrue a partition of the real interval exactly once."""
        offset(previous); offset(at)
        if at < previous:
            raise ValueError("Progress cannot run backwards")
        return max(0, min(at, self.finish) - max(previous, self.start))


@dataclass
class BoundaryFrame:
    previous: int
    at: int
    axis: TimeAxis
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed(self) -> int:
        return self.at - self.previous

    @property
    def label(self) -> dict[str, Any]:
        return self.axis.label(self.at)


@dataclass(frozen=True)
class BoundaryStage:
    name: str
    apply: Callable[[BoundaryFrame], Any]


class TimeActionKernel:
    """One monotonic clock and one ordered boundary loop.

State mutations belong to registered stages; the caller owns their transaction
and persistence. Restore is explicit and never dispatches a historical effect.
"""
    def __init__(self, axis: TimeAxis, *, now: int | None = None):
        self.axis = axis
        self._now = axis.check(axis.start if now is None else now)
        self._dispatching = False

    @property
    def now(self) -> int:
        return self._now

    def restore_position(self, at: int) -> None:
        if self._dispatching and at != self._now:
            raise ValueError("Cannot change the clock during a boundary")
        self._now = self.axis.check(at)

    def window(self, duration: int, *, unit: str | None = None,
               start: int | None = None) -> ActionWindow:
        offset(duration, "duration")
        if unit is not None and unit != self.axis.unit:
            raise ValueError("Action duration uses a different time unit")
        begin = self.now if start is None else self.axis.check(start)
        return ActionWindow(begin, self.axis.check(begin + duration), self.axis.unit)

    def dispatch(self, at: int, stages: Iterable[BoundaryStage]) -> bool:
        self.axis.check(at)
        if self._dispatching or at < self.now:
            raise ValueError("Reentrant or backwards boundary")
        plan = tuple(stages)
        names = [s.name for s in plan]
        if len(names) != len(set(names)) or any(not s.name or not callable(s.apply) for s in plan):
            raise ValueError("Boundary stages need distinct names and handlers")
        frame = BoundaryFrame(self.now, at, self.axis)
        previous = self.now
        self._now = at
        self._dispatching = True
        try:
            for stage in plan:
                if stage.apply(frame) is BoundaryResult.HALT:
                    return True
            return False
        except BaseException:
            self._now = previous
            raise
        finally:
            self._dispatching = False

    def run(self, next_at: Callable[[int, int], int | None],
            stages: Iterable[BoundaryStage], *, until: int | None = None,
            before_boundary: Callable[[], Any] | None = None,
            after_boundary: Callable[[], Any] | None = None,
            on_error: Callable[[Any], None] | None = None) -> bool:
        """Advance only at provider boundaries; return whether a stage halted.

        A provider may request the current offset to consume pending work, but
        cannot spin forever there. Display points do not imply action effects.
        """
        limit = self.axis.check(self.axis.end if until is None else until)
        if limit < self.now:
            raise ValueError("Cannot run backwards")
        plan = tuple(stages)
        repeated = 0
        while True:
            at = next_at(self.now, limit)
            if at is None:
                return False
            if type(at) is not int or not self.now <= at <= limit:
                raise ValueError("Boundary provider returned an invalid time")
            repeated = repeated + 1 if at == self.now else 0
            if repeated > 1024:
                raise ValueError("Boundary provider made no time progress")
            previous = self.now
            backup = before_boundary() if before_boundary else None
            try:
                halted = self.dispatch(at, plan)
                if after_boundary:
                    after_boundary()
            except BaseException:
                self.restore_position(previous)
                if on_error:
                    on_error(backup)
                raise
            if halted or at == limit:
                return halted

    def sequence(self, points: Iterable[int]) -> Callable[[int, int], int | None]:
        """Adapt a finite semantic schedule to the same dynamic run loop."""
        ordered = iter(points)
        def next_at(now, limit):
            at = next(ordered, None)
            if at is not None and at > limit:
                return None
            return at
        return next_at
