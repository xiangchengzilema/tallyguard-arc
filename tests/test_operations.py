import pytest

from tallyguard.operations import RateLimitExceeded, RequestMetrics, TenantRateLimiter


def test_rate_limit_is_isolated_per_tenant_and_recovers_after_window():
    now = [100.0]
    limiter = TenantRateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])

    limiter.check("tenant-a")
    limiter.check("tenant-a")
    limiter.check("tenant-b")

    with pytest.raises(RateLimitExceeded) as error:
        limiter.check("tenant-a")
    assert error.value.retry_after_seconds == 60

    now[0] = 161.0
    limiter.check("tenant-a")


def test_request_metrics_are_aggregate_and_bounded():
    now = [20.0]
    metrics = RequestMetrics(clock=lambda: now[0])
    metrics.observe(endpoint="list_invoices", status_code=200, elapsed_seconds=0.01)
    metrics.observe(endpoint="list_invoices", status_code=429, elapsed_seconds=0.03)
    now[0] = 21.0

    snapshot = metrics.snapshot()

    assert snapshot.requests_total == 2
    assert snapshot.responses_by_class == {"2xx": 1, "4xx": 1}
    assert snapshot.responses_by_endpoint == {"list_invoices": 2}
    assert snapshot.latency_ms == {"mean": 20.0, "max": 30.0}
    assert snapshot.uptime_seconds == 1.0
