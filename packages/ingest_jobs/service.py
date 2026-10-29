"""Durable bounded uploads and recoverable imports, scoped to a server tenant.

Only artifact IDs cross the HTTP boundary. Uploaded encoder bytes are fingerprint
material and are never loaded as executable models by this service.
"""
from __future__ import annotations
import hashlib
import asyncio
import json
import logging
import os
import tempfile
import shutil
import time
from pathlib import Path
from threading import Event, Lock, Thread, current_thread

from sqlalchemy import select, text
from sqlalchemy.orm import defer
from sqlalchemy.dialects.sqlite import insert
from infra.models import UploadedArtifact, ImportJob, SourceSplit, AuditEvent, new_id, utc_now
from packages.config.errors import ContractError
from packages.contracts.ingestion import ImportRequest
from packages.knowledge.service import content_hash


logger = logging.getLogger(__name__)

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def public_upload(row):
    return {'id': row.id, 'sha256': row.sha256, 'size_bytes': row.size_bytes, 'kind': row.kind}


def public_job(row, *, include_manifest=True, has_manifest=None):
    request = json.loads(row.request_json)
    return {'id': row.id, 'scene_id': row.scene_id, 'status': row.status,
            'format': request['format'], 'source_id': request['source_id'],
            'data_origin': request['data_origin'], 'created_at': row.created_at.isoformat(),
            'updated_at': row.updated_at.isoformat(),
            'config_hash': row.config_hash, 'knowledge_hash': row.knowledge_hash,
            'error': json.loads(row.error_json) if row.error_json else None,
            'progress': json.loads(row.progress_json) if row.progress_json else None,
            'cancel_requested': bool(row.cancel_requested),
            'manifest': json.loads(row.manifest_json) if include_manifest and row.manifest_json else None,
            'has_manifest': bool(row.manifest_json) if has_manifest is None else has_manifest}


class ImportService:
    def __init__(self, bundle, database):
        self.bundle, self.database = bundle, database
        self.settings = getattr(bundle.config, 'ingestion', None)
        self.tenant = bundle.profile.tenant_id
        self.tenant_key = hashlib.sha256(self.tenant.encode()).hexdigest()
        self.upload_root = bundle.paths['inbox'] / 'uploads' / self.tenant_key
        self.output_root = bundle.paths['datasets'] / 'imports' / self.tenant_key
        self.stop_event, self.wake_event, self.lock = Event(), Event(), Lock()
        self.thread = None
        self.worker_lock = None
        self.upload_lock = Lock()
        self.staging_bytes = 0
        self.cancellations = {}

    def capabilities(self):
        from packages.config.loader import resolve_path
        return {'enabled': self.settings is not None,
                'max_upload_bytes': self.settings.max_upload_bytes if self.settings else None,
                'upload_timeout_seconds': self.settings.upload_timeout_seconds if self.settings else None,
                'max_staging_bytes': self.settings.max_staging_bytes if self.settings else None,
                'formats': ['mp4', 'npz'] if self.settings else [],
                'encoder_adapter': self.bundle.config.encoder.adapter,
                'encoder_weights_available': resolve_path(self.bundle.root, self.bundle.profile.encoder_weights).is_file(),
                'example_available': self.settings is not None and self.bundle.profile.mode == 'demo'}

    def require_enabled(self):
        if self.settings is None:
            raise ContractError('INGESTION_DISABLED', '当前配置未启用数据导入，请配置 ingestion')

    def check_scene(self, scene_id):
        self.require_enabled()
        if scene_id not in self.bundle.scenes:
            raise ContractError('SCENE_NOT_FOUND', '场景不存在')

    def _audit(self, session, scene_id, event, job_id, payload=None):
        session.add(AuditEvent(tenant_id=self.tenant, scene_id=scene_id, event_type=event,
                               entity_id=job_id, payload_json=encode(payload or {})))

    def _path(self, artifact):
        # The content hash and kind come only from the immutable database record.
        return self.upload_root / (artifact.sha256 + '.' + artifact.kind)

    def _artifact(self, session, identifier, kind=None):
        row = session.scalar(select(UploadedArtifact).where(UploadedArtifact.id == identifier,
                    UploadedArtifact.tenant_id == self.tenant))
        if row is None:
            raise ContractError('UPLOAD_NOT_FOUND', '上传文件不存在或不属于当前租户')
        if kind and row.kind != kind:
            raise ContractError('UPLOAD_KIND_MISMATCH', '上传文件用途与导入格式不一致')
        return row

    def _finish_upload(self, temporary, digest, size, kind):
        if kind not in {'mp4', 'npz', 'encoder'}:
            raise ContractError('UPLOAD_KIND_INVALID', '不支持的上传文件用途')
        if size == 0:
            raise ContractError('UPLOAD_EMPTY', '上传文件不能为空')
        if kind == 'mp4':
            with temporary.open('rb') as stream:
                if stream.read(128).startswith(b'version https://git-lfs.github.com/spec/v1'):
                    raise ContractError('UPLOAD_LFS_POINTER', '当前文件只是 Git LFS 指针，请先拉取原视频实体')
        target = self.upload_root / (digest + '.' + kind)
        # Atomic rename is safe for concurrent identical bytes. Metadata is upserted.
        os.replace(temporary, target)
        with self.database.session() as session:
            session.execute(insert(UploadedArtifact).values(id=new_id(), tenant_id=self.tenant,
                sha256=digest, kind=kind, size_bytes=size, created_at=utc_now())
                .on_conflict_do_nothing(index_elements=['tenant_id', 'sha256', 'kind']))
            row = session.scalar(select(UploadedArtifact).where(UploadedArtifact.tenant_id == self.tenant,
                UploadedArtifact.sha256 == digest, UploadedArtifact.kind == kind))
            return public_upload(row)

    def _temporary_upload(self):
        self.require_enabled()
        self.upload_root.mkdir(parents=True, exist_ok=True)
        # A per-file lease survives until atomic publication. Under the shared
        # allocation lock, unlocked leftovers can only belong to interrupted
        # uploads; a concurrent live stream keeps its lease and is preserved.
        import fcntl
        with (self.upload_root / '.upload.lock').open('a') as allocation:
            fcntl.flock(allocation.fileno(), fcntl.LOCK_EX)
            for abandoned in self.upload_root.glob('.upload-*'):
                if abandoned.is_symlink():
                    continue
                try:
                    with abandoned.open('rb') as held:
                        try: fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError: continue
                        abandoned.unlink(missing_ok=True)
                except FileNotFoundError:
                    continue
            fd, path = tempfile.mkstemp(prefix='.upload-', dir=self.upload_root)
            fcntl.flock(fd, fcntl.LOCK_EX)
        return fd, Path(path)

    def _reserve_staging(self, amount):
        with self.upload_lock:
            if self.staging_bytes + amount > self.settings.max_staging_bytes:
                raise ContractError('UPLOAD_STAGING_LIMIT', '并发上传临时磁盘预算不足，请等待其他上传完成')
            if shutil.disk_usage(self.upload_root).free < amount:
                raise ContractError('UPLOAD_DISK_FULL', '上传存储空间不足，请清理数据后重试')
            self.staging_bytes += amount

    def _release_staging(self, amount):
        with self.upload_lock:
            self.staging_bytes -= amount

    async def upload(self, request, kind):
        self.require_enabled()
        length = request.headers.get('content-length') if hasattr(request, 'headers') else None
        if length is not None:
            try:
                declared = int(length)
                if declared < 0: raise ValueError
            except ValueError:
                raise ContractError('UPLOAD_LENGTH_INVALID', '上传长度声明无效') from None
            if declared > self.settings.max_upload_bytes:
                raise ContractError('UPLOAD_TOO_LARGE', '上传超过配置允许的字节上限')
        fd, temporary = self._temporary_upload()
        digest, size, reserved = hashlib.sha256(), 0, 0
        try:
            async with asyncio.timeout(self.settings.upload_timeout_seconds):
                with os.fdopen(fd, 'wb') as output:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > self.settings.max_upload_bytes:
                            raise ContractError('UPLOAD_TOO_LARGE', '上传超过配置允许的字节上限')
                        self._reserve_staging(len(chunk)); reserved += len(chunk)
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                    return self._finish_upload(temporary, digest.hexdigest(), size, kind)
        except TimeoutError:
            raise ContractError('UPLOAD_TIMEOUT', '上传超过配置时间限制，请重试或显式调整上传超时') from None
        finally:
            temporary.unlink(missing_ok=True)
            self._release_staging(reserved)

    def register_file(self, path, kind):
        self.require_enabled()
        if Path(path).stat().st_size > self.settings.max_upload_bytes:
            raise ContractError('UPLOAD_TOO_LARGE', '文件超过配置允许的字节上限')
        fd, temporary = self._temporary_upload()
        digest, size, reserved = hashlib.sha256(), 0, 0
        started = time.monotonic()
        try:
            with os.fdopen(fd, 'wb') as output, Path(path).open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    if time.monotonic() - started > self.settings.upload_timeout_seconds:
                        raise ContractError('UPLOAD_TIMEOUT', '文件登记超过配置时间限制')
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise ContractError('UPLOAD_TOO_LARGE', '示例文件超过配置上限')
                    self._reserve_staging(len(chunk)); reserved += len(chunk)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
                return self._finish_upload(temporary, digest.hexdigest(), size, kind)
        finally:
            temporary.unlink(missing_ok=True)
            self._release_staging(reserved)

    def _knowledge_hash(self, scene_id):
        knowledge, _ = self.bundle.current_knowledge(scene_id)
        return content_hash(knowledge.model_dump())

    def enqueue(self, scene_id, request):
        self.check_scene(scene_id)
        with self.lock, self.database.session() as session:
            uploaded = self._artifact(session, request.upload_id, request.format)
            artifact_hashes = {'input': uploaded.sha256}
            if request.encoder_artifact_id:
                artifact_hashes['encoder'] = self._artifact(session, request.encoder_artifact_id, 'encoder').sha256
            if request.source_upload_id:
                artifact_hashes['source'] = self._artifact(session, request.source_upload_id, 'mp4').sha256
            knowledge_hash = self._knowledge_hash(scene_id)
            body = request.model_dump(mode='json')
            implementation = self.implementation_hash()
            fingerprint = content_hash({'request': body, 'artifacts': artifact_hashes,
                'implementation_hash': implementation,
                'config_hash': self.bundle.config_hash, 'knowledge_hash': knowledge_hash})
            inserted = session.execute(insert(ImportJob).values(id=new_id(), tenant_id=self.tenant,
                scene_id=scene_id, idempotency_key=fingerprint, config_hash=self.bundle.config_hash,
                knowledge_hash=knowledge_hash, request_json=encode(body),
                config_snapshot_json=encode({**self.bundle.snapshot, '_ingestion_implementation': implementation}), status='queued',
                progress_json=encode({'stage': 'queued', 'completed': 0, 'total': None, 'unit': None}),
                cancel_requested=False,
                created_at=utc_now(), updated_at=utc_now())
                .on_conflict_do_nothing(index_elements=['tenant_id', 'scene_id', 'idempotency_key']))
            row = session.scalar(select(ImportJob).where(ImportJob.tenant_id == self.tenant,
                ImportJob.scene_id == scene_id, ImportJob.idempotency_key == fingerprint))
            if inserted.rowcount:
                self._audit(session, scene_id, 'import.queued', row.id, {'format': request.format})
            result = {**public_job(row), 'created': bool(inserted.rowcount)}
        self.wake_event.set()
        return result

    def _job(self, session, scene_id, identifier):
        row = session.scalar(select(ImportJob).where(ImportJob.id == identifier,
            ImportJob.tenant_id == self.tenant, ImportJob.scene_id == scene_id))
        if row is None: raise ContractError('IMPORT_NOT_FOUND', '导入任务不存在')
        return row

    def list(self, scene_id):
        self.check_scene(scene_id)
        with self.database.session() as session:
            # Do not deserialize every potentially large feature manifest to
            # render the task list. Full evidence remains on the detail endpoint.
            rows = session.execute(select(ImportJob, ImportJob.manifest_json.is_not(None))
                .options(defer(ImportJob.manifest_json)).where(ImportJob.tenant_id == self.tenant,
                ImportJob.scene_id == scene_id).order_by(ImportJob.created_at.desc(), ImportJob.id).limit(200))
            return {'items': [public_job(row, include_manifest=False, has_manifest=present) for row, present in rows]}

    def get(self, scene_id, identifier):
        self.check_scene(scene_id)
        with self.database.session() as session:
            return public_job(self._job(session, scene_id, identifier))

    def retry(self, scene_id, identifier):
        self.check_scene(scene_id)
        with self.lock, self.database.session() as session:
            row = self._job(session, scene_id, identifier)
            if row.config_hash != self.bundle.config_hash or row.knowledge_hash != self._knowledge_hash(scene_id):
                raise ContractError('IMPORT_CONFIG_CHANGED', '原任务配置或知识基线已变化；请新建导入以保留原记录')
            if row.status not in {'blocked', 'failed'}:
                return public_job(row)
            if json.loads(row.config_snapshot_json).get('_ingestion_implementation') != self.implementation_hash():
                raise ContractError('IMPORT_IMPLEMENTATION_CHANGED', '解码或特征实现已变化，请建立新导入保留历史结果')
            row.status, row.error_json, row.manifest_json, row.updated_at = 'queued', None, None, utc_now()
            row.cancel_requested=False
            row.progress_json=encode({'stage':'queued','completed':0,'total':None,'unit':None})
            self.cancellations.pop(identifier, None)
            self._audit(session, scene_id, 'import.retried', row.id)
            result = public_job(row)
        self.wake_event.set()
        return result

    def start(self):
        if self.settings is None: return
        import fcntl
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.worker_lock = (self.output_root / '.worker.lock').open('a')
        try:
            fcntl.flock(self.worker_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.worker_lock.close()
            self.worker_lock = None
            raise ContractError('INGESTION_WORKER_BUSY', '该数据目录已有导入服务，请使用现有实例') from None
        with self.database.session() as session:
            rows = session.scalars(select(ImportJob).where(ImportJob.tenant_id == self.tenant,
                ImportJob.status.in_(['queued', 'running'])))
            for row in rows:
                if row.manifest_json is None:
                    shutil.rmtree(self.output_root / row.id, ignore_errors=True)
                valid = row.config_hash == self.bundle.config_hash and row.scene_id in self.bundle.scenes
                if valid: valid = row.knowledge_hash == self._knowledge_hash(row.scene_id)
                valid = valid and json.loads(row.config_snapshot_json).get('_ingestion_implementation') == self.implementation_hash()
                if row.cancel_requested:
                    row.status='failed';row.error_json=encode({'code':'IMPORT_CANCELLED','message':'导入已取消，可手工重试'})
                elif valid:
                    row.status, row.error_json = 'queued', None
                    self._audit(session, row.scene_id, 'import.recovered', row.id)
                else:
                    row.status = 'blocked'
                    row.error_json = encode({'code': 'IMPORT_CONFIG_CHANGED', 'message': '重启时配置或知识已变化，请使用当前配置新建导入'})
                row.progress_json=encode({**json.loads(row.progress_json or '{}'), 'stage':row.status,
                    **({'completed':0,'total':None,'unit':None} if row.status == 'queued' else {})})
                row.updated_at = utc_now()
        self.thread = Thread(target=self._run, name='jianwei-import-worker', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            # Core decoder calls are bounded; retain the database until they finish.
            self.thread.join()
        if self.worker_lock:
            self.worker_lock.close()
            self.worker_lock = None

    def _run(self):
        while not self.stop_event.is_set():
            try:
                with self.database.session() as session:
                    session.execute(text('BEGIN IMMEDIATE'))
                    row = session.scalar(select(ImportJob).where(ImportJob.tenant_id == self.tenant,
                        ImportJob.status == 'queued').order_by(ImportJob.created_at, ImportJob.id).limit(1))
                    identifier = row.id if row else None
                    if row:
                        row.status, row.updated_at = 'running', utc_now()
                        self._audit(session, row.scene_id, 'import.started', row.id)
                if identifier:
                    self._process(identifier)
                    continue
            except Exception as error:
                # Do not lose the worker to transient storage failures. Report the
                # failure class without logging configuration or input contents.
                logger.error('Import worker iteration failed (%s)', type(error).__name__)
            self.wake_event.wait(self.settings.worker_poll_seconds)
            self.wake_event.clear()

    def _assign_split(self, session, manifest, requested):
        digest = manifest['source_hash']
        group_id = content_hash({'tenant_id': self.tenant, 'source_hash': digest})
        split = requested
        if not manifest.get('source_verified') and manifest.get('source_verification') != 'synthetic' and split != 'unassigned':
            raise ContractError('SOURCE_UNVERIFIED', '未核验原视频的特征不能进入训练或评测分区')
        if not manifest.get('source_verified') and manifest.get('source_verification') != 'synthetic':
            # A declared hash may name an already assigned real source; it must
            # not inherit train/test eligibility without source bytes verification.
            manifest['source_group_id'], manifest['split'] = group_id, 'unassigned'
            for batch in manifest.get('feature_batches', []):
                batch['split'] = 'unassigned'
            return
        # BEGIN IMMEDIATE in _process serializes assignment with every other writer.
        row = session.scalar(select(SourceSplit).where(SourceSplit.tenant_id == self.tenant, SourceSplit.source_hash == digest))
        if row is None:
            row = SourceSplit(id=group_id, tenant_id=self.tenant, source_hash=digest, split=split)
            session.add(row)
        elif row.split != 'unassigned' and split not in {'unassigned', row.split}:
            raise ContractError('SOURCE_SPLIT_CONFLICT', '同一原始来源已分配到其他分区；跨场景和 ROI 不得拆分')
        elif row.split == 'unassigned' and split != 'unassigned':
            row.split = split
        effective = row.split
        manifest['source_group_id'], manifest['split'] = group_id, effective
        for batch in manifest.get('feature_batches', []):
            batch['split'] = effective

    def _process(self, identifier):
        output = self.output_root / identifier
        last_write = [0.0]
        started = time.monotonic()
        stages = {}
        previous_stage = [None, started]
        def progress(stage, completed=0, total=None, unit=None):
            now = time.monotonic()
            if self.cancellations.get(identifier):
                raise ContractError('IMPORT_CANCELLED', '导入已取消，可重试')
            # Shutdown interrupts decoder probes; a new worker can resume a fresh attempt.
            if self.stop_event.is_set() and current_thread() is self.thread:
                raise ContractError('IMPORT_INTERRUPTED', '服务关闭中，重启后可重试')
            if now-last_write[0] < .25 and stage == previous_stage[0] and (total is None or completed != total): return
            with self.database.session() as session:
                row=session.get(ImportJob, identifier)
                if row.cancel_requested: raise ContractError('IMPORT_CANCELLED', '导入已取消，可重试')
                if stage != previous_stage[0]:
                    if previous_stage[0]: stages[previous_stage[0]]=stages.get(previous_stage[0],0)+now-previous_stage[1]
                    previous_stage[:]=[stage,now]
                prior=json.loads(row.progress_json) if row.progress_json else {}
                if total is None and prior.get('stage') == stage:
                    completed,total,unit=prior.get('completed',0),prior.get('total'),prior.get('unit')
                row.progress_json=encode({'stage':stage,'completed':completed,'total':total,'unit':unit,
                    'elapsed_seconds':round(now-started,3),'stage_seconds':{k:round(v,3) for k,v in stages.items()}})
                row.updated_at=utc_now()
            last_write[0]=now
        try:
            from packages.ingestion import process_import
            progress('verify')
            with self.database.session() as session:
                row = session.get(ImportJob, identifier)
                scene_id = row.scene_id
                if json.loads(row.config_snapshot_json).get('_ingestion_implementation') != self.implementation_hash():
                    raise ContractError('IMPORT_IMPLEMENTATION_CHANGED','导入实现已变化，请建立新任务')
                request = ImportRequest.model_validate(json.loads(row.request_json))
                input_path = self._path(self._artifact(session, request.upload_id, request.format))
                encoder_path = self._path(self._artifact(session, request.encoder_artifact_id, 'encoder')) if request.encoder_artifact_id else None
                source_path = self._path(self._artifact(session, request.source_upload_id, 'mp4')) if request.source_upload_id else None
                for artifact_id in [request.upload_id, request.encoder_artifact_id, request.source_upload_id]:
                    if artifact_id:
                        artifact = self._artifact(session, artifact_id)
                        path = self._path(artifact)
                        if not path.is_file() or path.stat().st_size != artifact.size_bytes:
                            raise ContractError('UPLOAD_MISSING', '上传文件已丢失或损坏，请重新上传')
                        with path.open('rb') as source:
                            digest=hashlib.sha256()
                            for block in iter(lambda:source.read(1024*1024),b''):
                                if self.cancellations.get(identifier) or (self.stop_event.is_set() and current_thread() is self.thread):
                                    raise ContractError('IMPORT_CANCELLED','导入已取消')
                                digest.update(block)
                            actual=digest.hexdigest()
                        if actual != artifact.sha256:
                            raise ContractError('UPLOAD_CORRUPTED', '上传内容校验失败，请重新上传')
            output = self.output_root / identifier
            output.mkdir(parents=True, exist_ok=True)
            manifest = process_import(self.bundle, scene_id, request, input_path,
                encoder_artifact_path=encoder_path, source_path=source_path, output_dir=output, progress=progress)
            progress('commit')
            with self.database.session() as session:
                # Acquire the SQLite write reservation before checking the source group.
                session.execute(text('BEGIN IMMEDIATE'))
                row = session.get(ImportJob, identifier)
                if row.cancel_requested: raise ContractError('IMPORT_CANCELLED','提交前收到取消请求')
                self._assign_split(session, manifest, request.requested_split)
                manifest.update(import_id=row.id, config_hash=row.config_hash, knowledge_hash=row.knowledge_hash)
                row.manifest_json = encode(manifest)
                row.status = manifest['status']
                row.error_json = encode(manifest['blocker']) if manifest.get('blocker') else None
                row.updated_at = utc_now()
                row.progress_json=encode({**json.loads(row.progress_json or '{}'), 'stage':row.status,
                    'elapsed_seconds':round(time.monotonic()-started,3)})
                self._audit(session, scene_id, 'import.' + row.status, row.id,
                    {'source_group_id': manifest['source_group_id'], 'split': manifest['split']})
                # DB is authoritative. File is an export cache; any interrupted write
                # is replaced on recovery, never treated as a committed job result.
                temporary = output / '.manifest.tmp'
                temporary.write_text(row.manifest_json, encoding='utf-8')
                os.replace(temporary, output / 'manifest.json')
        except ContractError as error:
            if output.exists(): shutil.rmtree(output)
            self._fail(identifier, error.detail())
        except Exception as error:
            logger.error('Import %s failed (%s)', identifier, type(error).__name__)
            if output.exists(): shutil.rmtree(output)
            self._fail(identifier, {'code': 'IMPORT_PROCESSING_FAILED', 'message': '导入处理失败；请检查文件格式与依赖后重试'})

    @staticmethod
    def implementation_hash():
        from packages.ingestion.pipeline import file_hash
        from packages.config.loader import PROJECT_ROOT
        paths = sorted((PROJECT_ROOT/'packages/ingestion').glob('*.py')) + sorted((PROJECT_ROOT/'packages/encoders').glob('*.py'))
        return content_hash({str(p.relative_to(PROJECT_ROOT)):file_hash(p) for p in paths})

    def cancel(self, scene_id, identifier):
        self.check_scene(scene_id)
        with self.lock,self.database.session() as session:
            row=self._job(session,scene_id,identifier)
            if row.status in {'queued','running'}:
                row.cancel_requested=True
                self.cancellations[identifier]=True
                if row.status=='queued':
                    row.status='failed';row.error_json=encode({'code':'IMPORT_CANCELLED','message':'导入已取消，可重试'})
                    row.progress_json=encode({**json.loads(row.progress_json or '{}'), 'stage':'failed'})
                row.updated_at=utc_now()
                self._audit(session,scene_id,'import.cancel_requested',identifier)
            result=public_job(row)
        self.wake_event.set()
        return result

    def _fail(self, identifier, detail):
        with self.database.session() as session:
            row = session.get(ImportJob, identifier)
            row.status, row.error_json, row.manifest_json, row.updated_at = 'failed', encode(detail), None, utc_now()
            row.progress_json=encode({**json.loads(row.progress_json or '{}'), 'stage':'failed'})
            self.cancellations.pop(identifier, None)
            self._audit(session, row.scene_id, 'import.failed', row.id, {'code': detail['code']})
