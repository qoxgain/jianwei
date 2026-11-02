"""Durable local research jobs. No scheduler, automatic test use or activation."""
import json
import logging
from threading import Event, Lock, Thread
from sqlalchemy import select, text, func
from sqlalchemy.dialects.sqlite import insert
from infra.models import TemporalRun, ImportJob, AuditEvent, new_id, utc_now
from packages.config.errors import ContractError
from packages.knowledge.service import content_hash
from packages.ingest_jobs.service import encode
from packages.temporal import engine
from packages.temporal.data import snapshot_imports, load_sequences, fail
from packages.temporal.provenance import assert_evaluation_independent
from packages.temporal.protocol import freeze_protocol, preview_protocol, assert_frozen_protocol, heldout_identity

logger=logging.getLogger(__name__)


def public(row,detail=True):
    return {'id':row.id,'scene_id':row.scene_id,'kind':row.kind,'status':row.status,
        'created_at':row.created_at.isoformat(),'updated_at':row.updated_at.isoformat(),
        'config_hash':row.config_hash,'engine_hash':row.engine_hash,
        'error':json.loads(row.error_json) if row.error_json else None,
        'report':json.loads(row.report_json) if detail and row.report_json else None,
        'request':json.loads(row.request_json) if detail else None,
        'snapshot':json.loads(row.snapshot_json) if detail else None}


class TemporalService:
    def __init__(self,bundle,database,imports):
        self.bundle,self.database,self.imports=bundle,database,imports
        self.settings=getattr(bundle.config,'temporal',None)
        self.tenant=bundle.profile.tenant_id
        self.output_root=bundle.paths['datasets']/'temporal'/imports.tenant_key
        self.lock=Lock();self.stop_event=Event();self.wake_event=Event();self.thread=None;self.worker_lock=None

    def capabilities(self):
        return {'enabled':self.settings is not None and self.bundle.config.learning.enabled,
                'models':list(engine.MODELS),'example_available':self.settings is not None and self.bundle.profile.mode=='demo',
                'auto_train':False,'auto_evaluate':False,'auto_activate':False,'semantic_recognition':False}

    def check_scene(self,scene):
        if self.settings is None or not self.bundle.config.learning.enabled:fail('TEMPORAL_DISABLED','当前配置未启用时序研究')
        if scene not in self.bundle.scenes:fail('SCENE_NOT_FOUND','场景不存在')

    def _knowledge_hash(self,scene):return content_hash(self.bundle.current_knowledge(scene)[0].model_dump())
    def _run_row(self,session,scene,identifier):
        row=session.scalar(select(TemporalRun).where(TemporalRun.id==identifier,TemporalRun.tenant_id==self.tenant,TemporalRun.scene_id==scene))
        if row is None:fail('TEMPORAL_RUN_NOT_FOUND','研究任务不存在')
        return row
    def _audit(self,session,row,event,payload=None):
        session.add(AuditEvent(tenant_id=self.tenant,scene_id=row.scene_id,event_type=event,
                              entity_id=row.id,payload_json=encode(payload or {})))

    def _heldout_available(self,session,binding,owner=None):
        if binding is None:return
        identity=heldout_identity(binding['snapshot']['roster'])
        # Every producer uses BEGIN IMMEDIATE before testing and adding these
        # append-only reservations, so separate API/worker instances serialize.
        events=session.scalars(select(AuditEvent).where(AuditEvent.tenant_id==self.tenant,
            AuditEvent.event_type.in_(['temporal.test_reserved','temporal.test_consumed'])))
        for event in events:
            if event.entity_id==owner:continue
            previous=json.loads(event.payload_json)
            if any(set(identity[key]) & set(previous.get(key,[])) for key in identity):
                fail('TEMPORAL_TEST_ALREADY_USED','封存来源、原始根或采集会话已分配给另一评估；新模型或协议须另设封存测试')
        # Neither an upgrade nor a transcode can seal previously used data.
        # Include historical fitting/calibration sources, not only evaluations:
        # new byte hashes retain their original root and acquisition session.
        # Conservatively include failed/interrupted attempts because absence of
        # a successful report cannot prove values were never exposed.
        legacy = session.scalars(select(TemporalRun).where(
            TemporalRun.tenant_id==self.tenant, TemporalRun.kind.in_(['train','evaluate']),
            func.json_extract(TemporalRun.snapshot_json,'$.data_origin')=='real_video'))
        for row in legacy:
            if row.id==owner:continue
            entries=json.loads(row.snapshot_json).get('entries',[])
            previous={
                'source_hashes':{entry['source_hash'] for entry in entries},
                'root_source_hashes':{(entry.get('provenance') or {}).get('root_source_sha256',entry['source_hash']) for entry in entries},
                'capture_session_ids':{(entry.get('provenance') or {}).get('capture_session_id') for entry in entries}}
            if any(set(identity[key]) & previous[key] for key in identity):
                fail('TEMPORAL_TEST_ALREADY_USED','该来源、原始根或会话已有真实训练、校准或评估记录；不能重新声明为封存测试')

    def _test_consumed(self,session,identifier):
        return session.scalar(select(AuditEvent.id).where(AuditEvent.tenant_id==self.tenant,
            AuditEvent.entity_id==identifier,AuditEvent.event_type=='temporal.test_consumed')) is not None

    def _consume_test(self,identifier,binding):
        if binding is None:return
        with self.lock,self.database.session() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row=session.get(TemporalRun,identifier)
            self._heldout_available(session,binding,owner=identifier)
            if self._test_consumed(session,identifier):
                fail('TEMPORAL_TEST_ALREADY_CONSUMED','该封存测试已开始读取；失败或重启后不再重读，请保留报告并另设封存测试')
            # Commit this marker BEFORE opening any heldout feature cache. Even
            # a later I/O failure cannot erase exposure or make retry safe.
            self._audit(session,row,'temporal.test_consumed',{
                **heldout_identity(binding['snapshot']['roster']),'protocol_hash':binding['hash']})

    def enqueue(self,scene,request,kind='train'):
        self.check_scene(scene)
        snapshot=snapshot_imports(self.bundle,self.database,scene,request.import_ids,
                                  {'train','calibration'} if kind=='train' else {'test'})
        binding=freeze_protocol(self.bundle,scene,snapshot) if kind=='train' else None
        if kind=='evaluate':
            with self.database.session() as session:
                model=self._run_row(session,scene,request.model_run_id)
                if model.kind!='train' or model.status!='succeeded':fail('TEMPORAL_MODEL_NOT_READY','候选模型尚未成功生成')
                expected=json.loads(model.report_json)['model'];model_snapshot=expected['snapshot']
                if any(model_snapshot[k]!=snapshot[k] for k in ('tenant_id','scene_id','feature_space_id','view_id','data_origin')):
                    fail('TEMPORAL_SCOPE_MISMATCH','test与候选模型作用域不一致')
                assert_evaluation_independent(snapshot, model_snapshot)
                binding=expected.get('research_protocol')
                assert_frozen_protocol(binding,snapshot,self.bundle,scene)
        body=request.model_dump();body['import_ids']=sorted(body['import_ids'])
        if binding is not None:body['_research_protocol']=binding
        knowledge=self._knowledge_hash(scene);code=engine.code_hash()
        key=content_hash({'kind':kind,'request':body,'snapshot':snapshot['hash'],
                          'config':self.bundle.config_hash,'knowledge':knowledge,'engine':code})
        with self.lock,self.database.session() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            existing=session.scalar(select(TemporalRun).where(TemporalRun.tenant_id==self.tenant,
                TemporalRun.scene_id==scene,TemporalRun.idempotency_key==key))
            if existing is not None:return {**public(existing),'created':False}
            self._heldout_available(session,binding)
            inserted=session.execute(insert(TemporalRun).values(id=new_id(),tenant_id=self.tenant,scene_id=scene,
                kind=kind,idempotency_key=key,config_hash=self.bundle.config_hash,knowledge_hash=knowledge,engine_hash=code,
                request_json=encode(body),snapshot_json=encode(snapshot),status='queued',created_at=utc_now(),updated_at=utc_now())
                .on_conflict_do_nothing(index_elements=['tenant_id','scene_id','idempotency_key']))
            row=session.scalar(select(TemporalRun).where(TemporalRun.tenant_id==self.tenant,TemporalRun.scene_id==scene,TemporalRun.idempotency_key==key))
            if inserted.rowcount:
                self._audit(session,row,'temporal.queued',{'kind':kind,'snapshot_hash':snapshot['hash']})
                if kind=='evaluate' and binding is not None:
                    self._audit(session,row,'temporal.test_reserved',{
                        **heldout_identity(binding['snapshot']['roster']),'protocol_hash':binding['hash']})
            result={**public(row),'created':bool(inserted.rowcount)}
        self.wake_event.set();return result

    def get(self,scene,identifier):
        self.check_scene(scene)
        with self.database.session() as session:return public(self._run_row(session,scene,identifier))
    def readiness(self,scene,request):
        from .readiness import check
        return check(self,scene,request)
    def protocol_preview(self,scene):
        self.check_scene(scene)
        return preview_protocol(self.bundle,scene)
    def dataset_template(self,scene,request):
        from .readiness import template
        return template(self,scene,request)
    def inputs(self,scene):
        self.check_scene(scene)
        fields=('split','source_hash','feature_space_id','view_id','data_origin','source_verification','eligible_rows')
        with self.database.session() as session:
            query=select(ImportJob.id,
                *[func.json_extract(ImportJob.manifest_json,'$.'+name).label(name) for name in fields])
            rows=session.execute(query.where(ImportJob.tenant_id==self.tenant,ImportJob.scene_id==scene,
                ImportJob.status=='succeeded').order_by(ImportJob.created_at.desc()).limit(200)).mappings()
            return {'items':[dict(row) for row in rows]}
    def list(self,scene):
        self.check_scene(scene)
        with self.database.session() as session:
            rows=session.scalars(select(TemporalRun).where(TemporalRun.tenant_id==self.tenant,TemporalRun.scene_id==scene)
                                 .order_by(TemporalRun.created_at.desc(),TemporalRun.id).limit(100))
            return {'items':[public(row,False) for row in rows]}
    def _current(self,row):
        return (row.config_hash==self.bundle.config_hash and row.engine_hash==engine.code_hash()
                and row.scene_id in self.bundle.scenes and row.knowledge_hash==self._knowledge_hash(row.scene_id))
    def retry(self,scene,identifier):
        self.check_scene(scene)
        with self.lock,self.database.session() as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row=self._run_row(session,scene,identifier)
            if not self._current(row):fail('TEMPORAL_CONFIG_CHANGED','配置、代码或知识已变化，请建立新研究任务')
            if row.status in {'blocked','failed'}:
                if row.kind=='evaluate' and self._test_consumed(session,identifier):
                    fail('TEMPORAL_TEST_ALREADY_CONSUMED','封存测试已经开始读取；失败后不得重试读取或据此修改候选')
                row.status='queued';row.error_json=None;row.report_json=None;row.updated_at=utc_now()
                self._audit(session,row,'temporal.retried')
            result=public(row)
        self.wake_event.set();return result

    def start(self):
        if self.settings is None:return
        import fcntl
        self.output_root.mkdir(parents=True,exist_ok=True)
        self.worker_lock=(self.output_root/'.worker.lock').open('a')
        try:fcntl.flock(self.worker_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.worker_lock.close();self.worker_lock=None;fail('TEMPORAL_WORKER_BUSY','研究目录已有工作进程')
        with self.database.session() as session:
            for row in session.scalars(select(TemporalRun).where(TemporalRun.tenant_id==self.tenant,TemporalRun.status.in_(['queued','running']))):
                row.status='queued' if self._current(row) else 'blocked'
                row.error_json=None if row.status=='queued' else encode({'code':'TEMPORAL_CONFIG_CHANGED','message':'重启时配置、代码或知识已变化'})
                if row.kind=='evaluate' and self._test_consumed(session,row.id):
                    row.status='blocked'
                    row.error_json=encode({'code':'TEMPORAL_TEST_ALREADY_CONSUMED','message':'封存测试已开始读取；进程中断后不再重读'})
                row.updated_at=utc_now();self._audit(session,row,'temporal.recovered',{'status':row.status})
        self.thread=Thread(target=self._loop,name='jianwei-temporal-worker',daemon=True);self.thread.start()
    def close(self):
        self.stop_event.set();self.wake_event.set()
        if self.thread:self.thread.join()
        if self.worker_lock:self.worker_lock.close();self.worker_lock=None
    def _loop(self):
        while not self.stop_event.is_set():
            try:
                with self.database.session() as session:
                    session.execute(text('BEGIN IMMEDIATE'))
                    row=session.scalar(select(TemporalRun).where(TemporalRun.tenant_id==self.tenant,TemporalRun.status=='queued').order_by(TemporalRun.created_at,TemporalRun.id).limit(1))
                    identifier=row.id if row else None
                    if row:
                        row.status='running';row.artifact_key=new_id();row.updated_at=utc_now()
                        self._audit(session,row,'temporal.started',{'attempt':row.artifact_key})
                if identifier:self._process(identifier);continue
            except Exception as error:logger.error('Temporal worker failed (%s)',type(error).__name__)
            self.wake_event.wait(self.settings.worker_poll_seconds);self.wake_event.clear()

    def _process(self,identifier):
        try:
            with self.database.session() as session:
                row=session.get(TemporalRun,identifier)
                if not self._current(row):fail('TEMPORAL_CONFIG_CHANGED','运行前配置、代码或知识已变化')
                request=json.loads(row.request_json);snapshot=json.loads(row.snapshot_json)
                scene,kind=row.scene_id,row.kind;directory=self.output_root/row.id/row.artifact_key
                config,knowledge=row.config_hash,row.knowledge_hash
                expected=None;model_directory=None
                if kind=='evaluate':
                    model=self._run_row(session,scene,request['model_run_id'])
                    if model.status!='succeeded':fail('TEMPORAL_MODEL_NOT_READY','候选模型不可用')
                    expected=json.loads(model.report_json)['model'];model_directory=self.output_root/model.id/model.artifact_key
            current=snapshot_imports(self.bundle,self.database,scene,request['import_ids'],{'train','calibration'} if kind=='train' else {'test'})
            if current!=snapshot:fail('TEMPORAL_SNAPSHOT_CHANGED','执行时数据快照已变化')
            binding=request.get('_research_protocol')
            if kind=='train':
                if freeze_protocol(self.bundle,scene,snapshot)!=binding:
                    fail('TEMPORAL_PROTOCOL_CHANGED','排队后研究协议或来源发生变化，停止读取特征')
                if binding is not None:
                    directory.mkdir(parents=True,exist_ok=True)
                    engine.atomic_json(directory/'protocol.json',binding)
            else:
                assert_frozen_protocol(binding,snapshot,self.bundle,scene)
                if expected.get('research_protocol')!=binding:
                    fail('TEMPORAL_PROTOCOL_CHANGED','评估任务与候选模型的冻结协议不一致')
                if binding is not None:
                    engine.load_model(model_directory,expected)
                    self._consume_test(identifier,binding)
            sequences=load_sequences(self.bundle,self.database,self.imports.output_root,snapshot)
            if kind=='train':report=engine.train(sequences,snapshot,self.bundle.config.learning,self.settings,directory,config,knowledge,research_protocol=binding)
            else:report=engine.evaluate(sequences,snapshot,model_directory,expected,directory)
            engine.atomic_json(directory/'report.json',report)
            with self.database.session() as session:
                row=session.get(TemporalRun,identifier);row.report_json=encode(report);row.status='succeeded';row.error_json=None;row.updated_at=utc_now()
                self._audit(session,row,'temporal.succeeded',{'kind':kind,'snapshot_hash':snapshot['hash'],'activated':False})
        except ContractError as error:self._fail(identifier,error.detail(),'blocked')
        except Exception as error:
            logger.error('Temporal run %s failed (%s)',identifier,type(error).__name__)
            self._fail(identifier,{'code':'TEMPORAL_FAILED','message':'研究执行失败，请检查数据与数值计算日志'},'failed')
    def _fail(self,identifier,detail,status):
        with self.database.session() as session:
            row=session.get(TemporalRun,identifier);row.status=status;row.error_json=encode(detail);row.updated_at=utc_now()
            self._audit(session,row,'temporal.'+status,{'code':detail['code']})
    def artifact(self,scene,identifier,name):
        self.check_scene(scene)
        allowed={'weights.npz','model.json','report.json','protocol.json','calibration-predictions.jsonl','test-predictions.jsonl','calibration-next-cluster.jsonl','test-next-cluster.jsonl'}
        if name not in allowed:fail('TEMPORAL_ARTIFACT_NOT_FOUND','研究产物不存在')
        with self.database.session() as session:
            row=self._run_row(session,scene,identifier)
            if row.status!='succeeded' or not row.artifact_key:fail('TEMPORAL_ARTIFACT_NOT_FOUND','研究产物尚不可用')
            path=self.output_root/row.id/row.artifact_key/name
            if not path.is_file():fail('TEMPORAL_ARTIFACT_NOT_FOUND','研究产物不存在')
            return path
