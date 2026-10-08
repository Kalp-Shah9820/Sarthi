from fastapi.testclient import TestClient
from typer.testing import CliRunner

from sarthi import validate
from sarthi.api.main import create_app
from sarthi.cli import app as cli_app
from sarthi.config import BACKEND_ROOT, Settings, get_settings


def test_health_endpoint():
    with TestClient(create_app()) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "llm": "unknown"}


def test_cors_allows_vite_dev_origin():
    with TestClient(create_app()) as client:
        r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_settings_defaults_and_paths():
    s = Settings(_env_file=None)
    assert s.llm_base_url == "http://localhost:1234/v1"
    assert s.llm_reasoning_headroom == 0
    assert s.db_file == BACKEND_ROOT / "data" / "sarthi.db"
    assert s.cors_list == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_settings_read_env_prefix(monkeypatch):
    monkeypatch.setenv("SARTHI_MC_PATHS", "500")
    monkeypatch.setenv("SARTHI_LLM_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.mc_paths == 500
    assert s.llm_enabled is False


def test_env_example_matches_settings_fields():
    """Every key in .env.example must be a real setting, so the example never drifts from the code."""
    keys = [
        line.split("=", 1)[0].removeprefix("SARTHI_").lower()
        for line in (BACKEND_ROOT / ".env.example").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert keys and set(keys) <= set(Settings.model_fields)


def test_cli_version():
    result = CliRunner().invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert "sarthi 0.1.0" in result.stdout


def test_validator_core_checks_pass():
    assert validate.check_dependencies().status == validate.PASS
    assert validate.check_imports().status == validate.PASS
    assert validate.check_api().status == validate.PASS
    assert validate.check_config().status in (validate.PASS, validate.WARN)


def test_validator_llm_check_never_fails_when_server_is_down(monkeypatch):
    monkeypatch.setenv("SARTHI_LLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens on port 9
    get_settings.cache_clear()
    try:
        assert validate.check_llm().status == validate.WARN
    finally:
        get_settings.cache_clear()


def test_validator_reports_a_crashing_check_as_failure(monkeypatch):
    def check_boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(validate, "CHECKS", [check_boom])
    results = validate.run()
    assert [(r.name, r.status) for r in results] == [("boom", validate.FAIL)]
    assert validate.report(results) is False
