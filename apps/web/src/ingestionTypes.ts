import type {Scene} from './sceneTypes';
export type ImportFormat='mp4'|'npz';
export type ImportStatus='queued'|'running'|'succeeded'|'blocked'|'failed';
export type ImportEpisode={episode_id:string;domain_index:number;start_ms:number;end_ms:number};
export type ClockAnchor={domain_index:number;pts_ms:number;capture_utc:string};
export type FeatureSpace={contract_version:'3.1';encoder_sha:string;preprocessing_sha:string;sampling_sha:string;roi_policy_hash:string;pooling:string;dimension:number};
export type Acquisition={capture_session_id:string|null;captured_at:string|null;review_status:'pending'|'confirmed'};
export type ViewInterval={domain_index:number;start_ms:number;end_ms:number;status:'usable'|'excluded'|'unreviewed';reason:string;reviewer:string|null};
export type ImportRequest={acquisition?:Acquisition|null;view_intervals?:ViewInterval[];upload_id:string;format:ImportFormat;source_id:string;data_origin:'real_video'|'synthetic_features';source_upload_id?:string;encoder_artifact_id?:string;declared_source_hash?:string;feature_space?:FeatureSpace;requested_split:'unassigned'|'train'|'calibration'|'test';context:Record<string,string|number|boolean|null>;episodes:ImportEpisode[];episode_origin:'manual'|'external'|null;clock_anchors:ClockAnchor[]};
export type ImportManifest={
 contract_version?:string;status?:string;source_hash?:string|null;artifact_hash?:string|null;source_video_id?:string;source_verified?:boolean;source_verification?:string;source_id?:string;scene_id?:string;tenant_id?:string;view_id?:string;data_origin?:string;requested_split?:string;split?:string;
 episodes?:Array<ImportEpisode&{time_domain_id?:string;complete?:boolean}>;
 time_domains?:Array<{domain_index:number;time_domain_id:string;start_ms:number;end_ms:number;capture_start_utc?:string|null}>;
 gaps?:unknown[];feature_space?:FeatureSpace|null;feature_space_id?:string|null;vectors_uri?:string|null;row_count?:number;feature_rows?:number;excluded_rows?:number;quality?:Record<string,unknown>;blocker?:{code:string;message:string}|null;
 [key:string]:unknown;
};
export type ImportProgress={stage:string;completed?:number;total?:number|null;unit?:string|null;elapsed_seconds?:number;stage_seconds?:Record<string,number>};
export type ImportJob={progress?:ImportProgress|null;cancel_requested?:boolean;id:string;status:ImportStatus;format:ImportFormat;source_id:string;created_at:string;updated_at:string;error:{code:string;message:string}|null;manifest:ImportManifest|null;has_manifest?:boolean;demo_reference?:boolean;created?:boolean};
export type Upload={id:string;sha256:string;size_bytes:number;kind:'mp4'|'npz'|'encoder'};
export type IngestionCapabilities={upload_timeout_seconds?:number;max_staging_bytes?:number;enabled:boolean;max_upload_bytes:number;formats:ImportFormat[];encoder_adapter:string;encoder_weights_available:boolean;example_available:boolean};
export type ImportForm={acquisitionText?:string;viewIntervalsText?:string;requestedSplit?:ImportRequest['requested_split'];sourceId:string;format:ImportFormat;contextFields:Record<string,string>;episodesText:string;episodeOrigin:'manual'|'external'|null;anchorsText:string;featureSpaceText:string;sourceHash:string};
export function contextValues(scene:Scene,fields:Record<string,string>):ImportRequest['context']{
 const result:ImportRequest['context']={};
 for(const [key,spec] of Object.entries(scene.context_schema)){
  const value=fields[key]??'';
  if(!value.trim()){if(scene.session_policy.required_context_keys.includes(key))throw new Error(`请填写 ${key}`);continue;}
  if(spec.type==='string')result[key]=value;
  else if(spec.type==='boolean'){if(!['true','false'].includes(value))throw new Error(`${key} 需要 true 或 false`);result[key]=value==='true';}
  else {const number=Number(value);if(!Number.isFinite(number)||(spec.type==='integer'&&!Number.isSafeInteger(number)))throw new Error(`${key} 需要有效${spec.type==='integer'?'整数':'数字'}`);result[key]=number;}
 }
 return result;
}
function arrayJSON<T>(text:string,label:string):T[]{let parsed:unknown;try{parsed=JSON.parse(text);}catch{throw new Error(`${label}不是有效 JSON`);}if(!Array.isArray(parsed))throw new Error(`${label}必须是 JSON 数组`);return parsed as T[];}
export function importMetadata(scene:Scene,form:ImportForm):Omit<ImportRequest,'upload_id'|'encoder_artifact_id'|'feature_space'> {
 if(!scene.sources.some(s=>s.id===form.sourceId&&s.type==='file'))throw new Error('请先在配置中注册绑定当前场景的文件来源');
 const episodes=arrayJSON<ImportEpisode>(form.episodesText,'周期边界');
 const mode=scene.episode_policy.mode;
 if(mode==='fixed_window'&&episodes.length)throw new Error('固定窗口场景由系统产生周期，不接受手工边界');
 if(episodes.length&&((mode==='external_cycle'&&form.episodeOrigin!=='external')||(mode==='manual_cycle'&&form.episodeOrigin!=='manual')||(mode==='manual_or_fixed_window'&&form.episodeOrigin!=='manual')||!form.episodeOrigin))throw new Error('周期来源不满足场景配置');
 if(!mode.includes('fixed_window')&&!episodes.length)throw new Error('此场景需要提供完整周期边界');
 for(const e of episodes){if(!e||Object.keys(e).some(key=>!['episode_id','domain_index','start_ms','end_ms'].includes(key))||typeof e.episode_id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(e.episode_id)||!Number.isSafeInteger(e.domain_index)||e.domain_index<0||!Number.isSafeInteger(e.start_ms)||e.start_ms<0||!Number.isSafeInteger(e.end_ms)||e.end_ms<=e.start_ms)throw new Error('周期需要有效 ID、时间域及递增的整数毫秒边界');}
 if(new Set(episodes.map(e=>e.episode_id)).size!==episodes.length)throw new Error('周期 ID 不能重复');
 const ordered=[...episodes].sort((a,b)=>a.domain_index-b.domain_index||a.start_ms-b.start_ms);
 for(let i=1;i<ordered.length;i++)if(ordered[i-1].domain_index===ordered[i].domain_index&&ordered[i-1].end_ms>ordered[i].start_ms)throw new Error('同一时间域的周期不能重叠');
 const anchors=arrayJSON<ClockAnchor>(form.anchorsText,'采集时间锚点');
 for(const a of anchors){if(!a||Object.keys(a).some(key=>!['domain_index','pts_ms','capture_utc'].includes(key))||!Number.isSafeInteger(a.domain_index)||a.domain_index<0||!Number.isSafeInteger(a.pts_ms)||a.pts_ms<0||typeof a.capture_utc!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(a.capture_utc)||!Number.isFinite(Date.parse(a.capture_utc)))throw new Error('采集锚点需要时间域、PTS 毫秒和含时区的 ISO8601 时间');}
 if(new Set(anchors.map(a=>a.domain_index)).size!==anchors.length)throw new Error('同一时间域只能提供一个采集锚点');
 const sourceHash=form.sourceHash.trim();if(sourceHash&&!/^[a-f0-9]{64}$/.test(sourceHash))throw new Error('来源 SHA256 需要 64 位小写十六进制字符');
 const capture=captureMetadata(form.acquisitionText??'null',form.viewIntervalsText??'[]');
 return {...capture,format:form.format,source_id:form.sourceId,data_origin:'real_video',requested_split:form.requestedSplit??'unassigned',context:contextValues(scene,form.contextFields),episodes,episode_origin:episodes.length?form.episodeOrigin:null,clock_anchors:anchors,...(form.format==='npz'&&sourceHash?{declared_source_hash:sourceHash}:{})};
}
export function parseFeatureSpace(text:string,encoderSha:string):FeatureSpace{
 let value:FeatureSpace;try{value=JSON.parse(text);}catch{throw new Error('特征空间不是有效 JSON');}
 if(!value||Array.isArray(value)||value.contract_version!=='3.1')throw new Error('特征空间需要 contract_version: 3.1');
 const keys=['contract_version','encoder_sha','preprocessing_sha','sampling_sha','roi_policy_hash','pooling','dimension'];
 if(Object.keys(value).some(k=>!keys.includes(k)))throw new Error('特征空间包含未知字段');
 for(const key of ['preprocessing_sha','sampling_sha','roi_policy_hash'] as const)if(!/^[a-f0-9]{64}$/.test(value[key]))throw new Error(`${key} 需要实际处理配置的 SHA256`);
 if(!/^[a-f0-9]{64}$/.test(encoderSha))throw new Error('实际编码器文件指纹无效');
 if(value.encoder_sha!==undefined&&typeof value.encoder_sha!=='string')throw new Error('编码器 SHA256 必须是字符串');
 if(value.encoder_sha&&value.encoder_sha!==encoderSha)throw new Error('编码器 SHA256 与所选文件不一致');
 if(!Number.isSafeInteger(value.dimension)||value.dimension<1||typeof value.pooling!=='string'||!value.pooling.trim())throw new Error('请提供实际特征维度与 pooling');
 return {...value,encoder_sha:encoderSha};
}

const identifier=/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;
export function captureMetadata(acquisitionText:string,viewIntervalsText:string):{acquisition:Acquisition|null;view_intervals:ViewInterval[]}{
 let acquisition:Acquisition|null;try{acquisition=JSON.parse(acquisitionText);}catch{throw new Error('采集会话不是有效 JSON');}
 if(acquisition!==null){
  if(typeof acquisition!=='object'||Array.isArray(acquisition)||Object.keys(acquisition).some(k=>!['capture_session_id','captured_at','review_status'].includes(k))||!['pending','confirmed'].includes(acquisition.review_status))throw new Error('采集会话需要 pending 或 confirmed 审核状态');
  if(acquisition.capture_session_id!=null&&(typeof acquisition.capture_session_id!=='string'||!identifier.test(acquisition.capture_session_id)))throw new Error('采集会话 ID 无效');
  if(acquisition.captured_at!=null&&(typeof acquisition.captured_at!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(acquisition.captured_at)||!Number.isFinite(Date.parse(acquisition.captured_at))))throw new Error('采集时间需要含时区的 ISO8601 时间');
  if(acquisition.review_status==='confirmed'&&(!acquisition.capture_session_id||!acquisition.captured_at))throw new Error('确认采集需要实际会话 ID 和采集时间');
 }
 const view_intervals=arrayJSON<ViewInterval>(viewIntervalsText,'视角审核区间');
 for(const v of view_intervals){if(!v||Object.keys(v).some(k=>!['domain_index','start_ms','end_ms','status','reason','reviewer'].includes(k))||!Number.isSafeInteger(v.domain_index)||v.domain_index<0||!Number.isSafeInteger(v.start_ms)||v.start_ms<0||!Number.isSafeInteger(v.end_ms)||v.end_ms<=v.start_ms||!['usable','excluded','unreviewed'].includes(v.status)||typeof v.reason!=='string'||!v.reason.trim()||(v.reviewer!=null&&(typeof v.reviewer!=='string'||!identifier.test(v.reviewer)))||(v.status!=='unreviewed'&&!v.reviewer))throw new Error('视角区间需要递增毫秒边界、原因与审核人；未审查可留空审核人');}
 const ordered=[...view_intervals].sort((a,b)=>a.domain_index-b.domain_index||a.start_ms-b.start_ms);
 for(let i=1;i<ordered.length;i++)if(ordered[i].domain_index===ordered[i-1].domain_index&&ordered[i].start_ms<ordered[i-1].end_ms)throw new Error('视角审核区间不能重叠');
 return {acquisition,view_intervals};
}

export type ExplorationReport={
 kind:'single_source_exploration';status:'exploratory_only';source_hash:string;view_id:string;
 media_url:string;preview_url?:string;eligible_rows:number;total_rows:number;feature_space:FeatureSpace;
 coverage:{fraction:number;covered_ms:number;source_domain_ms:number;domains:unknown[]};
 segments:{start_ms:number;end_ms:number;time_domain_id:string;anonymous_cluster_id:string;representative:{row:number;start_ms:number;end_ms:number}}[];
 direct_video_seek_supported:boolean;discarded_clips:unknown[];rejected_rows:unknown[];quality:unknown;limitations:string[];
};
