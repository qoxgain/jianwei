"""Adversarial API checks for immutable candidates and safe public failures."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import select

from apps.api.main import create_app
from infra.models import KnowledgeVersion
from packages.config.errors import ContractError
from packages.config.yaml import parse_yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api_files(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "knowledge").mkdir()
    config = tmp_path / "config/system.yaml"
    labels = tmp_path / "knowledge/labels.yaml"
    config.write_text((ROOT / "tests/fixtures/v30/config/system.yaml").read_text())
    labels.write_text((ROOT / "tests/fixtures/v30/knowledge/labels.yaml").read_text())
    return config, labels


@pytest.fixture
def api(api_files):
    config, labels = api_files
    app = create_app(config)
    with TestClient(app) as client:
        yield client, app, labels


def changed_yaml(source, name="人工复核名称"):
    data = parse_yaml(source)
    data["actions"][0]["name"] = name
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def test_candidate_retry_is_idempotent_and_never_rewrites_source_or_original_candidate(api):
    client, app, labels = api
    original = labels.read_text()
    base = client.get("/api/bootstrap").json()["knowledge"]
    candidate = changed_yaml(original)
    first = client.post("/api/knowledge/versions", json={"yaml": candidate, "base_hash": base["hash"]})
    assert first.status_code == 201
    saved = first.json()["version"]
    repeat = client.post("/api/knowledge/versions", json={"yaml": "# presentation changed\n" + candidate, "base_hash": base["hash"]})
    assert repeat.status_code == 200
    assert repeat.json()["created"] is False
    assert repeat.json()["version"] == saved
    assert labels.read_text() == original
    assert client.get("/api/bootstrap").json()["knowledge"]["hash"] == base["hash"]
    with app.state.database.session() as session:
        record = session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.id == saved["id"]))
        assert record.yaml_text == candidate
        assert record.base_hash == base["hash"]
        assert record.status == "candidate"


def test_concurrent_duplicate_submissions_create_one_candidate_and_one_audit_event(api):
    client, _, labels = api
    base = client.get("/api/bootstrap").json()["knowledge"]
    payload = {"yaml": changed_yaml(labels.read_text()), "base_hash": base["hash"]}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post("/api/knowledge/versions", json=payload), range(4)))
    assert sorted(result.status_code for result in results) == [200, 200, 200, 201]
    assert len({result.json()["version"]["id"] for result in results}) == 1
    events = client.get("/api/audit").json()["events"]
    assert len([event for event in events if event["event_type"] == "knowledge.candidate.created"]) == 1


def test_stale_baseline_conflict_survives_external_file_change_and_does_not_save_candidate(api):
    client, _, labels = api
    original = labels.read_text()
    stale_hash = client.get("/api/bootstrap").json()["knowledge"]["hash"]
    labels.write_text(changed_yaml(original, "外部规范更改"))
    response = client.post("/api/knowledge/versions", json={"yaml": changed_yaml(original), "base_hash": stale_hash})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "BASE_HASH_CONFLICT"
    versions = client.get("/api/knowledge/versions").json()["versions"]
    assert all(version["status"] == "baseline" for version in versions)


def test_saved_candidate_survives_app_restart_with_same_immutable_identity(api_files):
    config, labels = api_files
    with TestClient(create_app(config)) as client:
        base = client.get("/api/bootstrap").json()["knowledge"]
        response = client.post("/api/knowledge/versions", json={"yaml": changed_yaml(labels.read_text()), "base_hash": base["hash"]})
        assert response.status_code == 201
        saved = response.json()["version"]
    with TestClient(create_app(config)) as restarted:
        versions = restarted.get("/api/knowledge/versions").json()["versions"]
        assert saved in versions
        assert restarted.get("/api/bootstrap").json()["knowledge"]["hash"] == base["hash"]


@pytest.mark.parametrize("payload", [
    {"yaml": {"private_value": "fake-review-secret-never-echo"}},
    {"yaml": "schema_version: '3.0'", "unexpected": "fake-review-secret-never-echo"},
    ["fake-review-secret-never-echo"],
])
def test_invalid_request_validation_never_echoes_input_values(api, payload):
    client, _, _ = api
    result = client.post("/api/knowledge/preview", json=payload)
    assert result.status_code == 422
    assert "fake-review-secret-never-echo" not in result.text
    assert result.json()["detail"]["code"] == "REQUEST_INVALID"


def test_unparseable_json_does_not_echo_input(api):
    client, _, _ = api
    result = client.post("/api/knowledge/preview", content='{"yaml": "fake-review-secret-never-echo', headers={"Content-Type": "application/json"})
    assert result.status_code == 422
    assert "fake-review-secret-never-echo" not in result.text


def test_oversized_request_fails_before_echoing_content(api):
    client, _, _ = api
    result = client.post("/api/knowledge/preview", json={"yaml": "x" * 530_000})
    assert result.status_code == 413
    assert result.json()["detail"]["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.parametrize("profile", ["lab", "site"])
def test_unimplemented_real_profiles_do_not_start_or_silently_fall_back_to_demo(api_files, profile):
    config, _ = api_files
    with pytest.raises(ContractError) as caught:
        with TestClient(create_app(config, profile)):
            pytest.fail("non-demo service unexpectedly started")
    assert caught.value.code == "PROFILE_SERVICE_UNAVAILABLE"


def test_capabilities_do_not_claim_real_video_training_or_authentication(api):
    client, _, _ = api
    info = client.get("/api/bootstrap").json()
    for capability in ("video_ingest", "training", "live_inference", "authentication"):
        assert info["capabilities"][capability] is False
    for profile in info["profiles"]:
        if profile["id"] in {"lab", "site"}:
            assert profile["runtime_ready"] is False
            assert any(item["code"] == "PIPELINE_NOT_IMPLEMENTED" for item in profile["blockers"])


def test_version_details_preserve_original_yaml_and_canonical_content(api):
    client, _, labels = api
    base = client.get("/api/bootstrap").json()["knowledge"]
    candidate = "# 审阅记录\n" + changed_yaml(labels.read_text())
    saved = client.post("/api/knowledge/versions", json={"yaml": candidate, "base_hash": base["hash"]}).json()["version"]
    response = client.get(f"/api/knowledge/versions/{saved['id']}")
    assert response.status_code == 200
    detail = response.json()["version"]
    assert {key: detail[key] for key in saved} == saved
    assert detail["yaml"] == candidate
    assert detail["data"]["actions"][0]["name"] == "人工复核名称"
    missing = client.get("/api/knowledge/versions/not-a-version")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "VERSION_NOT_FOUND"


def test_invalid_candidate_does_not_create_version_or_audit_event(api):
    client, _, labels = api
    base = client.get("/api/bootstrap").json()["knowledge"]
    before_versions = client.get("/api/knowledge/versions").json()
    before_audit = client.get("/api/audit").json()
    invalid = parse_yaml(labels.read_text())
    invalid["actions"][0]["positive_examples"] = ["sample_unregistered"]
    response = client.post("/api/knowledge/versions", json={
        "yaml": yaml.safe_dump(invalid, allow_unicode=True), "base_hash": base["hash"],
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "KNOWLEDGE_INVALID"
    assert client.get("/api/knowledge/versions").json() == before_versions
    assert client.get("/api/audit").json() == before_audit


def test_unimplemented_approval_is_not_exposed_as_activation_endpoint(api):
    client, _, _ = api
    assert client.post("/api/knowledge/releases", json={"version": "candidate"}).status_code in {404, 405}
