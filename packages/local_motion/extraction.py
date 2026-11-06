"""Allowlisted development inputs, original PTS, frozen ROI features; file-only."""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,subprocess,time
import numpy as np
from packages.config.errors import ContractError
from packages.config.loader import PROJECT_ROOT,resolve_path
from packages.encoders.onnx_cpu import load_encoder
from packages.encoders.pretrained import clip_tensor,PREPROCESSING_VERSION
from packages.ingestion.video import decode_video,close_frames,video_geometry,_run
from packages.ingestion.recipe import production_snapshot
from packages.knowledge.service import content_hash
from .motion import crop,descriptors

VERSION='p037-b1-local-motion-v1'
OFFICIAL_SHA='2979019509005056778db9f462e9eed212c40f02d5e5bf1d6fc9e06caf2ebab8'


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write(path,value):
    path=Path(path);temp=path.with_suffix('.writing');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n');temp.replace(path)


def admission(bundle):
    s=getattr(bundle.config,'local_motion',None)
    if s is None or not s.enabled:raise ContractError('MOTION_DISABLED','须在system.yaml显式启用局部动态探索')
    source=next((v for v in bundle.profile.sources if v.id==s.source_id),None)
    if source is None or source.type!='file':raise ContractError('MOTION_SOURCE_INVALID','仅支持已注册本地开发文件')
    path=resolve_path(bundle.root,source.uri)
    registry=bundle.config.temporal.dataset_manifest if bundle.config.temporal else None
    if registry and any(v.split=='test' and s.source_sha256 in {v.source_sha256,v.root_source_sha256,v.parent_source_sha256} for v in registry.sources):raise ContractError('MOTION_SEALED_FORBIDDEN','封存及同源test不能用于B.1探索')
    if s.evidence_class=='development_exploration':
        catalog=json.loads((PROJECT_ROOT/'datasets/video_samples/catalog.json').read_text())
        asset=next((a for a in catalog['assets'] if a['kind']=='video' and a['asset_id']==s.source_id),None)
        if not asset or asset['provenance']['split']!='unassigned' or asset['sha256']!=s.source_sha256 or path!=(PROJECT_ROOT/asset['path']).resolve():raise ContractError('MOTION_DEVELOPMENT_ONLY','B.1仅允许明确列入开发目录的原始来源')
    else:
        receipt=path.with_suffix('.generated.json')
        if not path.is_relative_to(PROJECT_ROOT/'artifacts') or not receipt.is_file():raise ContractError('MOTION_SYNTHETIC_RECEIPT_REQUIRED','合成素材必须由本地生成器输出并保留回执')
        record=json.loads(receipt.read_text())
        if record.get('kind') not in {'b1-generated-rectangles-v1','b2u-generated-motifs-v1'} or record.get('sha256')!=s.source_sha256:raise ContractError('MOTION_SYNTHETIC_RECEIPT_REQUIRED','合成素材回执与配置不匹配')
    if not path.is_file():raise ContractError('MOTION_SOURCE_MISSING','原视频不可用；不能生成真实实验指标')
    if path.stat().st_size>bundle.config.ingestion.max_upload_bytes:raise ContractError('MOTION_SOURCE_TOO_LARGE','原视频超过统一接入预算')
    if sha(path)!=s.source_sha256:raise ContractError('MOTION_SOURCE_CHANGED','视频哈希与已授权开发来源不一致')
    return path


def geometry_check(path,ingestion,settings):
    args=['ffprobe','-v','error','-protocol_whitelist','file,pipe','-select_streams','v:0']
    data=json.loads(_run([*args,'-show_streams','-of','json',str(path)],ingestion.probe_timeout_seconds));stream=data['streams'][0]
    g=video_geometry(stream,max(ingestion.decode_width,ingestion.encoder_input_size))
    # Expected original display dimensions, not bounded decoder dimensions.
    sar=g['sample_aspect_ratio'];sar_value=float(sar[0]/sar[1]) if isinstance(sar,list) else float(__import__('fractions').Fraction(str(sar).replace(':','/')))
    w,h=round(int(stream['width'])*sar_value),int(stream['height'])
    if g['rotation_degrees_ccw'] in {-90,90}:w,h=h,w
    if (w,h)!=(settings.display_width,settings.display_height):raise ContractError('MOTION_DIMENSION_MISMATCH','ROI登记显示尺寸与实际视频不一致')
    frames=json.loads(_run([*args,'-read_intervals',f'%+#{ingestion.max_frames+1}','-show_frames','-show_entries','frame=width,height','-of','json',str(path)],ingestion.probe_timeout_seconds))['frames']
    if not 2<=len(frames)<=ingestion.max_frames:raise ContractError('MOTION_FRAME_BUDGET','原视频帧数超过统一预算或不足')
    if any((f.get('width'),f.get('height'))!=(int(stream['width']),int(stream['height'])) for f in frames):raise ContractError('MOTION_DIMENSIONS_CHANGED','视频运行中尺寸变化，必须分段重新登记ROI')
    return g


def recipe(bundle):
    production=production_snapshot();paths=[PROJECT_ROOT/'packages/local_motion'/name for name in ('__init__.py','extraction.py','motion.py')]
    payload={'version':VERSION,'settings':bundle.config.local_motion.model_dump(mode='json'),'config_hash':bundle.config_hash,
        'production':production,'source_files':{str(p.relative_to(PROJECT_ROOT)):sha(p) for p in paths},
        'sampling':'original_PTS_nearest_then_nearest_repeat_to_16_v1','motion':'gray_nearest_block_translation_v1',
        'model_sha256':bundle.config.encoder.weights_sha256,'preprocessing':bundle.config.encoder.preprocessing_version}
    return {**payload,'sha256':content_hash(payload)}


def sample_indices(pts,start,window,fps,tolerance):
    targets=start+np.arange(window*fps//1000)*1000/fps
    right=np.clip(np.searchsorted(pts,targets),0,len(pts)-1);left=np.maximum(0,right-1)
    indices=np.where(np.abs(pts[left]-targets)<=np.abs(pts[right]-targets),left,right)
    if len(np.unique(indices))!=len(indices) or np.max(np.abs(pts[indices]-targets))>tolerance:raise ContractError('MOTION_PTS_COVERAGE','原始时间轴无法覆盖指定采样密度')
    return indices


def extract(bundle,output,progress=None):
    output=Path(output).resolve()
    if not output.is_relative_to(PROJECT_ROOT/'artifacts'):raise ValueError('Private artifacts output required')
    output.mkdir(parents=True,exist_ok=False);s=getattr(bundle.config,'local_motion',None);pixels=None;started=time.monotonic()
    try:
        path=admission(bundle);geometry_check(path,bundle.config.ingestion,s);before=recipe(bundle)
        if bundle.config.encoder.adapter!='onnx' or not bundle.config.encoder.frozen or bundle.config.encoder.weights_sha256!=OFFICIAL_SHA or bundle.config.encoder.preprocessing_version!=PREPROCESSING_VERSION:raise ContractError('MOTION_ENCODER_REQUIRED','必须配置已核验的冻结官方R3D-18；不回退诊断编码器')
        encoder=load_encoder(resolve_path(bundle.root,bundle.profile.encoder_weights),OFFICIAL_SHA,512,112,bundle.config.ingestion.encoder_threads)
        pixels,pts,durations,quality,quality_report,metadata=decode_video(path,output,bundle.config.ingestion)
        if s.start_ms<int(pts[0]) or s.end_ms>int(pts[-1]+durations[-1]):raise ContractError('MOTION_TIME_RANGE','实验时间范围超出真实PTS')
        starts=list(range(s.start_ms,s.end_ms-max(s.window_ms)+1,s.stride_ms))
        if not starts or len(starts)>s.max_windows:raise ContractError('MOTION_WINDOW_BUDGET','无有效窗口或窗口数量超过预算')
        arms=[(w,f) for w in s.window_ms for f in s.sample_fps];rows=[];features=[];statuses=[]
        total_rows=len(starts)*len(arms)*len(s.rois)
        if total_rows>bundle.config.ingestion.max_rows or total_rows*512*4*2>bundle.config.ingestion.max_expanded_bytes:
            raise ContractError('MOTION_FEATURE_BUDGET','特征矩阵及副本超过统一内存/行预算')
        for start in starts:
            for window,fps in arms:
                indices=sample_indices(pts,start,window,fps,s.max_pts_error_ms);frames=pixels[indices]
                repeated=np.rint(np.linspace(0,len(indices)-1,16)).astype(int)
                for roi in s.rois:
                    index=len(rows);record={'index':index,'roi':roi.id,'role':roi.role,'start_ms':start,'end_ms':start+window,
                        'center_ms':start+window//2,'window_ms':window,'fps':fps,'source_frame_indices':indices.tolist(),
                        'observed_pts_ms':pts[indices].tolist(),'encoder_frame_indices':indices[repeated].tolist(),
                        'visibility':roi.visibility,'r3d_computed':False,'status':'valid'}
                    excluded=next((e for e in roi.exclusions if e.start_ms<start+window and e.end_ms>start),None)
                    reason='occluded' if roi.visibility=='occluded' else excluded.reason if excluded else 'invalid_frame' if float(quality[indices].mean())<s.min_valid_fraction else None
                    if reason:
                        record.update(status=reason);feature=np.full(512,np.nan,dtype=np.float32)
                    else:
                        roi_frames=crop(frames,roi.xyxy);record.update(descriptors(roi_frames,pts[indices],s))
                        if roi.encode_r3d:
                            feature=encoder.encode(clip_tensor(frames,repeated,roi.xyxy,112),deadline=started+bundle.config.ingestion.encode_timeout_seconds)[0];record['r3d_computed']=True
                        else:feature=np.full(512,np.nan,dtype=np.float32)
                    rows.append(record);features.append(feature);statuses.append(reason is None)
            if progress:progress(len(rows),len(starts)*len(arms)*len(s.rois))
        after=recipe(bundle)
        if before['sha256']!=after['sha256'] or sha(path)!=s.source_sha256:raise ContractError('MOTION_PRODUCTION_CHANGED','来源或生产依赖在执行期间发生变化；本次特征不可复用')
        if not any(statuses):
            write(output/'invalid-windows.json',{'rows':rows,'source_sha256':s.source_sha256})
            raise ContractError('MOTION_NO_VALID_WINDOWS','所有窗口无效或遮挡；不能形成有效研究特征')
        matrix=np.asarray(features,dtype=np.float32);np.savez_compressed(output/'features.npz',features=matrix,valid=np.array(statuses,dtype=bool),r3d_valid=np.array([r['r3d_computed'] for r in rows],dtype=bool))
        manifest={'schema':'b1-local-motion-features-v1','evidence_class':s.evidence_class,'source':{'asset_id':s.source_id,'sha256':s.source_sha256,
            'bytes':path.stat().st_size,'width':s.display_width,'height':s.display_height,'first_pts_ms':int(pts[0]),'last_pts_end_ms':int(pts[-1]+durations[-1])},
            'settings':s.model_dump(mode='json'),'recipe':before,'recipe_hash':before['sha256'],'encoder':'frozen_official_r3d18_512',
            'rows':rows,'features_sha256':sha(output/'features.npz'),'quality':quality_report,'geometry':metadata['geometry'],
            'elapsed_seconds':time.monotonic()-started,'database_accessed':False,'formal_test_data_accessed':False,'b2_started':False}
        write(output/'manifest.json',manifest);return manifest
    except BaseException as error:
        write(output/'failure.json',{'status':'failed','code':getattr(error,'code','MOTION_EXTRACTION_FAILED'),'type':type(error).__name__,
            'elapsed_seconds':time.monotonic()-started,'partial_features_reusable':False});raise
    finally:
        if pixels is not None:close_frames(pixels)
