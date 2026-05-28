"""Small process-local limiter for outbound X requests."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable


class XRequestLimiter:
    def __init__(
        self,
        *,
        min_delay_seconds: float,
        max_delay_seconds: float,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rand_float: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.min_delay_seconds = max(float(min_delay_seconds), 0.0)
        self.max_delay_seconds = max(float(max_delay_seconds), self.min_delay_seconds)
        self._sleeper = sleeper
        self._clock = clock
        self._rand_float = rand_float
        self._lock = threading.Lock()
        self._next_at = 0.0

    def __enter__(self) -> XRequestLimiter:
        self._lock.acquire()
        now = self._clock()
        wait_seconds = max(self._next_at - now, 0.0)
        if wait_seconds > 0:
            self._sleeper(wait_seconds)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        delay = self._rand_float(self.min_delay_seconds, self.max_delay_seconds)
        self._next_at = self._clock() + delay
        self._lock.release()

    def wait(self) -> None:
        with self:
            pass
