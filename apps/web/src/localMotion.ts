/** File-only B.1 viewer. Imported metadata never triggers extraction or uploads. */
export type Roi={id:string;role:string;xyxy:number[];visibility:string;encode_r3d?:boolean;exclusions?:unknown[]};
export type MotionPoint={pts_ms:number;start_ms:number;end_ms:number;raw:number|null;normalized:number|null;status:string};
export type MotionSeries={id:string;roi:string;role:string;method:string;points:MotionPoint[];stats:{valid:number;missing:number;mean:number|null;p50:number|null;p90:number|null};baseline:{status:string;count:number}};
export type MotionCandidate={id:string;series:string;roi:string;role:string;pts_ms:number;start_ms:number;end_ms:number;kind:string;score:number};
export type MotionArm={id:string;window_ms:number;fps:number;series:MotionSeries[];grid:{series:string;threshold:number;smoothing_ms:number;count:number;candidates:MotionCandidate[]}[];operation_background:{operation:string;control:string;aligned_points:number;correlation:number|null;mean_ratio:number|null}[];offset_sensitivity:{base:string;offset:string;correlation:number|null;relative_absolute_change:number|null}[]};
export type ManualEvent={id:string;target_id?:string|null;action:'confirm'|'adjust'|'delete'|'add';pts_ms?:number|null;kind:'onset'|'offset'|'pause'|'switch'|'unspecified';note:string};
export type ManualDocument={schema:'b1-boundary-review-v1';run_id:string;source_sha256:string;recipe_hash:string;annotator:string;start_ms:number;end_ms:number;coverage_complete:boolean;mode:'candidate_assisted'|'independent';events:ManualEvent[]};
export type MotionReport={schema:'b1-local-motion-report-v1';run_id:string;recipe_hash:string;analysis_hash:string;evidence_class:'development_exploration'|'synthetic_engineering';encoder:string;model_sha256:string;features_sha256:string;source:{asset_id:string;sha256:string;bytes:number;width:number;height:number;first_pts_ms:number;last_pts_end_ms:number};settings:{rois:Roi[];start_ms:number;end_ms:number;stride_ms:number;boundary_tolerance_ms:number;thresholds:number[];smoothing_ms:number[];[key:string]:unknown};arms:MotionArm[];primary:{arm:string;threshold:number;smoothing_ms:number;candidates:MotionCandidate[];count:number;selection:{eligible:number;temporal_suppressed:number;budget_omitted:number}};boundary_evaluation:{status:string;reason:string;precision:null;recall:null;f1:null;tolerance_ms:number};manual_review?:{manual_document:ManualDocument;evaluation:{status:string;reason:string|null;precision:number|null;recall:number|null;f1:number|null;annotation_mode:string}};formal_research_passed:false;b2_started:false;feature_rows:number;elapsed_extraction_seconds:number};
const sha=/^[a-f0-9]{64}$/;
const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
export const formatPts=(ms:number)=>`${Math.floor(ms/60000)}:${(Math.floor(ms/1000)%60).toString().padStart(2,'0')}.${Math.round(ms%1000).toString().padStart(3,'0')}`;
export function validBox(box:number[]){return box.length===4&&box.every(finite)&&0<=box[0]&&box[0]<box[2]&&box[2]<=1&&0<=box[1]&&box[1]<box[3]&&box[3]<=1;}
export function parseMotionReport(text:string):MotionReport{
 if(text.length>8*1024*1024)throw Error('研究报告超过8 MiB限制');
 const r=JSON.parse(text) as MotionReport;
 if(r.schema!=='b1-local-motion-report-v1'||!sha.test(r.run_id)||!sha.test(r.recipe_hash)||!r.source||!sha.test(r.source.sha256)||!['development_exploration','synthetic_engineering'].includes(r.evidence_class)||r.formal_research_passed!==false||r.b2_started!==false)throw Error('不是有效的B.1开发探索报告');
 if(!finite(r.source.width)||!finite(r.source.height)||r.source.width<=0||r.source.height<=0||!finite(r.source.bytes)||r.source.bytes<=0||r.source.bytes>2147483648)throw Error('来源尺寸或字节数无效');
 if(!r.settings||!Array.isArray(r.settings.rois)||r.settings.rois.length>12||!r.settings.rois.every(v=>validBox(v.xyxy))||!Array.isArray(r.arms)||!r.arms.length||r.arms.length>16)throw Error('ROI或实验组合无效');
 if(!finite(r.settings.start_ms)||!finite(r.settings.end_ms)||r.settings.start_ms>=r.settings.end_ms)throw Error('实验时间范围无效');
 let total=0;
 for(const a of r.arms){
  if(!Array.isArray(a.series)||a.series.length>36||!Array.isArray(a.grid)||a.grid.length>1500)throw Error('实验曲线过多');
  for(const s of a.series){
   if(!Array.isArray(s.points)||s.points.length>10000)throw Error('时间点超过限制');
   let previous=-1;
   for(const p of s.points){if(!finite(p.pts_ms)||p.pts_ms<=previous||p.pts_ms<r.settings.start_ms||p.pts_ms>r.settings.end_ms||![p.raw,p.normalized].every(v=>v===null||finite(v)))throw Error('曲线PTS/数值无效');previous=p.pts_ms;total++;}
  }
 }
 if(total>200000||!r.primary||r.primary.candidates.length>200||!r.primary.candidates.every(c=>sha.test(r.run_id)&&finite(c.pts_ms)&&finite(c.score)&&c.pts_ms>=r.settings.start_ms&&c.pts_ms<=r.settings.end_ms)||!r.arms.some(a=>a.id===r.primary.arm))throw Error('候选或主实验无效');
 return r;
}
export function blankReview(r:MotionReport):ManualDocument{return {schema:'b1-boundary-review-v1',run_id:r.run_id,source_sha256:r.source.sha256,recipe_hash:r.recipe_hash,annotator:'',start_ms:r.settings.start_ms,end_ms:r.settings.end_ms,coverage_complete:false,mode:'candidate_assisted',events:[]};}
export function parseReview(text:string,r:MotionReport):ManualDocument{
 const d=JSON.parse(text) as ManualDocument;
 if(d.schema!=='b1-boundary-review-v1'||d.run_id!==r.run_id||d.recipe_hash!==r.recipe_hash||d.source_sha256!==r.source.sha256)throw Error('人工复核与来源、配方或实验版本不匹配');
 if(!Array.isArray(d.events)||d.events.length>10000||!finite(d.start_ms)||!finite(d.end_ms)||d.start_ms<r.settings.start_ms||d.end_ms>r.settings.end_ms||d.start_ms>=d.end_ms)throw Error('复核范围或事件数无效');
 const ids=new Set<string>(),targets=new Set(r.primary.candidates.map(c=>c.id));
 for(const e of d.events){
  if(!e.id||ids.has(e.id)||!['confirm','adjust','delete','add'].includes(e.action))throw Error('复核事件ID或操作无效');ids.add(e.id);
  if(e.action==='add'){if(e.pts_ms==null)throw Error('新增边界需要PTS');targets.add('manual:'+e.id);}
  else if(!e.target_id||!targets.has(e.target_id))throw Error('复核引用未知边界');
  if(d.mode==='independent'&&e.action!=='add'&&!e.target_id?.startsWith('manual:'))throw Error('独立标注不能引用算法候选');
  if(e.action==='adjust'&&e.pts_ms==null)throw Error('修订边界需要PTS');
  if(e.pts_ms!=null&&(!finite(e.pts_ms)||e.pts_ms<d.start_ms||e.pts_ms>d.end_ms))throw Error('人工边界PTS超出范围');
 }
 return d;
}
export function latestDecisions(d:ManualDocument){const m=new Map<string,ManualEvent>();for(const e of d.events)if(e.target_id)m.set(e.target_id,e);return m;}
export function roiDraft(r:MotionReport,rois:Roi[]){if(!rois.every(v=>validBox(v.xyxy))||rois.filter(v=>v.role==='global').length!==1||rois.some(v=>v.role==='global'&&JSON.stringify(v.xyxy)!=='[0,0,1,1]'))throw Error('ROI为空、越界或全局控制被裁剪；没有修改已执行特征');return {local_motion:{...r.settings,rois}};}
export function candidateEvaluationNote(r:MotionReport,edited:boolean){return edited?'复核已修改，旧指标失效；须通过CLI重新评价':r.manual_review?.evaluation.status==='evaluated_development_only'?'已执行开发集人工边界评价；不是独立泛化证明':'无完整人工边界，Precision / Recall / F1 未验证';}
