"""Independent adversarial checks for the P0.1 configuration boundary.

These fixtures are deliberately synthetic configuration inputs, not video evidence.
"""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from packages.config.errors import ContractError
from packages.config.yaml import parse_yaml
from packages.contracts.models import SystemConfig
from packages.knowledge.service import content_hash, parse_knowledge, preview


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def labels():
    return parse_yaml((ROOT / "knowledge/labels.yaml").read_text())


@pytest.mark.parametrize("text,code", [
    ("outer:\n  token: private-first\n  token: private-second\n", "YAML_DUPLICATE_KEY"),
    ("outer: &root\n  child: *root\n", "YAML_ALIAS_UNSUPPORTED"),
    ("[one, two]", "YAML_ROOT_INVALID"),
    ("{1: value}", "YAML_KEY_INVALID"),
    ("token: [private-first", "YAML_INVALID"),
    ("token: !!python/object/apply:os.system ['false']", "YAML_INVALID"),
])
def test_yaml_rejects_ambiguous_or_unsafe_inputs_without_echoing_values(text, code):
    with pytest.raises(ContractError) as caught:
        parse_yaml(text)
    assert caught.value.code == code
    public_error = str(caught.value.detail())
    assert "private-first" not in public_error
    assert "private-second" not in public_error


@pytest.mark.parametrize("section,field,value", [
    ("encoder", "frozen", 1),
    ("teacher", "frozen", 1),
    ("teacher", "include_forecast_in_prompt", 0),
    ("learning", "freeze_foundation_models", 1),
    ("learning", "allow_cross_tenant", 0),
])
def test_literal_boolean_invariants_reject_yaml_integer_lookalikes(section, field, value):
    config = parse_yaml((ROOT / "config/system.yaml").read_text())
    config[section][field] = value
    with pytest.raises(ValidationError):
        SystemConfig.model_validate(config)


@pytest.mark.parametrize("field,value", [
    ("min_visible_seconds", float("nan")),
    ("min_visible_seconds", float("inf")),
    ("require_object_contact", "false"),
    ("require_object_contact", 0),
])
def test_knowledge_rejects_nonfinite_numbers_and_boolean_coercion(labels, field, value):
    labels["actions"][0]["completion_rule"][field] = value
    with pytest.raises(ContractError):
        parse_knowledge(yaml.safe_dump(labels, allow_unicode=True))


def test_knowledge_semantic_hash_ignores_yaml_presentation_and_key_order(labels):
    first = parse_knowledge(yaml.safe_dump(labels, sort_keys=True, allow_unicode=True))
    second = parse_knowledge("# reformat only\n" + yaml.safe_dump(labels, sort_keys=False, allow_unicode=True))
    assert content_hash(first.model_dump()) == content_hash(second.model_dump())


def test_action_rename_preserves_identity_and_only_reports_semantic_fields(labels):
    baseline = parse_knowledge(yaml.safe_dump(labels, allow_unicode=True))
    changed = deepcopy(labels)
    changed["actions"][0]["name"] = "审阅后的名称"
    result = preview(yaml.safe_dump(changed, allow_unicode=True), baseline, labels["scene_id"])
    assert result["valid"] is True
    assert result["diff"] == {
        "added": [], "removed": [],
        "changed": [{"id": labels["actions"][0]["id"], "fields": ["name"]}],
        "sop_changed": False, "metadata_changed": [],
    }
    assert baseline.actions[0].name == labels["actions"][0]["name"]


def test_unapproved_sop_cycle_is_rejected_but_explicit_rework_is_valid(labels):
    loop = {"from_action": labels["sop"]["nodes"][-1]["action_id"],
            "to_action": labels["sop"]["nodes"][0]["action_id"]}
    invalid = deepcopy(labels)
    invalid["sop"]["edges"].append(loop)
    with pytest.raises(ContractError):
        parse_knowledge(yaml.safe_dump(invalid, allow_unicode=True))
    labels["sop"]["repeatable_edges"].append(loop)
    assert parse_knowledge(yaml.safe_dump(labels, allow_unicode=True)).sop.repeatable_edges


def test_unknown_scene_is_not_accepted_as_current_knowledge(labels):
    with pytest.raises(ContractError) as caught:
        parse_knowledge(yaml.safe_dump(labels, allow_unicode=True), "different_scene")
    assert caught.value.code == "KNOWLEDGE_SCENE_MISMATCH"


def test_invalid_preview_does_not_return_secret_like_invalid_value(labels):
    baseline = parse_knowledge(yaml.safe_dump(labels, allow_unicode=True))
    labels["actions"][0]["risk_level"] = "DO-NOT-EXPOSE-THIS-INPUT"
    result = preview(yaml.safe_dump(labels, allow_unicode=True), baseline, labels["scene_id"])
    assert result["valid"] is False
    assert result["hash"] is None and result["data"] is None and result["diff"] is None
    assert "DO-NOT-EXPOSE-THIS-INPUT" not in str(result)


@pytest.mark.parametrize("field", ["id", "name"])
def test_action_identity_cannot_be_blank(labels, field):
    labels["actions"][0][field] = "  "
    with pytest.raises(ContractError):
        parse_knowledge(yaml.safe_dump(labels, allow_unicode=True))


def test_knowledge_requires_actions_and_nonblank_version_or_approver(labels):
    for change in (
        {"actions": []}, {"knowledge_version": "  "},
        {"approval_status": "approved", "approved_by": "  "},
    ):
        with pytest.raises(ContractError):
            parse_knowledge(yaml.safe_dump({**labels, **change}, allow_unicode=True))


@pytest.mark.parametrize("field", ["positive_examples", "negative_examples", "prototype_refs"])
def test_unregistered_sample_references_are_not_claimed_valid(labels, field):
    labels["actions"][0][field] = ["sample_unregistered"]
    with pytest.raises(ContractError) as caught:
        parse_knowledge(yaml.safe_dump(labels, allow_unicode=True))
    assert caught.value.code == "REFERENCE_REGISTRY_UNAVAILABLE"
    assert caught.value.errors[0]["path"] == f"actions.0.{field}"


def test_unregistered_memory_patch_target_is_not_claimed_valid(labels):
    labels["curated_memory"] = [{"op": "reject_sample", "target_id": "sample_unregistered",
                                 "action_id": None, "related_action_id": None, "reason": "待核验"}]
    with pytest.raises(ContractError) as caught:
        parse_knowledge(yaml.safe_dump(labels, allow_unicode=True))
    assert caught.value.code == "REFERENCE_REGISTRY_UNAVAILABLE"


def test_lone_surrogate_is_a_controlled_yaml_error():
    with pytest.raises(ContractError) as caught:
        parse_yaml("name: \ud800")
    assert caught.value.code == "YAML_INVALID"
