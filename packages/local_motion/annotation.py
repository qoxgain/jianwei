"""B.1.1 source-only annotation logs. No algorithm can create human events here."""
from datetime import datetime
from typing import Literal
from pydantic import Field,field_validator
from packages.contracts.models import Strict
from packages.config.errors import ContractError
from packages.knowledge.service import content_hash

SHA=r'^[a-f0-9]{64}$'


class AnnotationEvent(Strict):
    id:str=Field(min_length=1,max_length=100)
    at:str
    actor:str=Field(min_length=1,max_length=100)
    op:Literal['boundary_add','boundary_update','boundary_delete','boundary_review','interval_add','interval_update','interval_delete','exposure','attest']
    target_id:str|None=None
    pts_ms:int|None=None
    end_ms:int|None=None
    kind:str|None=None
    note:str=Field(default='',max_length=1000)

    @field_validator('at')
    @classmethod
    def timestamp(cls,value):
        if datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is None:raise ValueError('Timezone required')
        return value


class AnnotationDocument(Strict):
    schema_version:Literal['b11-annotation-v1']=Field(alias='schema')
    annotation_id:str=Field(min_length=1,max_length=100)
    source_sha256:str=Field(pattern=SHA)
    descriptor_hash:str=Field(pattern=SHA)
    annotator:str=Field(min_length=1,max_length=100)
    mode:Literal['blind','assisted']
    evidence_class:Literal['synthetic_engineering','development_exploration']
    start_ms:int=Field(ge=0)
    end_ms:int=Field(gt=0)
    created_at:str
    revision:int=Field(ge=0)
    events:list[AnnotationEvent]=Field(max_length=20000)
    status:Literal['draft','sealed']
    coverage_complete:bool
    human_attestation:bool
    sealed_at:str|None
    sealed_hash:str|None
    parent_sealed_hash:str|None=Field(default=None,pattern=SHA)

    _timestamp=field_validator('created_at')(AnnotationEvent.timestamp.__func__)


def union(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    return merged


def subtract(start,end,excluded):
    result=[];cursor=start
    for left,right in union(excluded):
        if left>cursor:result.append((cursor,min(left,end)))
        cursor=max(cursor,right)
    if cursor<end:result.append((cursor,end))
    return [(a,b) for a,b in result if b>a]


def validate_source(source):
    if source.get('schema')!='b11-annotation-source-v1':raise ContractError('ANNOTATION_SOURCE','Unsupported source descriptor')
    identity={k:v for k,v in source.items() if k!='descriptor_hash'}
    if content_hash(identity)!=source.get('descriptor_hash'):raise ContractError('ANNOTATION_SOURCE_CHANGED','源描述哈希不符')
    frames=source.get('frames',[]);s=source['source']
    if not frames or len(frames)>500000:raise ContractError('ANNOTATION_FRAME_TABLE','Empty or oversized frame table')
    for i,f in enumerate(frames):
        if f['index']!=i or not (type(f['pts_ms']) is int and type(f['end_ms']) is int and f['pts_ms']<f['end_ms']):raise ContractError('ANNOTATION_FRAME_TABLE','Invalid original PTS')
        if i and frames[i-1]['end_ms']!=f['pts_ms']:raise ContractError('ANNOTATION_FRAME_TABLE','PTS gaps require explicit separate source domains')
    if frames[0]['pts_ms']!=s['first_pts_ms'] or frames[-1]['end_ms']!=s['last_pts_end_ms']:raise ContractError('ANNOTATION_FRAME_TABLE','Frame endpoints differ from source')
    return source


def new_document(source,annotator='待真人填写'):
    """Empty draft only. It contains no truth and cannot produce real metrics."""
    from uuid import uuid4
    now=datetime.now().astimezone().isoformat();validate_source(source);s=source['source']
    return {'schema':'b11-annotation-v1','annotation_id':str(uuid4()),'source_sha256':s['sha256'],'descriptor_hash':source['descriptor_hash'],
        'annotator':annotator,'mode':'blind','evidence_class':source['evidence_class'],'start_ms':s['first_pts_ms'],'end_ms':s['last_pts_end_ms'],
        'created_at':now,'revision':0,'events':[],'status':'draft','coverage_complete':False,'human_attestation':False,'sealed_at':None,'sealed_hash':None}


def compile_annotations(source,document):
    validate_source(source);d=AnnotationDocument.model_validate(document);s=source['source']
    if (d.source_sha256,d.descriptor_hash,d.evidence_class)!=(s['sha256'],source['descriptor_hash'],source['evidence_class']):raise ContractError('ANNOTATION_BINDING','标注与视频/PTS版本不匹配')
    if (d.start_ms,d.end_ms)!=(s['first_pts_ms'],s['last_pts_end_ms']):raise ContractError('ANNOTATION_SCOPE','必须保留完整原片范围，包括未产生预测的尾部')
    if d.revision!=len(d.events):raise ContractError('ANNOTATION_REVISION','Revision must equal append-only operation count')
    boundaries={};intervals={};seen=set();exposed=d.mode=='assisted';last_time=d.created_at
    for e in d.events:
        if e.id in seen:raise ContractError('ANNOTATION_DUPLICATE','Duplicate operation ID')
        seen.add(e.id)
        if datetime.fromisoformat(e.at.replace('Z','+00:00'))<datetime.fromisoformat(last_time.replace('Z','+00:00')):raise ContractError('ANNOTATION_TIME','Operation time went backwards')
        last_time=e.at
        if e.op=='exposure':exposed=True;continue
        if e.op=='attest':continue
        if e.pts_ms is not None and not d.start_ms<=e.pts_ms<=d.end_ms:raise ContractError('ANNOTATION_TIME','Boundary outside complete source scope')
        if e.op.startswith('boundary_'):
            if e.op=='boundary_add':
                if e.pts_ms is None or e.kind not in {'onset','offset','pause','switch'}:raise ContractError('ANNOTATION_BOUNDARY','Boundary needs PTS and an explicit human type')
                boundaries[e.id]={'id':e.id,'pts_ms':e.pts_ms,'kind':e.kind,'note':e.note,'reviewed_by':[]}
            else:
                if e.target_id not in boundaries:raise ContractError('ANNOTATION_TARGET','Unknown/deleted human boundary')
                if e.op=='boundary_delete':boundaries.pop(e.target_id)
                elif e.op=='boundary_review':boundaries[e.target_id]['reviewed_by'].append({'actor':e.actor,'at':e.at,'note':e.note})
                else:
                    if e.pts_ms is None or e.kind not in {'onset','offset','pause','switch'}:raise ContractError('ANNOTATION_BOUNDARY','Revision needs PTS and type')
                    boundaries[e.target_id].update(pts_ms=e.pts_ms,kind=e.kind,note=e.note,reviewed_by=[])
        elif e.op.startswith('interval_'):
            if e.op=='interval_delete':
                if e.target_id not in intervals:raise ContractError('ANNOTATION_TARGET','Unknown human interval')
                intervals.pop(e.target_id);continue
            if e.pts_ms is None or e.end_ms is None or not d.start_ms<=e.pts_ms<e.end_ms<=d.end_ms or e.kind not in {'reviewed','uncertain','occluded','lighting','no_action','background_invalid'}:raise ContractError('ANNOTATION_INTERVAL','Invalid human coverage/interference interval')
            key=e.id if e.op=='interval_add' else e.target_id
            if e.op=='interval_update' and key not in intervals:raise ContractError('ANNOTATION_TARGET','Unknown human interval')
            intervals[key]={'id':key,'start_ms':e.pts_ms,'end_ms':e.end_ms,'kind':e.kind,'note':e.note}
    if exposed and d.mode=='blind':raise ContractError('ANNOTATION_EXPOSURE','看过预测后不能继续声称盲标；须标记assisted')
    reviewed=union([(v['start_ms'],v['end_ms']) for v in intervals.values() if v['kind']=='reviewed'])
    missing=subtract(d.start_ms,d.end_ms,reviewed);excluded=union([(v['start_ms'],v['end_ms']) for v in intervals.values() if v['kind'] in {'uncertain','occluded'}])
    valid=subtract(d.start_ms,d.end_ms,excluded);duration=d.end_ms-d.start_ms
    if d.status=='sealed':
        if d.sealed_at is None:raise ContractError('ANNOTATION_SEAL','Missing seal timestamp')
        seal_time=AnnotationEvent.timestamp(d.sealed_at)
        if datetime.fromisoformat(seal_time.replace('Z','+00:00'))<datetime.fromisoformat(last_time.replace('Z','+00:00')):raise ContractError('ANNOTATION_SEAL','Seal predates last operation')
        if content_hash({k:v for k,v in document.items() if k!='sealed_hash'})!=d.sealed_hash:raise ContractError('ANNOTATION_SEAL','Sealed revision was modified')
    eligible=d.status=='sealed' and d.coverage_complete and not missing and bool(valid) and d.human_attestation
    return {'annotation_id':d.annotation_id,'annotation_hash':content_hash(document),'sealed_hash':d.sealed_hash,'revision':d.revision,
        'annotator':d.annotator,'annotation_mode':d.mode,'status':d.status,'evidence_class':d.evidence_class,
        'boundaries':sorted(boundaries.values(),key=lambda v:(v['pts_ms'],v['id'])),'intervals':list(intervals.values()),
        'reviewed':reviewed,'unreviewed':missing,'excluded':excluded,'valid_intervals':valid,
        'coverage_ratio':1-sum(b-a for a,b in missing)/duration,'uncertain_or_occluded_ratio':sum(b-a for a,b in excluded)/duration,
        'eligible_for_evaluation':eligible,'reason':None if eligible else 'REQUIRES_SEALED_COMPLETE_HUMAN_REVIEW',
        'audit_log_is_self_reported':True,'independent_generalization':False}
