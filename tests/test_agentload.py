from tallyguard.agentload import AgentLoadConfiguration, execute_agent_load_test


def test_multi_tenant_agent_run_load_preserves_authority_and_idempotency():
    report = execute_agent_load_test(
        AgentLoadConfiguration(
            organizations=4,
            concurrency=8,
            duplicate_execution_storm=20,
            timeout_seconds=10,
        )
    )
    summary = report["summary"]
    assert report["errors"] == []
    assert summary["successful_agent_workflows"] == 4
    assert summary["failed_agent_workflows"] == 0
    assert summary["mixed_queue_items"] == 16
    assert summary["policy_authorized_settlements"] == 4
    assert summary["approval_routes"] == 4
    assert summary["protected_non_settlements"] == 8
    assert summary["verified_proof_packets"] == 4
    assert summary["cross_tenant_attempts"] == 4
    assert summary["cross_tenant_attempts_denied"] == 4
    assert summary["duplicate_execution_requests"] == 20
    assert summary["duplicate_execution_claim_winners"] == 1
    assert summary["duplicate_execution_provider_submissions"] == 1
    assert summary["duplicate_execution_terminal_status"] == "EXECUTED"
    assert summary["duplicate_execution_result_statuses"] == [
        "ROUTED",
        "SETTLED",
        "SKIPPED",
        "SKIPPED",
    ]
    assert summary["duplicate_execution_audit_events"] == [
        "AGENT_RUN_PLANNED",
        "AGENT_RUN_EXECUTED",
    ]
    assert summary["orchestration_single_execution_preserved"] is True
