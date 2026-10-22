"""P0.1.1 single-user scene API. No camera, inference, training or auth claims."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import yaml
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
from infra.legacy_data import record_legacy_mappings
from infra.models import AuditEvent, ConfigVersion, KnowledgeVersion, LegacyKnowledgeMapping, new_id
from packages.config.errors import ContractError
from packages.config.loader import PROJECT_ROOT, assert_demo_service, database_url, load_bundle, profile_status
from packages.contracts.models import Strict, Knowledge
from packages.migrations.legacy_v3 import adapt_knowledge
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


def _audit(session, event_type, entity_id, payload, tenant_id, scene_id):
    session.add(AuditEvent(event_type=event_type, entity_id=entity_id, payload_json=_json(payload), tenant_id=tenant_id, scene_id=scene_id))


def create_app(config_path: str | Path = PROJECT_ROOT / "config/system.yaml", profile: str | None = None) -> FastAPI:
    write_lock = Lock()

    @asynccontextmanager
    async def lifespan(application):
        bundle = load_bundle(config_path, profile)
        assert_demo_service(bundle)
        database = Database(database_url(bundle))
        database.migrate(bundle.profile.tenant_id, bundle.config.active_scene)
        application.state.bundle, application.state.database = bundle, database
        try:
            for scene_id in bundle.scenes:
                with database.session() as session:
                    identifier = new_id()
                    result = session.execute(insert(ConfigVersion).values(id=identifier, config_hash=bundle.config_hash,
                        tenant_id=bundle.profile.tenant_id, scene_id=scene_id, profile=bundle.config.active_profile,
                        snapshot_json=_json(bundle.snapshot)).on_conflict_do_nothing(index_elements=['tenant_id','scene_id','config_hash']))
                    if result.rowcount:
                        audit(session, 'config.snapshot.created', identifier, {'config_hash':bundle.config_hash}, scene_id)
                baseline(scene_id)
            yield
        finally:
            database.close()

    application = FastAPI(title="见微 P0.1.1 通用场景知识服务", version="0.1.1", lifespan=lifespan)
    application.add_middleware(BodyLimit)

    def scope(scene_id=None):
        bundle = application.state.bundle
        scene_id = scene_id or bundle.config.active_scene
        if scene_id not in bundle.scenes:
            raise ContractError('SCENE_NOT_FOUND', '场景不存在')
        return bundle.profile.tenant_id, scene_id

    def audit(session, event_type, entity_id, payload, scene_id=None):
        tenant, scene = scope(scene_id)
        _audit(session, event_type, entity_id, payload, tenant, scene)

    def scoped(model, scene_id=None):
        tenant, scene = scope(scene_id)
        return select(model).where(model.tenant_id == tenant, model.scene_id == scene)

    def modes(scene_id, normalized=True):
        bundle = application.state.bundle
        return bundle.scenes[scene_id].task_modes if normalized or bundle.config.config_version == '3.1' else None

    @application.exception_handler(ContractError)
    async def contract_error(_request, error):
        status = 404 if error.code in {'PROFILE_NOT_FOUND','VERSION_NOT_FOUND','SCENE_NOT_FOUND'} else 409 if error.code == 'BASE_HASH_CONFLICT' else 422
        return JSONResponse({'detail':error.detail()}, status_code=status)

    @application.exception_handler(RequestValidationError)
    async def request_error(_request, error):
        errors = [{'path':'.'.join(str(part) for part in item['loc']), 'message':f"请求字段不满足约束（{item['type']}）"} for item in error.errors()]
        return JSONResponse({'detail':{'code':'REQUEST_INVALID','message':'请求不满足数据契约','errors':errors}},status_code=422)

    @application.exception_handler(SQLAlchemyError)
    async def database_error(_request, _error):
        return JSONResponse({'detail':{'code':'DATABASE_ERROR','message':'数据库操作失败，事务已回滚'}},status_code=503)

    def baseline(scene_id=None, normalized=False):
        tenant, scene_id = scope(scene_id)
        knowledge, yaml_text = application.state.bundle.current_knowledge(scene_id)
        if normalized and isinstance(knowledge, Knowledge):
            knowledge, _ = adapt_knowledge(knowledge)
            yaml_text = yaml.safe_dump(knowledge.model_dump(), allow_unicode=True, sort_keys=False)
        digest = content_hash(knowledge.model_dump())
        with application.state.database.session() as session:
            identifier = new_id()
            result = session.execute(insert(KnowledgeVersion).values(id=identifier, tenant_id=tenant, scene_id=scene_id,
                content_hash=digest, knowledge_version=knowledge.knowledge_version, base_hash=None, status='baseline',
                yaml_text=yaml_text, data_json=_json(knowledge.model_dump()), diff_json=_json(knowledge_diff(knowledge,knowledge)))
                .on_conflict_do_nothing(index_elements=['tenant_id','scene_id','content_hash']))
            if result.rowcount:
                audit(session,'knowledge.baseline.recorded',identifier,{'hash':digest,'source':'knowledge_file'},scene_id)
                if isinstance(knowledge, Knowledge): record_legacy_mappings(session.connection())
        return knowledge, yaml_text, digest

    def versions(scene_id=None):
        with application.state.database.session() as session:
            return [_version(row) for row in session.scalars(scoped(KnowledgeVersion,scene_id).order_by(KnowledgeVersion.created_at.desc(),KnowledgeVersion.id))]

    def scene_info(scene_id, normalized=True):
        scope(scene_id)
        bundle = application.state.bundle
        spec = bundle.scenes[scene_id]
        knowledge, _ = bundle.current_knowledge(scene_id)
        if normalized and isinstance(knowledge, Knowledge): knowledge, _ = adapt_knowledge(knowledge)
        sources = [{'id':s.id,'type':s.type,'roi_xyxy':s.roi_xyxy} for s in bundle.profile.sources
                   if scene_id in (s.scene_ids if hasattr(s,'scene_ids') else [s.scene_id])]
        return {'id':scene_id, **spec.model_dump(), 'sources':sources,
                'config_hash':content_hash({'scene':spec.model_dump(),'sources':sources,'profile':bundle.config.active_profile}),
                'knowledge_hash':content_hash(knowledge.model_dump()), 'data_status':'not_connected',
                'rules_status':'configured_unverified' if getattr(knowledge,'events',[]) else 'collection_only'}

    @application.get('/api/health')
    def health():
        with application.state.database.session() as session:
            session.execute(text('SELECT 1'))
            revision = session.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        return {'status':'ok','stage':'P0.1.1','database':'ok','schema_version':revision,
                'active_profile':application.state.bundle.config.active_profile}

    @application.get('/api/scenes')
    def scenes():
        bundle = application.state.bundle
        return {'active_scene':bundle.config.active_scene, 'scenes':[scene_info(id) for id in bundle.scenes]}

    @application.get('/api/scenes/{scene_id}')
    def scene_bootstrap(scene_id: str):
        return bootstrap_payload(scene_id, True)

    def bootstrap_payload(scene_id, normalized):
        scope(scene_id)
        bundle = application.state.bundle
        knowledge, yaml_text, digest = baseline(scene_id, normalized)
        return {'project':{'name':'见微','version':'3.0','stage':'P0.1.1','title':bundle.config.ui.title},
            'active_profile':bundle.config.active_profile, 'active_scene':scene_id, 'tenant_id':bundle.profile.tenant_id,
            'config_hash':bundle.config_hash, 'scene':scene_info(scene_id, normalized),
            'scenes':[scene_info(id, normalized) for id in bundle.scenes],
            'profiles':[profile_status(bundle,name) for name in bundle.config.profiles],
            'knowledge':{'yaml':yaml_text,'hash':digest,'version':knowledge.knowledge_version,'data':knowledge.model_dump()},
            'versions':versions(scene_id),
            'capabilities':{'configuration':True,'knowledge_preview':True,'knowledge_versioning':True,
                            'video_ingest':False,'training':False,'live_inference':False,'authentication':False},
            'blockers':[{'code':'DEMO_ONLY','message':'仅通用场景配置与知识版本管理；视频、学习、预测、正式鉴权未实现'}]}

    @application.get('/api/bootstrap', deprecated=True)
    def bootstrap():
        """Compatibility alias, always resolves to the configured default scene."""
        return bootstrap_payload(application.state.bundle.config.active_scene, False)

    @application.post('/api/config/validate')
    def validate_configuration(body: ProfileRequest):
        bundle = load_bundle(config_path, profile)
        result = profile_status(bundle,body.profile)
        result.update(config_hash=bundle.config_hash, checked_at=datetime.now(timezone.utc).isoformat())
        with application.state.database.session() as session:
            audit(session,'config.profile.validated',body.profile,{'static_valid':result['static_valid'],
                'runtime_ready':result['runtime_ready'],'blocker_codes':[i['code'] for i in result['blockers']]})
        return result

    @application.post('/api/scenes/{scene_id}/knowledge/preview')
    def scene_preview(scene_id: str, body: PreviewRequest):
        return preview_payload(scene_id, body, True)

    def preview_payload(scene_id, body, normalized):
        knowledge, _, _ = baseline(scene_id, normalized)
        return preview(body.yaml,knowledge,scene_id,modes(scene_id, normalized))

    @application.get('/api/scenes/{scene_id}/knowledge/versions')
    def scene_versions(scene_id: str):
        baseline(scene_id)
        return {'versions':versions(scene_id)}

    @application.get('/api/scenes/{scene_id}/knowledge/versions/{version_id}')
    def scene_version_details(scene_id: str, version_id: str):
        with application.state.database.session() as session:
            row = session.scalar(scoped(KnowledgeVersion,scene_id).where(KnowledgeVersion.id == version_id))
            if row is None: raise ContractError('VERSION_NOT_FOUND','知识版本不存在')
            mapping = session.get(LegacyKnowledgeMapping,version_id)
            payload = {**_version(row),'yaml':row.yaml_text,'data':json.loads(row.data_json)}
            if mapping:
                payload['migration'] = {'old_hash':mapping.old_hash, 'normalized_hash':mapping.normalized_hash,
                    'yaml':mapping.normalized_yaml, 'data':json.loads(mapping.normalized_json), 'node_map':json.loads(mapping.node_map_json)}
            return {'version':payload}

    @application.post('/api/scenes/{scene_id}/knowledge/versions')
    def scene_save(scene_id: str, body: VersionRequest, response: Response):
        return save_payload(scene_id, body, response, True)

    def save_payload(scene_id, body, response, normalized):
        tenant, scene_id = scope(scene_id)
        with write_lock:
            knowledge, _, current_hash = baseline(scene_id, normalized)
            if body.base_hash != current_hash:
                raise ContractError('BASE_HASH_CONFLICT','运行知识基线已变化，请刷新后重新预览再保存')
            result = preview(body.yaml,knowledge,scene_id,modes(scene_id,normalized))
            if not result['valid']: raise ContractError('KNOWLEDGE_INVALID','候选知识包未通过校验',result['errors'])
            with application.state.database.session() as session:
                identifier = new_id()
                inserted = session.execute(insert(KnowledgeVersion).values(id=identifier,tenant_id=tenant,scene_id=scene_id,
                    content_hash=result['hash'],knowledge_version=result['data']['knowledge_version'],base_hash=body.base_hash,
                    status='candidate',yaml_text=body.yaml,data_json=_json(result['data']),diff_json=_json(result['diff']))
                    .on_conflict_do_nothing(index_elements=['tenant_id','scene_id','content_hash']))
                created = bool(inserted.rowcount)
                if created:
                    audit(session,'knowledge.candidate.created',identifier,{'hash':result['hash'],'base_hash':body.base_hash,'diff':result['diff']},scene_id)
                    if result['data']['schema_version'] == '3.0': record_legacy_mappings(session.connection())
                row = session.scalar(scoped(KnowledgeVersion,scene_id).where(KnowledgeVersion.content_hash == result['hash']))
                payload = {'created':created,'version':_version(row)}
            response.status_code = 201 if created else 200
            return payload

    @application.get('/api/scenes/{scene_id}/audit')
    def scene_audit(scene_id: str):
        with application.state.database.session() as session:
            rows = session.scalars(scoped(AuditEvent,scene_id).order_by(AuditEvent.created_at.desc()).limit(100))
            return {'events':[{'id':row.id,'event_type':row.event_type,'entity_id':row.entity_id,
                'payload':json.loads(row.payload_json),'created_at':row.created_at.isoformat()} for row in rows]}

    @application.post('/api/knowledge/preview',deprecated=True)
    def preview_knowledge(body: PreviewRequest): return preview_payload(scope()[1],body,False)
    @application.get('/api/knowledge/versions',deprecated=True)
    def list_versions(): return scene_versions(scope()[1])
    @application.get('/api/knowledge/versions/{version_id}',deprecated=True)
    def version_details(version_id: str): return scene_version_details(scope()[1],version_id)
    @application.post('/api/knowledge/versions',deprecated=True)
    def save_version(body: VersionRequest,response: Response): return save_payload(scope()[1],body,response,False)
    @application.get('/api/audit',deprecated=True)
    def audit_history(): return scene_audit(scope()[1])

    static_dir = PROJECT_ROOT / 'apps/web/dist'
    if static_dir.is_dir(): application.mount('/',StaticFiles(directory=static_dir,html=True),name='web')
    return application

app = create_app()
