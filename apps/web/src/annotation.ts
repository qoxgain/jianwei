/** Source-only full-video annotation. No prediction or curve imports. */
import {sha256} from 'js-sha256';
export type Frame={index:number;pts_ms:number;end_ms:number};
export type SourcePacket={schema:'b11-annotation-source-v1';evidence_class:'synthetic_engineering'|'development_exploration';descriptor_hash:string;source:{asset_id:string;sha256:string;bytes:number;width:number;height:number;first_pts_ms:number;last_pts_end_ms:number};frames:Frame[];annotation_protocol:string;clock_mapping:string};
export type EventOp='boundary_add'|'boundary_update'|'boundary_delete'|'boundary_review'|'interval_add'|'interval_update'|'interval_delete'|'exposure'|'attest';
export type AnnotationEvent={id:string;at:string;actor:string;op:EventOp;target_id:string|null;pts_ms:number|null;end_ms:number|null;kind:string|null;note:string};
export type AnnotationDoc={schema:'b11-annotation-v1';annotation_id:string;source_sha256:string;descriptor_hash:string;annotator:string;mode:'blind'|'assisted';evidence_class:SourcePacket['evidence_class'];start_ms:number;end_ms:number;created_at:string;revision:number;events:AnnotationEvent[];status:'draft'|'sealed';coverage_complete:boolean;human_attestation:boolean;sealed_at:string|null;sealed_hash:string|null;parent_sealed_hash:string|null};
export type Boundary={id:string;pts_ms:number;kind:string;note:string;reviewed_by:{actor:string;at:string;note:string}[]};
export type Interval={id:string;start_ms:number;end_ms:number;kind:string;note:string};
export const boundaryKinds:Record<string,string>={onset:'动作开始',offset:'动作结束',pause:'停顿',switch:'工步变化'};
export const intervalKinds:Record<string,string>={reviewed:'已人工审核',uncertain:'无法判断',occluded:'遮挡',lighting:'光照/阴影干扰',no_action:'无动作',background_invalid:'背景ROI无效（后续复核）'};
const sha=/^[a-f0-9]{64}$/;
const integer=(n:unknown):n is number=>typeof n==='number'&&Number.isSafeInteger(n);
export function canonical(value:unknown):string{
 if(value===null||typeof value!=='object')return JSON.stringify(value);
 if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
 const v=value as Record<string,unknown>;return '{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}';
}
export const digest=(v:unknown)=>sha256(canonical(v));
export function parseSource(text:string):SourcePacket{
 if(text.length>32*1024*1024)throw Error('PTS文件超过32 MiB');const s=JSON.parse(text) as SourcePacket;
 const {descriptor_hash,...body}=s;
 if(s.schema!=='b11-annotation-source-v1'||!sha.test(s.source?.sha256)||digest(body)!==descriptor_hash||!['synthetic_engineering','development_exploration'].includes(s.evidence_class))throw Error('源PTS描述格式或哈希不符；不能用算法报告替代');
 if(Object.keys(s).some(k=>!['schema','evidence_class','descriptor_hash','source','frames','annotation_protocol','clock_mapping'].includes(k)))throw Error('源描述不得携带算法内容');
 if(![s.source.bytes,s.source.width,s.source.height].every(v=>integer(v)&&v>0)||!Array.isArray(s.frames)||!s.frames.length||s.frames.length>500000)throw Error('源尺寸/帧数无效');
 s.frames.forEach((f,i)=>{if(f.index!==i||!integer(f.pts_ms)||!integer(f.end_ms)||f.end_ms<=f.pts_ms||(i>0&&s.frames[i-1].end_ms!==f.pts_ms))throw Error('PTS不连续或索引无效，不能按固定fps猜测');});
 if(s.frames[0].pts_ms!==s.source.first_pts_ms||s.frames.at(-1)!.end_ms!==s.source.last_pts_end_ms)throw Error('原片范围与帧表不符');return s;
}
export function union(ranges:number[][]){const result:number[][]=[];for(const [a,b] of [...ranges].sort((x,y)=>x[0]-y[0])){const last=result.at(-1);if(last&&a<=last[1])last[1]=Math.max(last[1],b);else result.push([a,b]);}return result;}
export function gaps(start:number,end:number,ranges:number[][]){const result:number[][]=[];let at=start;for(const [a,b] of union(ranges)){if(a>at)result.push([at,Math.min(a,end)]);at=Math.max(at,b);}if(at<end)result.push([at,end]);return result.filter(([a,b])=>b>a);}
export function blankAnnotation(s:SourcePacket):AnnotationDoc{return {schema:'b11-annotation-v1',annotation_id:crypto.randomUUID(),source_sha256:s.source.sha256,descriptor_hash:s.descriptor_hash,annotator:'',mode:'blind',evidence_class:s.evidence_class,start_ms:s.source.first_pts_ms,end_ms:s.source.last_pts_end_ms,created_at:new Date().toISOString(),revision:0,events:[],status:'draft',coverage_complete:false,human_attestation:false,sealed_at:null,sealed_hash:null,parent_sealed_hash:null};}
export function append(d:AnnotationDoc,op:EventOp,values:Partial<AnnotationEvent>={}):AnnotationDoc{
 if(d.status==='sealed')throw Error('已封存版本不可修改，请新建修订草稿');
 if(!d.annotator.trim())throw Error('请先填写标注人员');
 const e:AnnotationEvent={id:crypto.randomUUID(),at:new Date().toISOString(),actor:d.annotator,op,target_id:null,pts_ms:null,end_ms:null,kind:null,note:'',...values};
 return {...d,events:[...d.events,e],revision:d.revision+1};
}
export function compile(d:AnnotationDoc,s:SourcePacket){
 if(d.schema!=='b11-annotation-v1'||d.source_sha256!==s.source.sha256||d.descriptor_hash!==s.descriptor_hash||d.evidence_class!==s.evidence_class)throw Error('标注与视频或PTS版本不匹配');
 if(d.start_ms!==s.source.first_pts_ms||d.end_ms!==s.source.last_pts_end_ms||!Array.isArray(d.events)||d.events.length>20000||d.revision!==d.events.length)throw Error('必须保留完整原片范围及操作版本');
 if(!['blind','assisted'].includes(d.mode)||!['draft','sealed'].includes(d.status))throw Error('标注模式或状态不符');
 if(typeof d.coverage_complete!=='boolean'||typeof d.human_attestation!=='boolean'||typeof d.annotator!=='string')throw Error('人工声明或人员格式无效');
 const zoned=/(Z|[+-]\d{2}:\d{2})$/;const b=new Map<string,Boundary>(),intervals=new Map<string,Interval>(),ids=new Set<string>();let exposed=d.mode==='assisted',last=Date.parse(d.created_at);
 if(!Number.isFinite(last)||!zoned.test(d.created_at))throw Error('标注时间无效或缺少时区');
 for(const e of d.events){
  const time=Date.parse(e.at);if(typeof e.id!=='string'||!e.id||ids.has(e.id)||typeof e.actor!=='string'||!e.actor.trim()||typeof e.note!=='string'||e.note.length>1000||!Number.isFinite(time)||!zoned.test(e.at)||time<last)throw Error('操作ID/时间/人员/备注无效');ids.add(e.id);last=time;
  if(e.op==='exposure'){exposed=true;continue;}if(e.op==='attest')continue;
  if(e.pts_ms!==null&&(!integer(e.pts_ms)||e.pts_ms<d.start_ms||e.pts_ms>d.end_ms))throw Error('边界超出完整原片');
  if(e.op==='boundary_add'){if(e.pts_ms===null||!e.kind||!Object.hasOwn(boundaryKinds,e.kind))throw Error('边界需要PTS及人工类型');b.set(e.id,{id:e.id,pts_ms:e.pts_ms,kind:e.kind,note:e.note,reviewed_by:[]});}
  else if(['boundary_update','boundary_delete','boundary_review'].includes(e.op)){
   const target=b.get(e.target_id??'');if(!target)throw Error('边界引用不存在或已删除');
   if(e.op==='boundary_delete')b.delete(target.id);
   else if(e.op==='boundary_review')target.reviewed_by.push({actor:e.actor,at:e.at,note:e.note});
   else{if(e.pts_ms===null||!e.kind||!Object.hasOwn(boundaryKinds,e.kind))throw Error('修订需要PTS和类型');Object.assign(target,{pts_ms:e.pts_ms,kind:e.kind,note:e.note,reviewed_by:[]});}
  }else if(e.op==='interval_delete'){if(!intervals.delete(e.target_id??''))throw Error('区间不存在');}
  else if(e.op==='interval_add'||e.op==='interval_update'){
   if(e.pts_ms===null||!integer(e.end_ms)||e.end_ms<=e.pts_ms||e.end_ms>d.end_ms||!e.kind||!Object.hasOwn(intervalKinds,e.kind))throw Error('区间范围或类别无效');
   const id=e.op==='interval_add'?e.id:e.target_id!;if(e.op==='interval_update'&&!intervals.has(id))throw Error('区间不存在');intervals.set(id,{id,start_ms:e.pts_ms,end_ms:e.end_ms,kind:e.kind,note:e.note});
  }else throw Error('不支持的标注操作');
 }
 if(exposed&&d.mode==='blind')throw Error('看过算法后不能声称盲标，请标记为辅助标注');
 const ranges=[...intervals.values()],reviewed=union(ranges.filter(v=>v.kind==='reviewed').map(v=>[v.start_ms,v.end_ms])),missing=gaps(d.start_ms,d.end_ms,reviewed),excluded=union(ranges.filter(v=>['uncertain','occluded'].includes(v.kind)).map(v=>[v.start_ms,v.end_ms]));
 if(d.status==='sealed'){const {sealed_hash,...body}=d;if(!d.annotator.trim()||!sealed_hash||digest(body)!==sealed_hash||!d.sealed_at||!Number.isFinite(Date.parse(d.sealed_at))||!zoned.test(d.sealed_at)||Date.parse(d.sealed_at)<last)throw Error('封存版本被修改或时间不符');}
 const coincident=new Set([...b.values()].map(v=>v.pts_ms)).size!==b.size;
 return {boundaries:[...b.values()].sort((a,b)=>a.pts_ms-b.pts_ms||a.id.localeCompare(b.id)),intervals:ranges,reviewed,missing,excluded,coincident,coverage:1-missing.reduce((n,[a,b])=>n+b-a,0)/(d.end_ms-d.start_ms),eligible:d.status==='sealed'&&d.coverage_complete&&!missing.length&&d.human_attestation&&!coincident&&gaps(d.start_ms,d.end_ms,excluded).length>0};
}
export function parseAnnotation(text:string,s:SourcePacket){if(text.length>8*1024*1024)throw Error('标注文件超过8 MiB');const d=JSON.parse(text) as AnnotationDoc;compile(d,s);return d;}
export function seal(d:AnnotationDoc,s:SourcePacket):AnnotationDoc{const a=compile(d,s);if(a.coincident)throw Error('相同PTS多个人工边界须先人工裁定；不会自动合并或把同一时间点重复计为真值');if(!d.annotator.trim()||!d.coverage_complete||!d.human_attestation||a.missing.length)throw Error('请填写人员、补齐全部审核区间，并声明真实人工覆盖；未知/遮挡须单独标记');const next={...d,status:'sealed' as const,sealed_at:new Date().toISOString(),sealed_hash:null};const {sealed_hash,...body}=next;return {...next,sealed_hash:digest(body)};}
export function fork(d:AnnotationDoc):AnnotationDoc{return {...d,status:'draft',parent_sealed_hash:d.sealed_hash,sealed_hash:null,sealed_at:null,coverage_complete:false,human_attestation:false};}
export function frameAt(s:SourcePacket,pts:number){let low=0,high=s.frames.length-1;while(low<high){const m=Math.ceil((low+high)/2);if(s.frames[m].pts_ms<=pts)low=m;else high=m-1;}return low;}
export const exposureKey='jianwei-b11-viewed-algorithms';
let memoryExposure=false;
export function recordExposure(reason:string){memoryExposure=true;try{localStorage.setItem(exposureKey,reason);}catch{/* Source-only entry also checks in-memory exposure. */}}
export function hasExposure(){if(memoryExposure)return true;try{return !!localStorage.getItem(exposureKey);}catch{return true;}}
export function expose(d:AnnotationDoc,note:string):AnnotationDoc{if(d.status==='sealed'||d.mode==='assisted')return d;return {...d,mode:'assisted',revision:d.revision+1,events:[...d.events,{id:crypto.randomUUID(),at:new Date().toISOString(),actor:'system:exposure_record',op:'exposure',target_id:null,pts_ms:null,end_ms:null,kind:null,note}]};}
export function raterAgreement(first:AnnotationDoc,second:AnnotationDoc,s:SourcePacket){
 const a=compile(first,s),b=compile(second,s);if(!a.eligible||!b.eligible||first.annotation_id===second.annotation_id||first.annotator===second.annotator||first.mode!=='blind'||second.mode!=='blind')throw Error('须有两位不同人员的独立完整盲标封存版本，不能合并为共识真值');
 const valid=gaps(first.start_ms,first.end_ms,union([...a.excluded,...b.excluded]));const matches:{first:Boundary;second:Boundary;error_ms:number}[]=[];let missingFirst=0,missingSecond=0;
 const inSegment=(t:number,start:number,end:number)=>start<=t&&(t<end||(t===end&&end===first.end_ms));
 for(const [start,end] of valid){const x=a.boundaries.filter(v=>inSegment(v.pts_ms,start,end)),y=b.boundaries.filter(v=>inSegment(v.pts_ms,start,end));let i=0,j=0;while(i<x.length&&j<y.length){if(x[i].pts_ms<y[j].pts_ms-500){missingSecond++;i++;}else if(y[j].pts_ms<x[i].pts_ms-500){missingFirst++;j++;}else{matches.push({first:x[i],second:y[j],error_ms:y[j].pts_ms-x[i].pts_ms});i++;j++;}}missingSecond+=x.length-i;missingFirst+=y.length-j;}
 return {matches,missingFirst,missingSecond,typeDisagreements:matches.filter(v=>v.first.kind!==v.second.kind)};
}
