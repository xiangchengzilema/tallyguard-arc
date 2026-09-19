from tallyguard.loadtest import LoadConfiguration, execute_load_test


def test_synthetic_multi_tenant_load_smoke_has_no_double_payment():
    report = execute_load_test(
        LoadConfiguration(
            organizations=4,
            invoices=20,
            concurrency=8,
            duplicate_storm=20,
            timeout_seconds=10,
        )
    )
    summary = report["summary"]
    assert summary["successful_workflows"] == 20
    assert summary["failed_workflows"] == 0
    assert summary["duplicate_payment_count"] == 0
    assert summary["cross_tenant_attempts"] == 4
    assert summary["cross_tenant_attempts_denied"] == 4
    assert summary["duplicate_storm_successes"] == 20
    assert summary["duplicate_storm_unique_transaction_hashes"] == 1
    assert summary["duplicate_storm_provider_submissions"] == 1
