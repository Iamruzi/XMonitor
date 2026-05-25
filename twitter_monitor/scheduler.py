"""Polling interval jitter and backoff helpers."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class PollSchedule:
    min_seconds: int
    max_seconds: int
    backoff_max_seconds: int
    failures: int = 0
    rand_int: Callable[[int, int], int] = random.randint

    def record_success(self) -> None:
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1

    def next_delay(self) -> int:
        low, high = self._bounds()
        return self.rand_int(low, high)

    def _bounds(self) -> tuple[int, int]:
        normal_min = max(int(self.min_seconds), 1)
        normal_max = max(int(self.max_seconds), normal_min)
        backoff_max = max(int(self.backoff_max_seconds), normal_max)
        if self.failures <= 0:
            return normal_min, normal_max

        capped_failures = min(self.failures, 16)
        low = normal_max * (2 ** (capped_failures - 1))
        high = normal_max * (2 ** capped_failures)
        low = min(low, backoff_max)
        high = min(high, backoff_max)
        return low, max(low, high)


def poll_result_failed(result: dict[str, Any]) -> bool:
    for target_result in result.get("results", []):
        if isinstance(target_result, dict) and target_result.get("error"):
            return True
    return False
