"""The single configuration loader shared by the API, CLI and future workers.

Hashes cover a canonical redacted source snapshot, independent of shell cwd,
resolved credentials, and machine-specific absolute path expansion.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import ipaddress
import os
import re
from urllib.parse import urlsplit
from pydantic import ValidationError
from packages.contracts.models import SystemConfig, Profile, Knowledge
from packages.contracts.configuration import SceneConfig
from packages.contracts.scenes import SceneSpec, SceneKnowledge
from packages.migrations.legacy_v3 import legacy_scene
from packages.config.errors import ContractError, schema_errors
from packages.config.yaml import parse_yaml
from packages.knowledge.service import parse_knowledge, content_hash

ENV_REFERENCE = re.compile(r"^env://([A-Za-z_][A-Za-z0-9_]*)$")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _redact(value, key=""):
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value]
    if isinstance(value, str):
        if value.startswith("env://"):
            return value
        if any(word in key.lower() for word in ("password", "secret", "api_key", "token")):
            return "[REDACTED]"
        if "://" in value:
            try:
                parts = urlsplit(value)
                if parts.username or parts.password or parts.query:
                    # Credentials may appear in userinfo or URL query parameters.
                    host = parts.hostname or ''
                    port = f':{parts.port}' if parts.port else ''
                    return f"{parts.scheme}://{host}{port}{parts.path}"
            except ValueError:
                return "[REDACTED]"
    return value


@dataclass(frozen=True)
class Bundle:
    config: SystemConfig | SceneConfig
    profile: Profile
    root: Path
    path: Path
    config_hash: str
    snapshot: dict
    knowledge: Knowledge | SceneKnowledge
    knowledge_yaml: str
    paths: dict[str, Path]
    scenes: dict[str, SceneSpec]

    def current_knowledge(self, scene_id: str | None = None) -> tuple[Knowledge | SceneKnowledge, str]:
        scene_id = scene_id or self.config.active_scene
        if scene_id not in self.scenes:
            raise ContractError('SCENE_NOT_FOUND', '场景不存在')
        spec = self.scenes[scene_id]
        try:
            text = resolve_path(self.root, spec.knowledge_file).read_text(encoding="utf-8")
        except OSError:
            raise ContractError("KNOWLEDGE_FILE_MISSING", "无法读取配置指定的知识包文件") from None
        return parse_knowledge(text, scene_id, spec.task_modes if self.config.config_version == '3.1' else None), text


def _check_reference(value: str) -> None:
    if value.startswith("env://") and not ENV_REFERENCE.fullmatch(value):
        raise ContractError("ENV_REFERENCE_INVALID", "环境变量引用必须使用 env://VARIABLE_NAME")


def _reject_inline_credentials(value: str) -> None:
    if "://" not in value or value.startswith("env://"):
        return
    try:
        parsed = urlsplit(value)
        if parsed.username or parsed.password or parsed.query:
            raise ContractError("SECRET_REFERENCE_REQUIRED", "带认证信息或查询参数的地址必须使用环境变量引用")
    except ValueError as error:
        if isinstance(error, ContractError):
            raise
        raise ContractError("URL_INVALID", "地址格式无效") from None


def _validate_source_uri(mode: str, source_type: str, uri: str) -> None:
    if mode != "demo" and uri.startswith(("synthetic://", "mock://")):
        raise ContractError("MOCK_SOURCE_FORBIDDEN", "非 demo 环境禁止 synthetic/mock 数据源")
    if source_type == "rtsp" and not uri.startswith(("rtsp://", "rtsps://")):
        raise ContractError("SOURCE_URI_INVALID", "RTSP 数据源需要 rtsp:// 或 rtsps:// 地址")
    if source_type == "synthetic" and not uri.startswith("synthetic://"):
        raise ContractError("SOURCE_URI_INVALID", "合成数据源需要 synthetic:// 地址")
    if source_type == "file" and "://" in uri:
        raise ContractError("SOURCE_URI_INVALID", "文件数据源需要本地文件路径")


def load_bundle(config_path: str | Path = PROJECT_ROOT / "config/system.yaml", profile: str | None = None) -> Bundle:
    path = Path(config_path).expanduser().resolve()
    root = path.parent.parent
    try:
        raw = parse_yaml(path.read_text(encoding="utf-8"))
    except OSError:
        raise ContractError("CONFIG_FILE_MISSING", "无法读取运行配置文件") from None
    if profile is not None:
        raw["active_profile"] = profile
    try:
        config = (SceneConfig if raw.get('config_version') == '3.1' else SystemConfig).model_validate(raw)
    except ValidationError as error:
        raise ContractError("CONFIG_INVALID", "运行配置不满足严格契约", schema_errors(error)) from None
    for item in config.profiles.values():
        for value in item.model_dump().values():
            if isinstance(value, str):
                _check_reference(value)
                _reject_inline_credentials(value)
        for secret_ref in (item.teacher_api_key_ref, item.jwt_secret_ref):
            if not ENV_REFERENCE.fullmatch(secret_ref):
                raise ContractError("SECRET_REFERENCE_REQUIRED", "密钥必须使用环境变量引用，不得写入 YAML")
        for source in item.sources:
            _check_reference(source.uri)
            _reject_inline_credentials(source.uri)
            if not source.uri.startswith("env://"):
                _validate_source_uri(item.mode, source.type, source.uri)
    for name, value in config.paths.model_dump().items():
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ContractError("PATH_OUTSIDE_DATA_ROOT", "paths 必须是 data_root 中的相对目录", [{"path": f"paths.{name}", "message": "不允许绝对路径或父目录引用"}])
    selected = config.profiles[config.active_profile]
    data_root = resolve_path(root, selected.data_root)
    paths = {name: resolve_path(data_root, value) for name, value in config.paths.model_dump().items()}
    if any(not value.is_relative_to(data_root) for value in paths.values()):
        raise ContractError("PATH_OUTSIDE_DATA_ROOT", "paths 解析结果不得离开 data_root")
    scenes = config.scenes if isinstance(config, SceneConfig) else {config.active_scene: legacy_scene(config)}
    loaded = {}
    for scene_id, spec in scenes.items():
        try:
            text = resolve_path(root, spec.knowledge_file).read_text(encoding="utf-8")
        except OSError:
            raise ContractError("KNOWLEDGE_FILE_MISSING", f"无法读取场景知识包：{scene_id}") from None
        loaded[scene_id] = (parse_knowledge(text, scene_id, spec.task_modes if isinstance(config, SceneConfig) else None), text)
    knowledge, knowledge_yaml = loaded[config.active_scene]
    snapshot = _redact(config.model_dump(exclude={'knowledge_file', 'session'} if isinstance(config, SceneConfig) else None))
    return Bundle(config=config, profile=selected, root=root, path=path, config_hash=content_hash(snapshot),
                  snapshot=snapshot, knowledge=knowledge, knowledge_yaml=knowledge_yaml, paths=paths, scenes=scenes)


def required_references(profile: Profile) -> dict[str, str]:
    """Return only bindings needed for enabled components (never their values)."""
    fields = {"database_url": profile.database_url, "redis_url": profile.redis_url}
    if profile.teacher_enabled:
        fields.update(teacher_api_key_ref=profile.teacher_api_key_ref, teacher_base_url=profile.teacher_base_url)
    if profile.auth_enabled:
        fields["jwt_secret_ref"] = profile.jwt_secret_ref
    fields.update({f"source.{source.id}": source.uri for source in profile.sources})
    return {field: value[6:] for field, value in fields.items() if value.startswith("env://")}


def resolve_environment(bundle: Bundle) -> dict:
    """Private runtime values. Callers must never log or persist this result."""
    result = bundle.profile.model_dump()
    for field, name in required_references(bundle.profile).items():
        value = os.environ.get(name)
        if not value:
            raise ContractError("ENV_MISSING", f"缺少启用组件必需的环境变量：{name}")
        if field.startswith("source."):
            identifier = field.removeprefix("source.")
            for source in result["sources"]:
                if source["id"] == identifier:
                    source["uri"] = value
        else:
            result[field] = value
    for source in result["sources"]:
        _validate_source_uri(bundle.profile.mode, source["type"], source["uri"])
    # Disabled components retain references, and their environment is not inspected.
    return result


def database_url(bundle: Bundle) -> str:
    url = resolve_environment(bundle)["database_url"]
    if url.startswith("sqlite:///"):
        filename = url.removeprefix("sqlite:///")
        if filename == ":memory:":
            return url
        location = resolve_path(bundle.root, filename)
        location.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{location}"
    return url


def assert_demo_service(bundle: Bundle) -> None:
    if bundle.profile.mode != "demo" or bundle.config.active_profile != "demo":
        raise ContractError("PROFILE_SERVICE_UNAVAILABLE", "P0.1.1 服务仅支持 demo；真实视频、鉴权和现场服务尚未实现")
    if bundle.profile.auth_enabled:
        raise ContractError("AUTH_NOT_IMPLEMENTED", "鉴权尚未实现，不能把 auth_enabled 视为安全能力")
    if not is_loopback(bundle.profile.api_host):
        raise ContractError("LOOPBACK_REQUIRED", "P0.1.1 API 必须绑定回环地址；通过受控预览访问单用户 demo")
    if not bundle.profile.database_url.startswith("sqlite:///"):
        raise ContractError("DATABASE_UNSUPPORTED", "P0.1.1 服务仅接通 SQLite，其他数据库尚未验证")


def profile_status(bundle: Bundle, profile_name: str) -> dict:
    if profile_name not in bundle.config.profiles:
        raise ContractError("PROFILE_NOT_FOUND", "配置中不存在该环境")
    profile = bundle.config.profiles[profile_name]
    blockers: list[dict[str, str]] = []
    checks = [{"code": "SCHEMA_VALID", "status": "passed", "message": "严格 schema、重复键、引用及语义交叉检查通过"}]
    def blocked(code, message):
        blockers.append({"code": code, "message": message})
        checks.append({"code": code, "status": "blocked", "message": message})
    for field, name in required_references(profile).items():
        if not os.environ.get(name):
            blocked("ENV_MISSING", f"缺少环境变量：{name}（{field}）")
        else:
            checks.append({"code": "ENV_PRESENT", "status": "passed", "message": f"已检测到环境变量：{name}；未验证远端授权"})
    if profile.mode != "demo":
        if any(bundle.current_knowledge(scene_id)[0].approval_status != "approved" for scene_id in bundle.scenes):
            blocked("KNOWLEDGE_DRAFT", "知识包仍为草案，现场使用前需要业务批准")
        weights = resolve_path(bundle.root, profile.encoder_weights)
        if not weights.is_file():
            blocked("ENCODER_WEIGHTS_MISSING", "缺少配置指定的编码器权重")
        if not bundle.config.encoder.weights_sha256:
            blocked("ENCODER_SHA_NOT_PINNED", "编码器权重 SHA-256 尚未锁定")
        elif weights.is_file():
            digest = hashlib.sha256()
            with weights.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != bundle.config.encoder.weights_sha256:
                blocked("ENCODER_SHA_MISMATCH", "编码器权重与锁定的 SHA-256 不一致")
        for source in profile.sources:
            uri = os.environ.get(source.uri[6:], "") if source.uri.startswith("env://") else source.uri
            if uri:
                try:
                    _validate_source_uri(profile.mode, source.type, uri)
                except ContractError as error:
                    blocked(error.code, error.message)
            if source.type == "file" and (not uri or not resolve_path(bundle.root, uri).is_file()):
                blocked("INPUT_FILE_MISSING", f"缺少真实视频文件（{source.id}）")
        if profile.auth_enabled:
            blocked("AUTH_NOT_IMPLEMENTED", "鉴权服务尚未实现，不能提供 lab/site 服务")
        blocked("PIPELINE_NOT_IMPLEMENTED", "真实视频管线和模型运行待后续阶段实现")
        checks.append({"code": "RUNTIME_NOT_CHECKED", "status": "not_run", "message": "未连接数据库、Redis、教师或相机，未加载模型，未验证 GPU/FFmpeg"})
    else:
        if not is_loopback(profile.api_host):
            blocked("LOOPBACK_REQUIRED", "无鉴权 demo 的 API 必须绑定回环地址")
        if profile.auth_enabled:
            blocked("AUTH_NOT_IMPLEMENTED", "鉴权服务尚未实现")
        checks.append({"code": "DEMO_SCOPE", "status": "passed", "message": "仅配置与知识管理基础演示，不代表视觉识别或学习效果"})
    return {"id": profile_name, "name": {"demo": "演示环境", "lab": "研发环境", "site": "现场环境"}.get(profile_name, profile_name),
            "mode": profile.mode, "static_valid": True, "runtime_ready": not blockers,
            "checks": checks, "blockers": blockers}
