"""Static deployment contracts that do not require Render credentials."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_daily_pipeline_is_declared_as_one_shot_render_cron():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "type: cron" in blueprint
    assert "name: portfolio-pilot-daily-pipeline" in blueprint
    assert 'schedule: "0 22 * * 1-5"' in blueprint
    assert "dockerCommand: python -m app.workers.run_daily_pipeline" in blueprint
    assert "type: worker\n" not in blueprint


def test_render_blueprint_does_not_contain_object_storage_credentials():
    blueprint = (ROOT / "render.yaml").read_text(encoding="utf-8")

    for secret_name in (
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "QWEN_API_KEY",
        "DATABASE_URL",
        "TUSHARE_TOKEN",
    ):
        if f"key: {secret_name}" in blueprint:
            section = blueprint.split(f"key: {secret_name}", maxsplit=1)[1].split(
                "- key:", maxsplit=1
            )[0]
            assert "sync: false" in section
