"""Configuration regressions independent of the loader's own test fixtures."""
import json
from pathlib import Path

import pytest
import yaml

from packages.config.errors import ContractError
from packages.config.loader import load_bundle, profile_status, resolve_environment
from packages.config.yaml import parse_yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_file(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge/labels.yaml").write_text((ROOT / "tests/fixtures/v30/knowledge/labels.yaml").read_text())
    path = tmp_path / "config/system.yaml"
    path.write_text((ROOT / "tests/fixtures/v30/config/system.yaml").read_text())
    return path


def test_snapshot_paths_and_hash_do_not_depend_on_callers_working_directory(config_file, monkeypatch, tmp_path):
    original = load_bundle(config_file)
    nested = tmp_path / "unrelated/deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    again = load_bundle(config_file)
    assert again.root == original.root == config_file.parent.parent
    assert again.config_hash == original.config_hash
    assert again.paths == original.paths
    assert again.paths["inbox"] == config_file.parent.parent / "data_demo/inbox"


def test_disabled_components_do_not_require_credentials(config_file, monkeypatch):
    monkeypatch.delenv("JIANWEI_TEACHER_KEY", raising=False)
    monkeypatch.delenv("JIANWEI_JWT_SECRET", raising=False)
    values = resolve_environment(load_bundle(config_file, "demo"))
    assert values["teacher_enabled"] is False
    assert values["auth_enabled"] is False


def test_resolved_secrets_do_not_reach_public_snapshot_hash_or_status(config_file, monkeypatch):
    secrets = {
        "JIANWEI_DATABASE_URL": "postgresql://review:fake-review-password@localhost/review",
        "JIANWEI_TEACHER_KEY": "fake-review-teacher-secret",
        "JIANWEI_JWT_SECRET": "fake-review-jwt-secret",
        "JIANWEI_CAMERA_01_RTSP": "rtsp://review:fake-camera-secret@localhost/camera",
    }
    before = load_bundle(config_file, "site")
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    after = load_bundle(config_file, "site")
    private_values = resolve_environment(after)
    assert private_values["sources"][0]["uri"] == secrets["JIANWEI_CAMERA_01_RTSP"]
    assert private_values["database_url"] == secrets["JIANWEI_DATABASE_URL"]
    assert before.config_hash == after.config_hash
    public_data = json.dumps({"snapshot": after.snapshot, "status": profile_status(after, "site")})
    for secret in secrets.values():
        assert secret not in public_data
    assert "fake-review-password" not in public_data
    assert "fake-camera-secret" not in public_data


@pytest.mark.parametrize("uri", ["mock://camera", "synthetic://feature_demo"])
def test_environment_resolution_cannot_hide_mock_source_in_live_profile(config_file, monkeypatch, uri):
    monkeypatch.setenv("JIANWEI_DATABASE_URL", "postgresql://localhost/review")
    monkeypatch.setenv("JIANWEI_TEACHER_KEY", "fake-review-key")
    monkeypatch.setenv("JIANWEI_JWT_SECRET", "fake-review-signing-key")
    monkeypatch.setenv("JIANWEI_CAMERA_01_RTSP", uri)
    with pytest.raises(ContractError):
        resolve_environment(load_bundle(config_file, "site"))


def test_paths_cannot_escape_data_root_via_parent_reference(config_file):
    raw = parse_yaml(config_file.read_text())
    raw["paths"]["memory"] = "../outside"
    config_file.write_text(yaml.safe_dump(raw, allow_unicode=True))
    with pytest.raises(ContractError) as caught:
        load_bundle(config_file)
    assert caught.value.code == "PATH_OUTSIDE_DATA_ROOT"


def test_missing_enabled_secret_reports_binding_name_without_environment_dump(config_file, monkeypatch):
    monkeypatch.setenv("JIANWEI_TEACHER_KEY", "fake-unrelated-private-value")
    monkeypatch.delenv("JIANWEI_DATABASE_URL", raising=False)
    with pytest.raises(ContractError) as caught:
        resolve_environment(load_bundle(config_file, "site"))
    assert caught.value.code == "ENV_MISSING"
    assert "JIANWEI_DATABASE_URL" in str(caught.value)
    assert "fake-unrelated-private-value" not in str(caught.value.detail())
