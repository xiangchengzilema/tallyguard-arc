"""Operational controls that do not participate in payment authorization."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Callable


class RateLimitExceeded(RuntimeError):
    """Raised when one tenant exceeds its configured request budget."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Tenant request limit exceeded.")
        self.retry_after_seconds = max(1, retry_after_seconds)


class TenantRateLimiter:
    """Thread-safe sliding-window limiter keyed only by opaque tenant ID."""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int = 60,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("Rate limit must be at least one request per window.")
        if window_seconds < 1:
            raise ValueError("Rate-limit window must be at least one second.")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def check(self, tenant_id: str) -> None:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            requests = self._requests[tenant_id]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.limit:
                retry_after = int(max(1, self.window_seconds - (now - requests[0])))
                raise RateLimitExceeded(retry_after)
            requests.append(now)


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    requests_total: int
    responses_by_class: dict[str, int]
    responses_by_endpoint: dict[str, int]
    latency_ms: dict[str, float]
    uptime_seconds: float


class RequestMetrics:
    """Bounded aggregate request metrics without tenant or invoice labels."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._started_at = clock()
        self._requests_total = 0
        self._responses_by_class: dict[str, int] = defaultdict(int)
        self._responses_by_endpoint: dict[str, int] = defaultdict(int)
        self._latency_count = 0
        self._latency_sum_ms = 0.0
        self._latency_max_ms = 0.0
        self._lock = RLock()

    def observe(self, *, endpoint: str, status_code: int, elapsed_seconds: float) -> None:
        elapsed_ms = max(0.0, elapsed_seconds * 1000)
        status_class = f"{status_code // 100}xx"
        endpoint_key = endpoint or "unmatched"
        with self._lock:
            self._requests_total += 1
            self._responses_by_class[status_class] += 1
            self._responses_by_endpoint[endpoint_key] += 1
            self._latency_count += 1
            self._latency_sum_ms += elapsed_ms
            self._latency_max_ms = max(self._latency_max_ms, elapsed_ms)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            mean_ms = (
                self._latency_sum_ms / self._latency_count
                if self._latency_count
                else 0.0
            )
            return MetricsSnapshot(
                requests_total=self._requests_total,
                responses_by_class=dict(sorted(self._responses_by_class.items())),
                responses_by_endpoint=dict(sorted(self._responses_by_endpoint.items())),
                latency_ms={
                    "mean": round(mean_ms, 3),
                    "max": round(self._latency_max_ms, 3),
                },
                uptime_seconds=round(max(0.0, self._clock() - self._started_at), 3),
            )
