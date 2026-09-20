"""Generate a fail-closed release audit for the TallyGuard submission package."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import subprocess
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from .audit import canonical_json


@dataclass(frozen=True, slots=True)
class ReleaseCheck:
    category: str
    name: str
    status: str
    detail: str
    evidence_sha256: str | None = None


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _check_file(root: Path, relative_path: str) -> ReleaseCheck:
    path = root / relative_path
    if not path.is_file() or path.stat().st_size == 0:
        return ReleaseCheck("local", relative_path, "failed", "Required file is missing or empty.")
    return ReleaseCheck(
        "local",
        relative_path,
        "passed",
        f"Present ({path.stat().st_size} bytes).",
        _hash_file(path),
    )


def _check_png_dimensions(
    root: Path,
    relative_path: str,
    expected_dimensions: tuple[int, int],
) -> ReleaseCheck:
    path = root / relative_path
    try:
        payload = path.read_bytes()
        if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG file")
        dimensions = struct.unpack(">II", payload[16:24])
    except (OSError, ValueError, struct.error) as exc:
        return ReleaseCheck(
            "local",
            relative_path,
            "failed",
            f"Cannot verify responsive capture: {exc}",
        )
    if dimensions != expected_dimensions:
        expected = f"{expected_dimensions[0]}x{expected_dimensions[1]}"
        actual = f"{dimensions[0]}x{dimensions[1]}"
        return ReleaseCheck(
            "local",
            relative_path,
            "failed",
            f"Expected {expected} PNG; found {actual}.",
        )
    return ReleaseCheck(
        "local",
        relative_path,
        "passed",
        f"Verified responsive capture at {dimensions[0]}x{dimensions[1]}.",
        _hash_file(path),
    )


def _check_responsive_broll(
    root: Path,
    manifest_relative_path: str,
) -> ReleaseCheck:
    manifest_path = root / manifest_relative_path
    try:
        manifest = _load_json(manifest_path)
        media = manifest.get("media")
        sequence = manifest.get("sequence")
        review = manifest.get("review")
        if not isinstance(media, dict):
            raise ValueError("media must be an object")
        media_relative = str(media.get("path", ""))
        media_path = root / media_relative
        payload = media_path.read_bytes()
        duration = Decimal(str(media.get("duration_seconds")))
        if payload[:4] != b"\x1aE\xdf\xa3":
            raise ValueError("media is not an EBML/WebM file")
        if not (
            manifest.get("classification")
            == "responsive product proof; not final submission video"
            and media_relative
            == "submission/assets/tallyguard-responsive-broll.webm"
            and media.get("container") == "webm"
            and media.get("width") == 1440
            and media.get("height") == 900
            and Decimal("8") <= duration <= Decimal("10")
            and media.get("bytes") == len(payload)
            and media.get("sha256") == sha256(payload).hexdigest()
            and isinstance(sequence, list)
            and [item.get("state") for item in sequence if isinstance(item, dict)]
            == ["desktop", "tablet", "mobile", "all"]
            and isinstance(review, dict)
            and review.get("console_errors") == 0
            and review.get("console_warnings") == 0
        ):
            raise ValueError("media metadata or review assertions do not match")

        expected_sources = (
            (
                "desktop",
                "1440x900",
                "submission/assets/responsive-desktop.png",
            ),
            (
                "tablet",
                "768x1024",
                "submission/assets/responsive-tablet.png",
            ),
            (
                "mobile",
                "390x844",
                "submission/assets/responsive-mobile.png",
            ),
        )
        for item, (state, viewport, source_relative) in zip(
            sequence[:3], expected_sources, strict=True
        ):
            if not isinstance(item, dict):
                raise ValueError(f"{state} sequence item is not an object")
            source_path = root / source_relative
            if not (
                item.get("state") == state
                and item.get("viewport") == viewport
                and item.get("source") == source_relative
                and item.get("source_sha256") == _hash_file(source_path)
            ):
                raise ValueError(f"{state} source capture is not hash-bound")
        frames = review.get("inspected_frame_seconds")
        if not (
            isinstance(frames, list)
            and len(frames) == 4
            and all(Decimal("0") < Decimal(str(frame)) < duration for frame in frames)
        ):
            raise ValueError("four inspected frame timestamps are required")
    except (
        OSError,
        json.JSONDecodeError,
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        return ReleaseCheck(
            "local",
            manifest_relative_path,
            "failed",
            f"Cannot verify responsive product proof: {exc}",
        )
    return ReleaseCheck(
        "local",
        manifest_relative_path,
        "passed",
        f"Verified {duration}-second 1440x900 responsive proof and three source hashes.",
        _hash_file(media_path),
    )


def _check_text_fragments(
    root: Path,
    relative_path: str,
    required_fragments: tuple[str, ...],
) -> ReleaseCheck:
    path = root / relative_path
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ReleaseCheck(
            "local", relative_path, "failed", f"Cannot read file: {exc}"
        )
    missing = [fragment for fragment in required_fragments if fragment not in content]
    if missing:
        return ReleaseCheck(
            "local",
            relative_path,
            "failed",
            "Missing required release language: " + ", ".join(missing),
        )
    return ReleaseCheck(
        "local",
        relative_path,
        "passed",
        "Required release language is present.",
        _hash_file(path),
    )


_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


def _check_tracked_markdown_links(root: Path, relative_path: str) -> ReleaseCheck:
    path = root / relative_path
    if not path.is_file():
        return ReleaseCheck(
            "repository",
            f"{relative_path} local links",
            "failed",
            "Markdown source is missing.",
        )
    failures: set[str] = set()
    checked: set[str] = set()
    for raw_target in _MARKDOWN_LINK.findall(
        path.read_text(encoding="utf-8", errors="replace")
    ):
        target = raw_target.strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1].strip()
        if not target or target.startswith("#"):
            continue
        parsed = urlparse(target)
        if parsed.scheme or parsed.netloc:
            continue
        local_target = unquote(parsed.path)
        if not local_target:
            continue
        candidate = (path.parent / local_target).resolve()
        try:
            repository_relative = candidate.relative_to(root.resolve()).as_posix()
        except ValueError:
            failures.add(local_target)
            continue
        checked.add(repository_relative)
        if not candidate.is_file():
            failures.add(repository_relative)
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", repository_relative],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if tracked.returncode != 0:
            failures.add(repository_relative)
    if failures:
        return ReleaseCheck(
            "repository",
            f"{relative_path} local links",
            "failed",
            "Missing, outside-repository, or untracked target(s): "
            + ", ".join(sorted(failures)),
        )
    return ReleaseCheck(
        "repository",
        f"{relative_path} local links",
        "passed",
        f"Verified {len(checked)} local target(s) are present and tracked.",
        _hash_file(path),
    )


def _check_single_pitch_deck(root: Path, expected_name: str) -> ReleaseCheck:
    submission = root / "submission"
    decks = sorted(path.name for path in submission.glob("TallyGuard_Tameion_Pitch_v*.pptx"))
    if decks != [expected_name]:
        found = ", ".join(decks) if decks else "none"
        return ReleaseCheck(
            "local",
            "Final pitch deck selection",
            "failed",
            f"Expected only {expected_name}; found {found}.",
        )
    return ReleaseCheck(
        "local",
        "Final pitch deck selection",
        "passed",
        f"Only {expected_name} is present for reviewer use.",
        _hash_file(submission / expected_name),
    )


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Top-level JSON value must be an object.")
    return value


def _check_report(
    root: Path,
    relative_path: str,
    validator: Callable[[dict[str, Any]], bool],
    success_detail: str,
) -> ReleaseCheck:
    path = root / relative_path
    try:
        report = _load_json(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return ReleaseCheck("local", relative_path, "failed", f"Cannot read report: {exc}")
    if not validator(report):
        return ReleaseCheck("local", relative_path, "failed", "Report assertions did not pass.")
    return ReleaseCheck("local", relative_path, "passed", success_detail, _hash_file(path))


def _workflow_report_valid(report: dict[str, Any]) -> bool:
    summary = report.get("summary")
    return bool(
        isinstance(summary, dict)
        and summary.get("successful_workflows") == 10_000
        and summary.get("failed_workflows") == 0
        and summary.get("duplicate_payment_count") == 0
        and summary.get("cross_tenant_attempts_denied")
        == summary.get("cross_tenant_attempts")
        and summary.get("treasury_atomic_limit_preserved") is True
    )


def _agent_report_valid(report: dict[str, Any]) -> bool:
    summary = report.get("summary")
    return bool(
        isinstance(summary, dict)
        and summary.get("successful_agent_workflows") == 50
        and summary.get("failed_agent_workflows") == 0
        and summary.get("verified_proof_packets") == 50
        and summary.get("cross_tenant_attempts_denied")
        == summary.get("cross_tenant_attempts")
        and summary.get("orchestration_single_execution_preserved") is True
    )


def _deployment_report_valid(report: dict[str, Any]) -> bool:
    summary = report.get("summary")
    safety = report.get("safety")
    checks = report.get("checks")
    check_names = {
        str(check.get("name"))
        for check in checks
        if isinstance(check, dict) and check.get("status") == "passed"
    } if isinstance(checks, list) else set()
    required_checks = {
        "judge_console",
        "health_probe",
        "safe_readiness",
        "role_separation",
        "deterministic_decision",
        "simulation_settlement",
        "accounting_export",
        "session_revocation",
    }
    return bool(
        report.get("classification")
        == "synthetic deployment acceptance; not customer traction"
        and isinstance(summary, dict)
        and summary.get("status") == "passed"
        and summary.get("checks_passed") == 8
        and summary.get("checks_failed") == 0
        and isinstance(safety, dict)
        and safety.get("settlement_mode") == "simulation"
        and safety.get("funds_moved") is False
        and safety.get("mainnet_enabled") is False
        and safety.get("credentials_required") is False
        and required_checks == check_names
    )


def _url_check(name: str, value: str | None, *, prefix: str | None = None) -> ReleaseCheck:
    if value is None or not value.strip():
        return ReleaseCheck("external", name, "pending", "External evidence has not been supplied.")
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc or (prefix and not candidate.startswith(prefix)):
        expectation = f" beginning with {prefix}" if prefix else ""
        return ReleaseCheck(
            "external",
            name,
            "failed",
            f"Expected a public HTTPS URL{expectation}.",
        )
    return ReleaseCheck("external", name, "passed", candidate)


def _acceptance_check(path_value: str | None) -> ReleaseCheck:
    if not path_value:
        return ReleaseCheck(
            "external",
            "Arc Testnet acceptance artifact",
            "pending",
            "No real Testnet acceptance artifact supplied.",
        )
    path = Path(path_value)
    try:
        report = _load_json(path)
        amount = Decimal(str(report.get("amount_usdc")))
        receipt = report.get("receipt")
        valid = bool(
            report.get("network") == "ARC-TESTNET"
            and Decimal("0") < amount <= Decimal("0.10")
            and report.get("decision_action") == "PAY"
            and report.get("audit_chain_valid") is True
            and isinstance(receipt, dict)
            and re.fullmatch(r"0x[0-9a-fA-F]{64}", str(receipt.get("transaction_hash", "")))
            and str(receipt.get("explorer_url", "")).startswith(
                "https://explorer.testnet.arc.io/"
            )
            and report.get("idempotent_replay", {}).get("same_receipt") is True
        )
    except (OSError, json.JSONDecodeError, ValueError, InvalidOperation) as exc:
        return ReleaseCheck(
            "external", "Arc Testnet acceptance artifact", "failed", f"Cannot verify: {exc}"
        )
    if not valid:
        return ReleaseCheck(
            "external",
            "Arc Testnet acceptance artifact",
            "failed",
            "Artifact does not prove a capped, reconciled, idempotent Arc Testnet payment.",
        )
    return ReleaseCheck(
        "external",
        "Arc Testnet acceptance artifact",
        "passed",
        "Capped real Testnet acceptance evidence is structurally complete.",
        _hash_file(path),
    )


def _mainnet_acceptance_check(path_value: str) -> ReleaseCheck:
    path = Path(path_value)
    try:
        report = _load_json(path)
        amount = Decimal(str(report.get("amount_usdc")))
        approval = report.get("approval")
        intent = report.get("intent")
        receipt = report.get("receipt")
        requester = str(approval.get("requested_by_user_id", "")) if isinstance(approval, dict) else ""
        resolver = str(approval.get("resolved_by_user_id", "")) if isinstance(approval, dict) else ""
        valid = bool(
            report.get("classification") == "controlled-live-mainnet-proof"
            and report.get("network") == "ARC-MAINNET"
            and Decimal("0") < amount <= Decimal("0.01")
            and report.get("decision_action") == "PAY"
            and report.get("audit_chain_valid") is True
            and isinstance(approval, dict)
            and approval.get("status") == "APPROVED"
            and requester
            and resolver
            and requester != resolver
            and isinstance(intent, dict)
            and intent.get("approval_reference") == approval.get("id")
            and intent.get("network") == "ARC-MAINNET"
            and Decimal(str(intent.get("amount_usdc"))) == amount
            and isinstance(receipt, dict)
            and receipt.get("provider") == "circle-developer-wallets+arc-rpc"
            and receipt.get("network") == "ARC-MAINNET"
            and receipt.get("status") == "CONFIRMED"
            and re.fullmatch(r"0x[0-9a-fA-F]{64}", str(receipt.get("transaction_hash", "")))
            and str(receipt.get("explorer_url", "")).startswith(
                "https://explorer.arc.io/"
            )
            and report.get("idempotent_replay", {}).get("same_receipt") is True
            and report.get("idempotent_replay", {}).get(
                "second_request_reused_receipt"
            )
            is True
        )
    except (OSError, json.JSONDecodeError, ValueError, InvalidOperation) as exc:
        return ReleaseCheck(
            "external", "Controlled Arc Mainnet proof", "failed", f"Cannot verify: {exc}"
        )
    if not valid:
        return ReleaseCheck(
            "external",
            "Controlled Arc Mainnet proof",
            "failed",
            "Artifact does not prove a capped, role-separated, Circle-backed, idempotent Arc Mainnet payment.",
        )
    return ReleaseCheck(
        "external",
        "Controlled Arc Mainnet proof",
        "passed",
        "Optional 0.01-USDC Mainnet proof is structurally complete and Circle/Arc bound.",
        _hash_file(path),
    )


def _pilot_check(path_value: str | None) -> ReleaseCheck:
    if not path_value:
        return ReleaseCheck(
            "external",
            "Genuine pilot report",
            "pending",
            "No operator-attested pilot report supplied.",
        )
    path = Path(path_value)
    try:
        envelope = _load_json(path)
        report = envelope.get("report")
        expected_hash = envelope.get("report_sha256")
        valid = bool(
            isinstance(report, dict)
            and expected_hash == sha256(canonical_json(report).encode("utf-8")).hexdigest()
            and report.get("usage_measurement", {}).get("source") == "operator attestation"
            and report.get("product_evidence", {}).get("packet_verified") is True
            and report.get("attestation", {}).get("reporting_consent") is True
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return ReleaseCheck("external", "Genuine pilot report", "failed", f"Cannot verify: {exc}")
    if not valid:
        return ReleaseCheck(
            "external",
            "Genuine pilot report",
            "failed",
            "Report hash, packet verification, operator source, or reporting consent is invalid.",
        )
    return ReleaseCheck(
        "external",
        "Genuine pilot report",
        "passed",
        "Content address, product proof, and operator attestation are complete.",
        _hash_file(path),
    )


def _git_check(root: Path) -> ReleaseCheck:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ReleaseCheck("repository", "Clean worktree", "failed", result.stderr.strip())
    if result.stdout.strip():
        return ReleaseCheck("repository", "Clean worktree", "failed", "Uncommitted files remain.")
    return ReleaseCheck("repository", "Clean worktree", "passed", "No uncommitted files.")


def _git_commit_check(root: Path) -> ReleaseCheck:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        return ReleaseCheck(
            "repository",
            "Release commit",
            "failed",
            result.stderr.strip() or "HEAD is not a full Git commit.",
        )
    return ReleaseCheck(
        "repository",
        "Release commit",
        "passed",
        commit,
        sha256(commit.encode("ascii")).hexdigest(),
    )


_TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".csv",
        ".example",
        ".html",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".scss",
        ".svg",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_TEXT_FILENAMES = frozenset({".dockerignore", ".gitattributes", ".gitignore"})
_SENSITIVE_FILENAMES = frozenset(
    {".env", "id_rsa", "id_ed25519", "credentials.json"}
)


def _secret_patterns() -> tuple[re.Pattern[str], ...]:
    return (
        re.compile("gh" + r"[pousr]_[A-Za-z0-9]{20,}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(
            r"(?:api[_-]?key|auth[_-]?token|entity[_-]?secret|private[_-]?key|"
            r"mnemonic|password|seed[_-]?phrase)[ \t]*[:=][ \t]*['\"]?"
            r"(?:0x)?[A-Za-z0-9+/=_-]{20,}",
            re.IGNORECASE,
        ),
    )


def _sensitive_path(relative: str) -> bool:
    path = Path(relative)
    lower_name = path.name.lower()
    return bool(
        lower_name in _SENSITIVE_FILENAMES
        or lower_name.endswith((".key", ".keystore", ".p12", ".pem"))
    )


def _secret_check(root: Path) -> ReleaseCheck:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if listed.returncode != 0:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        return ReleaseCheck("repository", "Tracked-tree secret scan", "failed", detail)
    patterns = _secret_patterns()
    matches: list[str] = []
    for raw_relative in listed.stdout.split(b"\0"):
        if not raw_relative:
            continue
        relative = raw_relative.decode("utf-8", errors="replace")
        path = root / relative
        if _sensitive_path(relative):
            matches.append(relative)
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in _TEXT_FILENAMES:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        if any(pattern.search(content) for pattern in patterns):
            matches.append(relative)
    if matches:
        return ReleaseCheck(
            "repository",
            "Tracked-tree secret scan",
            "failed",
            "Potential secret or sensitive file in: " + ", ".join(sorted(set(matches))),
        )
    return ReleaseCheck(
        "repository",
        "Tracked-tree secret scan",
        "passed",
        "No credential pattern or sensitive filename found in tracked files.",
    )


def _git_history_secret_check(root: Path) -> ReleaseCheck:
    history = subprocess.run(
        [
            "git",
            "log",
            "--all",
            "--format=commit:%H",
            "--patch",
            "--no-color",
            "--no-ext-diff",
            "--no-renames",
            "--",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if history.returncode != 0:
        return ReleaseCheck(
            "repository",
            "Git history secret scan",
            "failed",
            history.stderr.strip(),
        )
    patterns = _secret_patterns()
    commit = "unknown"
    relative = "unknown"
    findings: set[str] = set()
    for line in history.stdout.splitlines():
        if line.startswith("commit:"):
            commit = line.removeprefix("commit:").strip()[:12]
        elif line.startswith("--- a/"):
            relative = line.removeprefix("--- a/").strip()
        elif line.startswith("+++ b/"):
            relative = line.removeprefix("+++ b/").strip()
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            if _sensitive_path(relative) or any(
                pattern.search(line[1:]) for pattern in patterns
            ):
                findings.add(f"{commit}:{relative}")
    if findings:
        return ReleaseCheck(
            "repository",
            "Git history secret scan",
            "failed",
            "Potential historical secret in: " + ", ".join(sorted(findings)),
        )
    return ReleaseCheck(
        "repository",
        "Git history secret scan",
        "passed",
        "No credential pattern or sensitive filename found in reachable history.",
    )


def audit_release(
    root: Path,
    *,
    repository_url: str | None = None,
    live_url: str | None = None,
    video_url: str | None = None,
    testnet_explorer_url: str | None = None,
    acceptance_artifact: str | None = None,
    mainnet_acceptance_artifact: str | None = None,
    pilot_report: str | None = None,
    include_git: bool = True,
) -> dict[str, Any]:
    checks = [
        _check_file(root, "README.md"),
        _check_tracked_markdown_links(root, "README.md"),
        _check_file(root, "Dockerfile"),
        _check_file(root, "render.yaml"),
        _check_file(root, "docs/ARCHITECTURE.md"),
        _check_file(root, "docs/SECURITY_MODEL.md"),
        _check_file(root, "web/dist/index.html"),
        _check_file(root, "submission/TallyGuard_Tameion_Pitch_v9.pptx"),
        _check_single_pitch_deck(root, "TallyGuard_Tameion_Pitch_v9.pptx"),
        _check_png_dimensions(
            root,
            "submission/assets/responsive-desktop.png",
            (1440, 900),
        ),
        _check_png_dimensions(
            root,
            "submission/assets/responsive-tablet.png",
            (768, 1024),
        ),
        _check_png_dimensions(
            root,
            "submission/assets/responsive-mobile.png",
            (390, 844),
        ),
        _check_responsive_broll(
            root,
            "submission/assets/tallyguard-responsive-broll.json",
        ),
        _check_text_fragments(
            root,
            "submission/DEMO_RUNBOOK.md",
            ("1440px", "768px", "390px", "one responsive web product"),
        ),
        _check_text_fragments(
            root,
            "README.md",
            (
                "One responsive judge product",
                "responsive-desktop.png",
                "responsive-tablet.png",
                "responsive-mobile.png",
            ),
        ),
        _check_report(
            root,
            "docs/reports/deployment-smoke.json",
            _deployment_report_valid,
            "Eight public-safe HTTP checks, including session revocation, verified.",
        ),
        _check_report(
            root,
            "docs/reports/load-test-10000.json",
            _workflow_report_valid,
            "10,000 workflows, isolation, idempotency, and treasury contention verified.",
        ),
        _check_report(
            root,
            "docs/reports/agent-run-load-50.json",
            _agent_report_valid,
            "50 tenant agent runs and proof packets verified.",
        ),
    ]
    if include_git:
        checks.extend(
            (
                _git_commit_check(root),
                _git_check(root),
                _secret_check(root),
                _git_history_secret_check(root),
            )
        )
    submission_copy = root / "submission/FINAL_SUBMISSION_COPY.md"
    pending_placeholders = (
        submission_copy.read_text(encoding="utf-8").count("PENDING_EXTERNAL")
        if submission_copy.is_file()
        else 0
    )
    checks.append(
        ReleaseCheck(
            "external",
            "Final submission placeholders",
            "pending" if pending_placeholders else "passed",
            f"{pending_placeholders} PENDING_EXTERNAL markers remain.",
        )
    )
    checks.extend(
        (
            _url_check(
                "Public repository URL",
                repository_url,
                prefix="https://github.com/",
            ),
            _url_check("Live judge console URL", live_url),
            _url_check("Demo video URL", video_url),
            _url_check(
                "Arc Testnet Explorer URL",
                testnet_explorer_url,
                prefix="https://explorer.testnet.arc.io/",
            ),
            _acceptance_check(acceptance_artifact),
            _pilot_check(pilot_report),
        )
    )
    if mainnet_acceptance_artifact is not None:
        checks.append(_mainnet_acceptance_check(mainnet_acceptance_artifact))
    counts = {
        status: sum(check.status == status for check in checks)
        for status in ("passed", "pending", "failed")
    }
    status = "failed" if counts["failed"] else "ready" if not counts["pending"] else "needs-external-evidence"
    payload = {
        "schema_version": "1.0",
        "status": status,
        "summary": counts,
        "checks": [asdict(check) for check in checks],
    }
    payload["audit_sha256"] = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit TallyGuard release and submission evidence")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--repository-url")
    parser.add_argument("--live-url")
    parser.add_argument("--video-url")
    parser.add_argument("--testnet-explorer-url")
    parser.add_argument("--acceptance-artifact")
    parser.add_argument("--mainnet-acceptance-artifact")
    parser.add_argument("--pilot-report")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    report = audit_release(
        args.root.resolve(),
        repository_url=args.repository_url,
        live_url=args.live_url,
        video_url=args.video_url,
        testnet_explorer_url=args.testnet_explorer_url,
        acceptance_artifact=args.acceptance_artifact,
        mainnet_acceptance_artifact=args.mainnet_acceptance_artifact,
        pilot_report=args.pilot_report,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if report["status"] == "failed" or (args.require_complete and report["status"] != "ready"):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
