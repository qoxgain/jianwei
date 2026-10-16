"""Knowledge parsing and semantic diffs; persistence never edits the source YAML."""
from __future__ import annotations
import hashlib
import rfc8785
from pydantic import ValidationError
from packages.contracts.models import Knowledge
from packages.config.errors import ContractError, schema_errors
from packages.config.yaml import parse_yaml


def content_hash(value: dict) -> str:
    """RFC 8785 JSON canonicalization matches browser JSON number rendering."""
    try:
        canonical = rfc8785.dumps(value)
    except rfc8785.CanonicalizationError:
        raise ContractError("CANONICAL_VALUE_INVALID", "内容包含无法稳定生成跨端摘要的数字或字符") from None
    return hashlib.sha256(canonical).hexdigest()


def parse_knowledge(text: str, scene_id: str | None = None) -> Knowledge:
    raw = parse_yaml(text)
    try:
        knowledge = Knowledge.model_validate(raw)
    except ValidationError as error:
        raise ContractError("KNOWLEDGE_INVALID", "知识包不满足严格契约", schema_errors(error)) from None
    if scene_id is not None and knowledge.scene_id != scene_id:
        raise ContractError("KNOWLEDGE_SCENE_MISMATCH", "知识包场景与运行配置不一致")
    errors = []
    for index, action in enumerate(knowledge.actions):
        for field in ("positive_examples", "negative_examples", "prototype_refs"):
            if getattr(action, field):
                errors.append({"path": f"actions.{index}.{field}", "message": "P0.1 尚未接入样本与原型登记库，无法校验引用"})
    if knowledge.curated_memory:
        errors.append({"path": "curated_memory", "message": "P0.1 尚未接入机器记忆登记库，无法校验补丁目标"})
    if errors:
        raise ContractError("REFERENCE_REGISTRY_UNAVAILABLE", "知识包包含当前阶段无法核验的登记引用", errors)
    # Validate browser-compatible number/string domains before preview or persistence.
    content_hash(knowledge.model_dump())
    return knowledge


def knowledge_diff(before: Knowledge, after: Knowledge) -> dict:
    old = {action.id: action.model_dump() for action in before.actions}
    new = {action.id: action.model_dump() for action in after.actions}
    return {
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": [
            {"id": identifier, "fields": sorted(key for key in old[identifier] if old[identifier][key] != new[identifier][key])}
            for identifier in sorted(old.keys() & new.keys()) if old[identifier] != new[identifier]
        ],
        "sop_changed": before.sop != after.sop,
        "metadata_changed": sorted(key for key, value in before.model_dump().items()
                                   if key not in {"actions", "sop"} and value != after.model_dump()[key]),
    }


def preview(text: str, baseline: Knowledge, scene_id: str) -> dict:
    try:
        knowledge = parse_knowledge(text, scene_id)
    except ContractError as error:
        return {"valid": False, "errors": error.errors or [{"path": "", "message": error.message}],
                "hash": None, "diff": None, "impact": [], "data": None}
    diff = knowledge_diff(baseline, knowledge)
    impact = []
    if diff["added"]:
        impact.append("新增动作需要真实正反例、语义确认与独立评估。")
    if diff["removed"]:
        impact.append("删除动作将影响历史类别映射，激活前必须评估迁移影响。")
    if diff["changed"]:
        impact.append("动作语义变更需要重新核验样例；保留 action_id 可延续历史关联。")
    if diff["sop_changed"]:
        impact.append("SOP 变更需要业务复核，不会被学习系统自动激活。")
    if diff["metadata_changed"]:
        impact.append("知识包元数据已变化，请核对版本与批准状态。")
    if not any([diff["added"], diff["removed"], diff["changed"], diff["sop_changed"], diff["metadata_changed"]]):
        impact.append("语义内容没有变化，保存将复用已有版本。")
    impact.append("本阶段仅保存不可变候选版本，不覆盖 labels.yaml，不激活运行知识。")
    return {"valid": True, "errors": [], "hash": content_hash(knowledge.model_dump()),
            "diff": diff, "impact": impact, "data": knowledge.model_dump()}
