import {isDemo} from './sceneService';
export type SopMapping={asset_id:string;section_ids:string[];notes:string};
export type SopReference={source_sha256:string;pdf_page:number;text_start:number;text_end:number;text:string;text_sha256:string;extraction_version:string};
export type SopSection={document_id:string;title:string;pdf_pages:number[];evidence_classification:unknown;raw_references:SopReference[]};
export type SopAsset={asset_id:string;kind:string;path:string;media_url:string;preview_url?:string;media?:{duration_seconds?:number;rotation_degrees?:number;codec?:string}};
export type SopDocument={revision:string;status:string;activated:false;is_ground_truth:false;sections:SopSection[];mappings:SopMapping[];disputes:string[];audit:Record<string,unknown>[];assets:SopAsset[];source:{sha256:string;pdf_pages:number;media_url:string};evidence?:Record<string,unknown>[];limitations?:string[]};
export type SopRevision={base_revision:string;reviewer:string;reason:string;mappings:SopMapping[];disputes:string[]};
export type SopEvidence={revision:string;kind:'observation'|'prediction'|'normative_expectation'|'human_annotation'|'semantic_candidate';asset_id:string;section_id:string;start_ms:number;end_ms:number;producer:string;note:string;evidence_type:'visible_operation'|'closeup_detail'|'instrument_record';evidence_reference:string|null};
async function request<T>(scene:string,suffix='',body?:unknown):Promise<T>{
 if(isDemo)throw new Error('SOP 原文与人工审核需要启动完整 API 服务');
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
 try{const response=await fetch(`/api/scenes/${encodeURIComponent(scene)}/sop${suffix}`,{signal:controller.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});let value;try{value=await response.json();}catch{throw new Error(`无法读取审核服务响应（${response.status}）`);}if(!response.ok)throw new Error(value.detail?.message||`审核请求失败（${response.status}）`);return value;}catch(error){if((error as Error).name==='AbortError')throw new Error('审核请求超时，请刷新后核对保存结果');throw error;}finally{clearTimeout(timer);}
}
export const getSop=(scene:string)=>request<SopDocument>(scene);
export const saveSopRevision=(scene:string,body:SopRevision)=>request<unknown>(scene,'/revisions',body);
export const decideSop=(scene:string,body:{revision:string;action:'approve'|'revoke';reviewer:string;reason:string})=>request<unknown>(scene,'/decisions',body);
export const recordSopEvidence=(scene:string,body:SopEvidence)=>request<unknown>(scene,'/evidence',body);
export function reviewerIdentity(reviewer:string,reason:string){
 const identity=reviewer.trim();if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(identity))throw new Error('请填写审核人 ID（字母、数字、点、横线或下划线）');
 if(!reason.trim())throw new Error('请填写本次修改或决定的理由');return {reviewer:identity,reason:reason.trim()};
}
export function evidenceInterval(start:string,end:string,duration?:number){
 const begin=Number(start),finish=Number(end);
 if(!start.trim()||!end.trim()||!Number.isFinite(begin)||!Number.isFinite(finish)||begin<0||finish<=begin||(duration!==undefined&&finish>duration))throw new Error('证据区间需要位于视频内的递增秒数');
 const start_ms=Math.round(begin*1000),end_ms=Math.round(finish*1000);
 if(!Number.isSafeInteger(start_ms)||!Number.isSafeInteger(end_ms)||end_ms<=start_ms)throw new Error('证据区间至少需要一毫秒且不能超过整数范围');
 return {start_ms,end_ms};
}
export function toggleSection(mappings:SopMapping[],asset:string,section:string,checked:boolean):SopMapping[]{
 return mappings.map(mapping=>mapping.asset_id!==asset?mapping:{...mapping,section_ids:checked?[...new Set([...mapping.section_ids,section])]:mapping.section_ids.filter(id=>id!==section)});
}
