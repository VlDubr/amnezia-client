from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, dt: datetime):
        self._dt = dt

    def now(self) -> datetime:
        return self._dt

    def set(self, dt: datetime) -> None:
        self._dt = dt

    def advance(self, seconds: float) -> None:
        self._dt += timedelta(seconds=seconds)
