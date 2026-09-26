from collections import defaultdict, deque

from app.domain.clock import Clock


class RateLimiter:
    """In-process sliding window limiter; the panel runs as a single backend process."""

    def __init__(self, limit: int, window_s: float, clock: Clock):
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        now = self._clock.now().timestamp()
        hits = self._hits[key]
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
