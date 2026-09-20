from tallyguard.api import create_app
from tallyguard.deployment_smoke import run_deployment_smoke


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
    assert report["summary"]["checks_passed"] == 7
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
    ]
    app.extensions["tallyguard_repository"].close()
