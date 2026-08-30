"""不启动 Docker 也能守住的一键交付契约。真机验收仍由 Docker 执行。"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
INIT = ROOT / "deploy" / "docker" / "init.sh"


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_database_and_upload_data_use_named_volumes():
    data = compose()
    assert "pgdata:/var/lib/postgresql/data" in data["services"]["db"]["volumes"]
    for service in ("init", "api", "worker"):
        assert "uploads:/app/data" in data["services"][service]["volumes"]


def test_api_has_a_real_healthcheck_and_web_waits_for_it():
    services = compose()["services"]
    check = " ".join(services["api"]["healthcheck"]["test"])
    assert "/api/health" in check
    assert services["web"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_init_waits_for_the_database_and_blocks_the_application_on_failure():
    services = compose()["services"]
    assert services["init"]["depends_on"]["db"]["condition"] == "service_healthy"
    for service in ("api", "worker"):
        assert services[service]["depends_on"]["init"]["condition"] == (
            "service_completed_successfully"
        )


def test_missing_or_placeholder_model_keys_fail_with_clear_messages():
    script = INIT.read_text(encoding="utf-8")
    for key in ("SILICONFLOW_API_KEY", "LLM_API_KEY"):
        assert key in script
        assert f"配置错误：{key} 未填写" in script
    assert "xxxxxxxx" in script


def test_sample_ingest_and_demo_account_are_part_of_initialization():
    script = INIT.read_text(encoding="utf-8")
    assert "copilot ingest /app/samples" in script
    assert "copilot seed-user" in script
    assert "样例语料入库完成" in script
