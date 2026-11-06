"""Read-only B.1 cache adapter. No visual feature module is reimplemented."""
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np
from packages.config.loader import resolve_path
from packages.config.errors import ContractError
from packages.local_motion.extraction import admission,sha,OFFICIAL_SHA
from packages.knowledge.service import content_hash


@dataclass
class Stream:
    name:str
    values:np.ndarray
    starts:np.ndarray
    ends:np.ndarray
    activity:np.ndarray
    variants:dict
    contract:dict
    background:bool=False


def load_features(bundle):
    s=bundle.config.unlabeled
    if s is None or not s.enabled:raise ContractError('UNLABELED_DISABLED','须在system.yaml显式启用零标签开发研究')
    # Reuse the registered-source/sealed-root guard BEFORE touching features.
    path=admission(bundle)
    manifest_path=resolve_path(bundle.root,s.feature_manifest);m=json.loads(manifest_path.read_text());lm=bundle.config.local_motion
    if m.get('schema')!='b1-local-motion-features-v1' or m['source']['sha256']!=lm.source_sha256 or m['evidence_class']!=lm.evidence_class:raise ContractError('UNLABELED_SOURCE','缓存必须绑定同一已核验开发来源')
    if m['recipe']['model_sha256']!=OFFICIAL_SHA or m['source']['bytes']!=path.stat().st_size:raise ContractError('UNLABELED_ENCODER','缓存编码器或源字节数不符')
    recipe_payload={k:v for k,v in m['recipe'].items() if k!='sha256'}
    if m['recipe_hash']!=m['recipe']['sha256'] or content_hash(recipe_payload)!=m['recipe_hash'] or m['settings']!=m['recipe']['settings'] or m['settings']!=lm.model_dump(mode='json'):
        raise ContractError('UNLABELED_RECIPE','缓存生产配方/ROI与配置不一致，不把旧特征改标为新生产版本')
    vectors_path=manifest_path.parent/'features.npz'
    import zipfile
    with zipfile.ZipFile(vectors_path) as archive:
        if sum(f.file_size for f in archive.infolist())>bundle.config.ingestion.max_expanded_bytes:raise ContractError('UNLABELED_EXPANSION','特征展开超出统一内存预算')
    if sha(vectors_path)!=m['features_sha256']:raise ContractError('UNLABELED_CACHE_CHANGED','旧特征哈希不符；禁止静默重提或重标版本')
    with np.load(vectors_path,allow_pickle=False) as arrays:vectors=arrays['features'].copy()
    if vectors.shape!=(len(m['rows']),512):raise ContractError('UNLABELED_SHAPE','缓存形状与PTS清单不符')
    rows=[r for r in m['rows'] if r['window_ms']==s.window_ms and r['fps']==s.sample_fps]
    by_roi={roi['id']:sorted((r for r in rows if r['roi']==roi['id']),key=lambda r:r['start_ms']) for roi in m['settings']['rois']}
    ids={r['role']:r['id'] for r in m['settings']['rois'] if r['role'] in {'global','hands','interaction','background'}}
    base=by_roi[ids['global']]
    if not base or len(base)>s.max_profile_windows:raise ContractError('UNLABELED_BUDGET','原臂缺失或超过明确预算，未静默抽掉来源/窗口')
    starts=np.array([r['start_ms'] for r in base]);ends=np.array([r['end_ms'] for r in base])
    if len(starts)<16 or np.any(np.diff(starts)!=m['settings']['stride_ms']):raise ContractError('UNLABELED_PTS','当前有界研究需要连续原始窗口；缺口须独立域处理')
    for group in by_roi.values():
        if len(group)!=len(base) or any((r['start_ms'],r['end_ms'])!=(a,b) for r,a,b in zip(group,starts,ends)):raise ContractError('UNLABELED_ALIGNMENT','ROI窗口不完整或不对齐')
    def scalar(roi,method):return np.array([r.get(method,np.nan) if r['status']=='valid' else np.nan for r in by_roi[roi]],dtype=float)[:,None]
    def deep(roi):return np.array([vectors[r['index']] if r.get('r3d_computed') and r['status']=='valid' else np.full(512,np.nan) for r in by_roi[roi]],dtype=float)
    h,i,bg=ids['hands'],ids['interaction'],ids['background'];activity=scalar(h,'frame_difference')[:,0]
    hf,hi,hb,ib=scalar(h,'frame_difference'),scalar(i,'frame_difference'),scalar(h,'block_motion'),scalar(i,'block_motion')
    bf,bb=scalar(bg,'frame_difference'),scalar(bg,'block_motion');rv={k:deep(v) for k,v in ids.items()}
    definitions={
        'global_r3d':([rv['global']],[rv['background']]),'hands_r3d':([rv['hands']],[rv['background']]),
        'interaction_r3d':([rv['interaction']],[rv['background']]),'local_r3d':([rv['hands'],rv['interaction']],[rv['background'],rv['background']]),
        'frame_difference':([hf,hi],[bf,bf]),'block_motion':([hb,ib],[bb,bb]),'hands_motion':([hf,hb],[bf,bb]),
        'motion_fusion':([hf,hi,hb,ib],[bf,bf,bb,bb]),
        'local_fusion':([rv['hands'],rv['interaction'],hf,hi,hb,ib],[rv['background'],rv['background'],bf,bf,bb,bb]),
        'background_motion':([bf,bb],[bf,bb]),'background_r3d':([rv['background']],[rv['background']])}
    streams=[];missing=[]
    for name,(parts,background) in definitions.items():
        values=np.concatenate(parts,axis=1)
        if not np.isfinite(values).all() or not np.isfinite(activity).all():
            missing.append({'method':name,'status':'unavailable','reason':'R3D_NOT_EXECUTED_OR_INVALID_WINDOWS; not filled with zeros'});continue
        variants={'background_mix':{'values':(1-s.background_mix)*values+s.background_mix*np.concatenate(background,axis=1),
            'activity':(1-s.background_mix)*activity+s.background_mix*bf[:,0]}}
        if name in {'hands_motion','frame_difference','block_motion','motion_fusion'}:
            for side in ['left','right']:
                key='hands_'+side+'_shift'
                if key in by_roi:
                    altered=values.copy();fd=scalar(key,'frame_difference')[:,0];bm=scalar(key,'block_motion')[:,0]
                    if name=='hands_motion':altered[:,0]=fd;altered[:,1]=bm
                    elif name=='frame_difference':altered[:,0]=fd
                    elif name=='block_motion':altered[:,0]=bm
                    else:altered[:,0]=fd;altered[:,2]=bm
                    if np.isfinite(altered).all():variants['roi_'+side]={'values':altered,'activity':fd.copy()}
        contract={'encoder_sha256':m['recipe']['model_sha256'],'preprocessing':m['recipe']['preprocessing'],
            'production_snapshot_sha256':m['recipe']['production']['sha256'],'producer_source_files':m['recipe']['source_files'],
            'rois':[{k:r[k] for k in ['id','role','xyxy','encode_r3d']} for r in m['settings']['rois'] if r['role']!='offset_control'],
            'method':name,'dimension':values.shape[1],'window_ms':s.window_ms,'sample_fps':s.sample_fps,'stride_ms':m['settings']['stride_ms']}
        streams.append(Stream(name,values,starts.copy(),ends.copy(),activity.copy(),variants,contract,name.startswith('background')))
    if not streams:raise ContractError('UNLABELED_EMPTY','没有可用的实际特征流')
    return m,streams,missing


def time_roles(stream,settings):
    start,end=int(stream.starts[0]),int(stream.ends[-1]);fit_end=start+(end-start)*settings.fit_fraction
    cal_end=fit_end+(end-start)*settings.calibration_fraction
    roles={'fit':np.where(stream.ends<=fit_end)[0],
        'calibration':np.where((stream.starts>=fit_end+settings.purge_ms)&(stream.ends<=cal_end))[0],
        'replay':np.where(stream.starts>=cal_end+settings.purge_ms)[0]}
    if len(roles['fit'])<max(settings.motif_lengths)+settings.min_repeats or len(roles['calibration'])<settings.forecast_history+max(settings.forecast_lags)+1 or len(roles['replay'])<settings.forecast_history+max(settings.forecast_lags)+1:
        raise ContractError('UNLABELED_TOO_SHORT','清除共享帧后时间不足；未把重叠片段冒充独立样本')
    return roles


def cross_source_admission(parent_source,query_source,settings,contract,parent_contract):
    evidence={e.source_sha256:e for e in settings.source_evidence};a=evidence.get(parent_source['sha256']);b=evidence.get(query_source['sha256'])
    if not a or not b or any(e.independence_review!='verified' or e.near_duplicate_review!='clear' or not e.capture_session_id or not e.view_id or not e.captured_at for e in [a,b]):
        raise ContractError('UNLABELED_INDEPENDENCE_UNKNOWN','独立来源/会话/近重复/采集顺序未确认；文件名或SHA不同不能替代来源证据')
    if a.root_source_sha256==b.root_source_sha256 or a.capture_session_id==b.capture_session_id:raise ContractError('UNLABELED_SAME_ROOT','同根/同会话不能冒充独立来源')
    if a.kind!=b.kind:raise ContractError('UNLABELED_ORIGIN_CHANGED','生成与现场来源不可混作独立场景证据')
    from datetime import datetime
    first=datetime.fromisoformat(a.captured_at.replace('Z','+00:00'));second=datetime.fromisoformat(b.captured_at.replace('Z','+00:00'))
    if first.tzinfo is None or second.tzinfo is None or first>=second:raise ContractError('UNLABELED_SOURCE_ORDER','缺少已知的先拟合、后查询时间顺序')
    if a.view_id!=b.view_id or content_hash(contract)!=content_hash(parent_contract):raise ContractError('UNLABELED_SPACE_CHANGED','机位/ROI/特征生产空间不兼容，不静默迁移原型')
    return {'independence':'declared_verified','kind':a.kind,'same_view':True,'query_updates_model':False}
