import pytest

from tallyguard.api import create_app
from tallyguard.deployment_smoke import (
    DeploymentSmokeError,
    _normalize_remote_base_url,
    run_deployment_smoke,
)


def test_real_http_deployment_smoke_covers_public_safe_flow(tmp_path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    monkeypatch.setenv("TALLYGUARD_FRONTEND_DIST", str(frontend))
    monkeypatch.setenv("TALLYGUARD_MODE", "simulation")
    monkeypatch.delenv("TALLYGUARD_ALLOW_MAINNET", raising=False)
    app = create_app(database_path=tmp_path / "deployment-smoke.sqlite3")

    report = run_deployment_smoke(app)

    assert report["summary"]["status"] == "passed"
    assert report["summary"]["checks_passed"] == 8
    assert report["safety"] == {
        "settlement_mode": "simulation",
        "funds_moved": False,
        "mainnet_enabled": False,
        "credentials_required": False,
    }
    assert [check["name"] for check in report["checks"]] == [
        "judge_console",
        "health_probe",
        "safe_readiness",
        "role_separation",
        "deterministic_decision",
        "simulation_settlement",
        "accounting_export",
        "session_revocation",
    ]
    app.extensions["tallyguard_repository"].close()


def test_remote_smoke_accepts_only_a_bare_public_https_origin():
    assert (
        _normalize_remote_base_url(" https://tallyguard.example.com/ ")
        == "https://tallyguard.example.com"
    )

    for invalid in (
        "http://tallyguard.example.com",
        "https://user:secret@tallyguard.example.com",
        "https://tallyguard.example.com/app",
        "https://tallyguard.example.com?token=secret",
        "https://tallyguard.example.com/#private",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.8",
    ):
        with pytest.raises(DeploymentSmokeError):
            _normalize_remote_base_url(invalid)


def test_deployment_smoke_requires_exactly_one_target(tmp_path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    monkeypatch.setenv("TALLYGUARD_FRONTEND_DIST", str(frontend))
    app = create_app(database_path=tmp_path / "exclusive-target.sqlite3")

    with pytest.raises(DeploymentSmokeError):
        run_deployment_smoke()
    with pytest.raises(DeploymentSmokeError):
        run_deployment_smoke(app, base_url="https://tallyguard.example.com")

    app.extensions["tallyguard_repository"].close()
