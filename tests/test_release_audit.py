from hashlib import sha256
import json
from pathlib import Path
import subprocess

from tallyguard.audit import canonical_json
from tallyguard.release_audit import (
    _acceptance_check,
    _check_png_dimensions,
    _check_responsive_broll,
    _check_single_pitch_deck,
    _check_text_fragments,
    _check_tracked_markdown_links,
    _deployment_report_valid,
    _git_commit_check,
    _git_history_secret_check,
    _mainnet_acceptance_check,
    _pilot_check,
    _secret_check,
    _url_check,
)


def test_release_audit_binds_the_current_git_commit():
    check = _git_commit_check(Path(__file__).resolve().parents[1])

    assert check.status == "passed"
    assert len(check.detail) == 40
    assert check.evidence_sha256 == sha256(check.detail.encode("ascii")).hexdigest()


def test_external_url_check_distinguishes_pending_invalid_and_public_urls():
    assert _url_check("Live", None).status == "pending"
    assert _url_check("Live", "http://localhost:8000").status == "failed"
    assert _url_check("Live", "https://demo.example.com").status == "passed"


def test_responsive_capture_check_verifies_png_dimensions(tmp_path):
    capture = tmp_path / "capture.png"
    capture.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0dIHDR"
        + (768).to_bytes(4, "big")
        + (1024).to_bytes(4, "big")
    )

    assert (
        _check_png_dimensions(tmp_path, "capture.png", (768, 1024)).status
        == "passed"
    )
    assert _check_png_dimensions(tmp_path, "capture.png", (390, 844)).status == "failed"


def test_responsive_broll_check_binds_media_and_source_captures(tmp_path):
    assets = tmp_path / "submission" / "assets"
    assets.mkdir(parents=True)
    media = assets / "tallyguard-responsive-broll.webm"
    media.write_bytes(b"\x1aE\xdf\xa3" + b"proof-video")
    sources = {
        "desktop": ("1440x900", "responsive-desktop.png"),
        "tablet": ("768x1024", "responsive-tablet.png"),
        "mobile": ("390x844", "responsive-mobile.png"),
    }
    sequence = []
    for state, (viewport, filename) in sources.items():
        source = assets / filename
        source.write_bytes(f"{state}-capture".encode("ascii"))
        sequence.append(
            {
                "state": state,
                "viewport": viewport,
                "source": f"submission/assets/{filename}",
                "source_sha256": sha256(source.read_bytes()).hexdigest(),
            }
        )
    sequence.append(
        {
            "state": "all",
            "viewport": "composite",
            "sources": [item["source"] for item in sequence],
        }
    )
    manifest = {
        "schema_version": "1.0",
        "classification": "responsive product proof; not final submission video",
        "media": {
            "path": "submission/assets/tallyguard-responsive-broll.webm",
            "container": "webm",
            "width": 1440,
            "height": 900,
            "duration_seconds": 9.24,
            "bytes": media.stat().st_size,
            "sha256": sha256(media.read_bytes()).hexdigest(),
        },
        "sequence": sequence,
        "review": {
            "console_errors": 0,
            "console_warnings": 0,
            "inspected_frame_seconds": [1.4, 3.4, 5.4, 8.2],
        },
    }
    manifest_path = assets / "tallyguard-responsive-broll.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    relative = "submission/assets/tallyguard-responsive-broll.json"
    assert _check_responsive_broll(tmp_path, relative).status == "passed"

    media.write_bytes(media.read_bytes() + b"tampered")
    failed = _check_responsive_broll(tmp_path, relative)
    assert failed.status == "failed"
    assert "metadata" in failed.detail


def test_video_runbook_check_requires_all_three_widths_and_single_product_claim(tmp_path):
    runbook = tmp_path / "runbook.md"
    runbook.write_text(
        "Show 1440px, 768px, and 390px as one responsive web product.",
        encoding="utf-8",
    )

    required = ("1440px", "768px", "390px", "one responsive web product")
    assert _check_text_fragments(tmp_path, "runbook.md", required).status == "passed"

    runbook.write_text("Show 1440px and 390px.", encoding="utf-8")
    failed = _check_text_fragments(tmp_path, "runbook.md", required)
    assert failed.status == "failed"
    assert "768px" in failed.detail


def test_readme_local_link_check_requires_present_tracked_targets(tmp_path):
    _initialize_git_repository(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("guide\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[Guide](docs/guide.md) [External](https://example.com)\n",
        encoding="utf-8",
    )
    _commit_all(tmp_path, "add linked documentation")

    passed = _check_tracked_markdown_links(tmp_path, "README.md")
    assert passed.status == "passed"
    assert "1 local target" in passed.detail

    (tmp_path / "README.md").write_text(
        "[Missing](docs/missing.md)\n",
        encoding="utf-8",
    )
    failed = _check_tracked_markdown_links(tmp_path, "README.md")
    assert failed.status == "failed"
    assert "docs/missing.md" in failed.detail


def test_readme_local_link_check_rejects_untracked_target(tmp_path):
    _initialize_git_repository(tmp_path)
    (tmp_path / "README.md").write_text("root\n", encoding="utf-8")
    _commit_all(tmp_path, "add readme")
    (tmp_path / "preview.png").write_bytes(b"preview")
    (tmp_path / "README.md").write_text("![Preview](preview.png)\n", encoding="utf-8")

    failed = _check_tracked_markdown_links(tmp_path, "README.md")
    assert failed.status == "failed"
    assert "preview.png" in failed.detail


def test_pitch_deck_check_rejects_stale_versions(tmp_path):
    submission = tmp_path / "submission"
    submission.mkdir()
    final = submission / "TallyGuard_Tameion_Pitch_v9.pptx"
    final.write_bytes(b"final-deck")

    assert _check_single_pitch_deck(
        tmp_path, "TallyGuard_Tameion_Pitch_v9.pptx"
    ).status == "passed"

    (submission / "TallyGuard_Tameion_Pitch_v8.pptx").write_bytes(b"stale-deck")
    failed = _check_single_pitch_deck(
        tmp_path, "TallyGuard_Tameion_Pitch_v9.pptx"
    )
    assert failed.status == "failed"
    assert "Pitch_v8.pptx" in failed.detail


def test_deployment_report_validation_requires_safe_session_cleanup():
    required = (
        "judge_console",
        "health_probe",
        "safe_readiness",
        "role_separation",
        "deterministic_decision",
        "simulation_settlement",
        "accounting_export",
        "session_revocation",
    )
    report = {
        "classification": "synthetic deployment acceptance; not customer traction",
        "summary": {"status": "passed", "checks_passed": 8, "checks_failed": 0},
        "safety": {
            "settlement_mode": "simulation",
            "funds_moved": False,
            "mainnet_enabled": False,
            "credentials_required": False,
        },
        "checks": [
            {"name": name, "status": "passed", "detail": "verified"}
            for name in required
        ],
    }

    assert _deployment_report_valid(report) is True
    report["checks"][-1]["status"] = "failed"
    assert _deployment_report_valid(report) is False


def test_secret_scan_covers_tracked_tree_without_echoing_secret(tmp_path):
    _initialize_git_repository(tmp_path)
    secret = "gh" + "p_" + "A" * 36
    (tmp_path / "credentials.txt").write_text(
        f"release_token={secret}\n",
        encoding="utf-8",
    )
    _commit_all(tmp_path, "add credential")

    check = _secret_check(tmp_path)

    assert check.status == "failed"
    assert "credentials.txt" in check.detail
    assert secret not in check.detail


def test_secret_scan_allows_empty_example_but_rejects_tracked_env(tmp_path):
    _initialize_git_repository(tmp_path)
    placeholder = "CIRCLE_WEB3_" + "API_KEY=\n"
    (tmp_path / ".env.example").write_text(placeholder, encoding="utf-8")
    _commit_all(tmp_path, "add safe environment template")
    assert _secret_check(tmp_path).status == "passed"

    (tmp_path / ".env").write_text(placeholder, encoding="utf-8")
    _commit_all(tmp_path, "track unsafe environment file")
    failed = _secret_check(tmp_path)
    assert failed.status == "failed"
    assert ".env" in failed.detail


def test_history_scan_detects_removed_secret_without_echoing_it(tmp_path):
    _initialize_git_repository(tmp_path)
    secret = "gh" + "p_" + "B" * 36
    leaked = tmp_path / "temporary.txt"
    leaked.write_text(f"token={secret}\n", encoding="utf-8")
    _commit_all(tmp_path, "add temporary credential")
    leaked.unlink()
    _commit_all(tmp_path, "remove temporary credential")

    check = _git_history_secret_check(tmp_path)

    assert check.status == "failed"
    assert "temporary.txt" in check.detail
    assert secret not in check.detail


def _initialize_git_repository(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "TallyGuard Tests"],
        cwd=path,
        check=True,
    )


def _commit_all(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=path,
        check=True,
        capture_output=True,
    )


def test_acceptance_check_requires_capped_arc_testnet_proof(tmp_path):
    artifact = tmp_path / "acceptance.json"
    artifact.write_text(
        json.dumps(
            {
                "network": "ARC-TESTNET",
                "amount_usdc": "0.01",
                "decision_action": "PAY",
                "audit_chain_valid": True,
                "receipt": {
                    "transaction_hash": "0x" + "a" * 64,
                    "explorer_url": "https://explorer.testnet.arc.io/tx/0x" + "a" * 64,
                },
                "idempotent_replay": {"same_receipt": True},
            }
        ),
        encoding="utf-8",
    )

    assert _acceptance_check(str(artifact)).status == "passed"

    report = json.loads(artifact.read_text(encoding="utf-8"))
    report["amount_usdc"] = "0.11"
    artifact.write_text(json.dumps(report), encoding="utf-8")
    assert _acceptance_check(str(artifact)).status == "failed"


def test_mainnet_acceptance_check_rejects_synthetic_and_validates_approval_binding(
    tmp_path,
):
    artifact = tmp_path / "mainnet-acceptance.json"
    approval_id = "approval_123"
    report = {
        "classification": "controlled-live-mainnet-proof",
        "network": "ARC-MAINNET",
        "amount_usdc": "0.01",
        "decision_action": "PAY",
        "audit_chain_valid": True,
        "approval": {
            "id": approval_id,
            "status": "APPROVED",
            "requested_by_user_id": "operator",
            "resolved_by_user_id": "approver",
        },
        "intent": {
            "approval_reference": approval_id,
            "network": "ARC-MAINNET",
            "amount_usdc": "0.01",
        },
        "receipt": {
            "provider": "circle-developer-wallets+arc-rpc",
            "network": "ARC-MAINNET",
            "status": "CONFIRMED",
            "transaction_hash": "0x" + "b" * 64,
            "explorer_url": "https://explorer.arc.io/tx/0x" + "b" * 64,
        },
        "idempotent_replay": {
            "same_receipt": True,
            "second_request_reused_receipt": True,
        },
    }
    artifact.write_text(json.dumps(report), encoding="utf-8")

    assert _mainnet_acceptance_check(str(artifact)).status == "passed"

    report["classification"] = "synthetic-mainnet-path-test"
    artifact.write_text(json.dumps(report), encoding="utf-8")
    assert _mainnet_acceptance_check(str(artifact)).status == "failed"

    report["classification"] = "controlled-live-mainnet-proof"
    report["intent"]["approval_reference"] = "approval_other"
    artifact.write_text(json.dumps(report), encoding="utf-8")
    assert _mainnet_acceptance_check(str(artifact)).status == "failed"


def test_pilot_check_verifies_content_address_and_claim_boundaries(tmp_path):
    report = {
        "usage_measurement": {"source": "operator attestation"},
        "product_evidence": {"packet_verified": True},
        "attestation": {"reporting_consent": True},
    }
    envelope = {
        "report": report,
        "report_sha256": sha256(canonical_json(report).encode("utf-8")).hexdigest(),
    }
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    assert _pilot_check(str(path)).status == "passed"

    envelope["report"]["product_evidence"]["packet_verified"] = False
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert _pilot_check(str(path)).status == "failed"
