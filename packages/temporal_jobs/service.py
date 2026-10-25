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

    def enqueue(self,scene,request,kind='train'):
        self.check_scene(scene)
        snapshot=snapshot_imports(self.bundle,self.database,scene,request.import_ids,
                                  {'train','calibration'} if kind=='train' else {'test'})
        if kind=='evaluate':
            with self.database.session() as session:
                model=self._run_row(session,scene,request.model_run_id)
                if model.kind!='train' or model.status!='succeeded':fail('TEMPORAL_MODEL_NOT_READY','候选模型尚未成功生成')
                expected=json.loads(model.report_json)['model'];model_snapshot=expected['snapshot']
                if any(model_snapshot[k]!=snapshot[k] for k in ('tenant_id','scene_id','feature_space_id','view_id','data_origin')):
                    fail('TEMPORAL_SCOPE_MISMATCH','test与候选模型作用域不一致')
                assert_evaluation_independent(snapshot, model_snapshot)
        body=request.model_dump();body['import_ids']=sorted(body['import_ids'])
        knowledge=self._knowledge_hash(scene);code=engine.code_hash()
        key=content_hash({'kind':kind,'request':body,'snapshot':snapshot['hash'],
                          'config':self.bundle.config_hash,'knowledge':knowledge,'engine':code})
        with self.lock,self.database.session() as session:
            inserted=session.execute(insert(TemporalRun).values(id=new_id(),tenant_id=self.tenant,scene_id=scene,
                kind=kind,idempotency_key=key,config_hash=self.bundle.config_hash,knowledge_hash=knowledge,engine_hash=code,
                request_json=encode(body),snapshot_json=encode(snapshot),status='queued',created_at=utc_now(),updated_at=utc_now())
                .on_conflict_do_nothing(index_elements=['tenant_id','scene_id','idempotency_key']))
            row=session.scalar(select(TemporalRun).where(TemporalRun.tenant_id==self.tenant,TemporalRun.scene_id==scene,TemporalRun.idempotency_key==key))
            if inserted.rowcount:self._audit(session,row,'temporal.queued',{'kind':kind,'snapshot_hash':snapshot['hash']})
            result={**public(row),'created':bool(inserted.rowcount)}
        self.wake_event.set();return result

    def get(self,scene,identifier):
        self.check_scene(scene)
        with self.database.session() as session:return public(self._run_row(session,scene,identifier))
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
            row=self._run_row(session,scene,identifier)
            if not self._current(row):fail('TEMPORAL_CONFIG_CHANGED','配置、代码或知识已变化，请建立新研究任务')
            if row.status in {'blocked','failed'}:
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
            sequences=load_sequences(self.bundle,self.database,self.imports.output_root,snapshot)
            if kind=='train':report=engine.train(sequences,snapshot,self.bundle.config.learning,self.settings,directory,config,knowledge)
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
        allowed={'weights.npz','model.json','report.json','calibration-predictions.jsonl','test-predictions.jsonl','calibration-next-cluster.jsonl','test-next-cluster.jsonl'}
        if name not in allowed:fail('TEMPORAL_ARTIFACT_NOT_FOUND','研究产物不存在')
        with self.database.session() as session:
            row=self._run_row(session,scene,identifier)
            if row.status!='succeeded' or not row.artifact_key:fail('TEMPORAL_ARTIFACT_NOT_FOUND','研究产物尚不可用')
            path=self.output_root/row.id/row.artifact_key/name
            if not path.is_file():fail('TEMPORAL_ARTIFACT_NOT_FOUND','研究产物不存在')
            return path
