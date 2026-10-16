"""P0.1 single-user demo API. No camera, inference, training or auth claims."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from infra.database import Database
from infra.models import AuditEvent, ConfigVersion, KnowledgeVersion, new_id
from packages.config.errors import ContractError
from packages.config.loader import PROJECT_ROOT, assert_demo_service, database_url, load_bundle, profile_status
from packages.contracts.models import Strict
from packages.knowledge.service import content_hash, knowledge_diff, preview


class ProfileRequest(Strict):
    profile: str = Field(min_length=1, max_length=64)


class PreviewRequest(Strict):
    yaml: str = Field(min_length=1, max_length=262144)


class VersionRequest(PreviewRequest):
    base_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class BodyLimit:
    """Bound requests before JSON parsing, including chunked transfer bodies."""
    def __init__(self, app, max_bytes=524288):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.max_bytes:
                return await JSONResponse({"detail": {"code": "REQUEST_TOO_LARGE", "message": "请求超过大小限制"}}, status_code=413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        used = False
        async def replay():
            nonlocal used
            if not used:
                used = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        await self.app(scope, replay, send)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _version(row: KnowledgeVersion) -> dict:
    return {"id": row.id, "hash": row.content_hash, "version": row.knowledge_version, "status": row.status,
            "base_hash": row.base_hash, "created_at": row.created_at.isoformat(), "diff": json.loads(row.diff_json)}


def _audit(session, event_type, entity_id, payload):
    session.add(AuditEvent(event_type=event_type, entity_id=entity_id, payload_json=_json(payload)))


def create_app(config_path: str | Path = PROJECT_ROOT / "config/system.yaml", profile: str | None = None) -> FastAPI:
    write_lock = Lock()

    @asynccontextmanager
    async def lifespan(application):
        bundle = load_bundle(config_path, profile)
        assert_demo_service(bundle)
        database = Database(database_url(bundle))
        database.migrate()
        application.state.bundle = bundle
        application.state.database = database
        with database.session() as session:
            identifier = new_id()
            result = session.execute(insert(ConfigVersion).values(id=identifier, config_hash=bundle.config_hash,
                profile=bundle.config.active_profile, snapshot_json=_json(bundle.snapshot)).on_conflict_do_nothing(index_elements=["config_hash"]))
            if result.rowcount:
                _audit(session, "config.snapshot.created", identifier, {"config_hash": bundle.config_hash, "profile": bundle.config.active_profile})
        baseline()
        try:
            yield
        finally:
            database.close()

    application = FastAPI(title="见微 P0.1 配置与知识服务", version="0.1.0", lifespan=lifespan)
    application.add_middleware(BodyLimit)

    @application.exception_handler(ContractError)
    async def contract_error(_request, error):
        status = 404 if error.code in {"PROFILE_NOT_FOUND", "VERSION_NOT_FOUND"} else 409 if error.code == "BASE_HASH_CONFLICT" else 422
        return JSONResponse({"detail": error.detail()}, status_code=status)

    @application.exception_handler(RequestValidationError)
    async def request_error(_request, error):
        errors = [{"path": ".".join(str(part) for part in item["loc"]), "message": f"请求字段不满足约束（{item['type']}）"}
                  for item in error.errors()]
        return JSONResponse({"detail": {"code": "REQUEST_INVALID", "message": "请求不满足数据契约", "errors": errors}}, status_code=422)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request, _error):
        return JSONResponse({"detail": {"code": "DATABASE_ERROR", "message": "数据库操作失败，事务已回滚"}}, status_code=503)

    def baseline():
        bundle = application.state.bundle
        knowledge, yaml_text = bundle.current_knowledge()
        digest = content_hash(knowledge.model_dump())
        with application.state.database.session() as session:
            identifier = new_id()
            result = session.execute(insert(KnowledgeVersion).values(id=identifier, content_hash=digest,
                knowledge_version=knowledge.knowledge_version, base_hash=None, status="baseline", yaml_text=yaml_text,
                data_json=_json(knowledge.model_dump()), diff_json=_json(knowledge_diff(knowledge, knowledge)))
                .on_conflict_do_nothing(index_elements=["content_hash"]))
            if result.rowcount:
                _audit(session, "knowledge.baseline.recorded", identifier, {"hash": digest, "source": "knowledge_file"})
        return knowledge, yaml_text, digest

    def versions():
        with application.state.database.session() as session:
            return [_version(row) for row in session.scalars(select(KnowledgeVersion).order_by(KnowledgeVersion.created_at.desc(), KnowledgeVersion.id))]

    @application.get("/api/health")
    def health():
        with application.state.database.session() as session:
            session.execute(text("SELECT 1"))
            revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        return {"status": "ok", "stage": "P0.1", "database": "ok", "schema_version": revision,
                "active_profile": application.state.bundle.config.active_profile}

    @application.get("/api/bootstrap")
    def bootstrap():
        bundle = application.state.bundle
        knowledge, yaml_text, digest = baseline()
        return {"project": {"name": "见微", "version": "3.0", "stage": "P0.1", "title": bundle.config.ui.title},
            "active_profile": bundle.config.active_profile, "config_hash": bundle.config_hash,
            "profiles": [profile_status(bundle, name) for name in bundle.config.profiles],
            "knowledge": {"yaml": yaml_text, "hash": digest, "version": knowledge.knowledge_version, "data": knowledge.model_dump()},
            "versions": versions(),
            "capabilities": {"configuration": True, "knowledge_preview": True, "knowledge_versioning": True,
                             "video_ingest": False, "training": False, "live_inference": False, "authentication": False},
            "blockers": [{"code": "DEMO_ONLY", "message": "当前交付为 P0.1 单用户配置与知识工作台；真实视频、学习训练、评测与鉴权待后续阶段实现"}]}

    @application.post("/api/config/validate")
    def validate_configuration(body: ProfileRequest):
        bundle = load_bundle(config_path, profile)
        result = profile_status(bundle, body.profile)
        result.update(config_hash=bundle.config_hash, checked_at=datetime.now(timezone.utc).isoformat())
        with application.state.database.session() as session:
            _audit(session, "config.profile.validated", body.profile, {"static_valid": result["static_valid"],
                "runtime_ready": result["runtime_ready"], "blocker_codes": [item["code"] for item in result["blockers"]]})
        return result

    @application.post("/api/knowledge/preview")
    def preview_knowledge(body: PreviewRequest):
        knowledge, _, _ = baseline()
        return preview(body.yaml, knowledge, application.state.bundle.config.active_scene)

    @application.get("/api/knowledge/versions")
    def list_versions():
        baseline()
        return {"versions": versions()}

    @application.get("/api/knowledge/versions/{version_id}")
    def version_details(version_id: str):
        with application.state.database.session() as session:
            row = session.get(KnowledgeVersion, version_id)
            if row is None:
                raise ContractError("VERSION_NOT_FOUND", "知识版本不存在")
            return {"version": {**_version(row), "yaml": row.yaml_text, "data": json.loads(row.data_json)}}

    @application.post("/api/knowledge/versions")
    def save_version(body: VersionRequest, response: Response):
        with write_lock:
            knowledge, _, current_hash = baseline()
            if body.base_hash != current_hash:
                raise ContractError("BASE_HASH_CONFLICT", "运行知识基线已变化，请刷新后重新预览再保存")
            result = preview(body.yaml, knowledge, application.state.bundle.config.active_scene)
            if not result["valid"]:
                raise ContractError("KNOWLEDGE_INVALID", "候选知识包未通过校验", result["errors"])
            with application.state.database.session() as session:
                identifier = new_id()
                inserted = session.execute(insert(KnowledgeVersion).values(id=identifier, content_hash=result["hash"],
                    knowledge_version=result["data"]["knowledge_version"], base_hash=body.base_hash, status="candidate",
                    yaml_text=body.yaml, data_json=_json(result["data"]), diff_json=_json(result["diff"]))
                    .on_conflict_do_nothing(index_elements=["content_hash"]))
                created = bool(inserted.rowcount)
                if created:
                    _audit(session, "knowledge.candidate.created", identifier, {"hash": result["hash"], "base_hash": body.base_hash, "diff": result["diff"]})
                row = session.scalar(select(KnowledgeVersion).where(KnowledgeVersion.content_hash == result["hash"]))
                payload = {"created": created, "version": _version(row)}
            response.status_code = 201 if created else 200
            return payload

    @application.get("/api/audit")
    def audit_history():
        with application.state.database.session() as session:
            rows = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100))
            return {"events": [{"id": row.id, "event_type": row.event_type, "entity_id": row.entity_id,
                                "payload": json.loads(row.payload_json), "created_at": row.created_at.isoformat()} for row in rows]}

    static_dir = PROJECT_ROOT / "apps/web/dist"
    if static_dir.is_dir():
        application.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    return application


app = create_app()
