"""Bounded YAML 1.2 core parser; duplicate keys and aliases are unsupported.

PyYAML defaults to YAML 1.1 scalar resolution. Keep product scalars aligned
with the browser's YAML 1.2 core schema without changing its global loaders
or the supplied reference toolkit.
"""
from __future__ import annotations
import re
import yaml
from packages.config.errors import ContractError

MAX_YAML_BYTES = 262_144


class UniqueLoader(yaml.SafeLoader):
    pass


# Copy resolver tables before modifying them: SafeLoader is also used by the
# independent reference tools and must keep its own existing behavior.
_CORE_REPLACED_TAGS = {f"tag:yaml.org,2002:{name}" for name in ("bool", "timestamp", "int", "float")}
UniqueLoader.yaml_implicit_resolvers = {
    initial: [(tag, pattern) for tag, pattern in resolvers if tag not in _CORE_REPLACED_TAGS]
    for initial, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)
UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int", re.compile(r"^(?:[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$"), list("-+0123456789")
)
UniqueLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(r"^(?:[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"),
    list("-+0123456789."),
)


def core_integer(loader, node):
    value = loader.construct_scalar(node)
    base = 8 if value.startswith("0o") else 16 if value.startswith("0x") else 10
    try:
        return int(value, base)
    except ValueError:
        raise yaml.constructor.ConstructorError(None, None, "invalid YAML 1.2 integer", node.start_mark) from None


UniqueLoader.add_constructor("tag:yaml.org,2002:int", core_integer)


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ContractError("YAML_KEY_INVALID", "YAML 映射的键必须为字符串")
        if key in result:
            raise ContractError("YAML_DUPLICATE_KEY", "YAML 存在重复键", [
                {"path": f"line:{key_node.start_mark.line + 1}", "message": "此位置的键重复定义"}
            ])
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse_yaml(text: str) -> dict:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ContractError("YAML_INVALID", "YAML 文档含无效 Unicode 字符") from None
    if size > MAX_YAML_BYTES:
        raise ContractError("YAML_TOO_LARGE", "YAML 文档超过 256 KiB 限制")
    # Aliases add no value to these configuration contracts and can expand without bound.
    try:
        for event in yaml.parse(text):
            if isinstance(event, yaml.AliasEvent):
                raise ContractError("YAML_ALIAS_UNSUPPORTED", "配置不允许 YAML 别名，请显式声明字段")
        result = yaml.load(text, Loader=UniqueLoader)
    except (yaml.YAMLError, RecursionError):
        raise ContractError("YAML_INVALID", "无法解析 YAML，请检查缩进、类型与文档结构") from None
    if not isinstance(result, dict):
        raise ContractError("YAML_ROOT_INVALID", "YAML 根节点必须为对象")
    return result
