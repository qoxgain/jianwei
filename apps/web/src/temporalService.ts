import {isDemo} from './sceneService';
import evidence from './temporalExample.json';
import readinessEvidence from './readinessExample.json';
import type {ReadinessReport,DatasetDraft} from './readinessTypes';
import type {TemporalExample,TemporalInput,TemporalRun} from './temporalTypes';
const references=evidence as {scenes:Record<string,TemporalRun[]>};
const path=(scene:string)=>`scenes/${encodeURIComponent(scene)}/temporal`;
async function request<T>(url:string,data?:unknown):Promise<T>{
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
 try{const response=await fetch('/api/'+url,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data),signal:controller.signal});const result=await response.json();if(!response.ok)throw new Error(result.detail?.message??`请求失败 ${response.status}`);return result;}finally{clearTimeout(timer);}
}
export async function temporalInputs(scene:string){return isDemo?[]:(await request<{items:TemporalInput[]}>(path(scene)+'/inputs')).items;}
export async function temporalRuns(scene:string):Promise<TemporalRun[]>{return isDemo?structuredClone(references.scenes[scene]??[]):(await request<{items:TemporalRun[]}>(path(scene)+'/runs')).items;}
export async function temporalRun(scene:string,id:string):Promise<TemporalRun>{if(isDemo){const row=references.scenes[scene]?.find(r=>r.id===id);if(!row)throw new Error('此场景尚无随附研究记录');return structuredClone(row);}return request(path(scene)+'/runs/'+encodeURIComponent(id));}
function requireAPI(){if(isDemo)throw new Error('离线页面只展示已执行的合成研究记录；真实训练需要本地API');}
export async function createTemporalExample(scene:string):Promise<TemporalExample>{requireAPI();return request(path(scene)+'/example',{});}
export async function trainTemporal(scene:string,import_ids:string[]):Promise<TemporalRun>{requireAPI();return request(path(scene)+'/train',{import_ids});}
export async function evaluateTemporal(scene:string,model_run_id:string,import_ids:string[]):Promise<TemporalRun>{requireAPI();return request(path(scene)+'/evaluate',{model_run_id,import_ids,sealed_test:true});}
export async function retryTemporal(scene:string,id:string):Promise<TemporalRun>{requireAPI();return request(path(scene)+'/runs/'+encodeURIComponent(id)+'/retry',{});}
export function temporalArtifact(scene:string,id:string,name:string){return '/api/'+path(scene)+'/runs/'+encodeURIComponent(id)+'/artifacts/'+encodeURIComponent(name);}

export async function checkReadiness(scene:string,import_ids:string[],model_run_id?:string):Promise<ReadinessReport>{
 if(isDemo){const reports=readinessEvidence as {scenes:Record<string,ReadinessReport>};const report=reports.scenes[scene];if(!report)throw new Error('此场景尚无随附预检记录');return structuredClone(report);}
 return request(path(scene)+'/readiness',{import_ids,...(model_run_id?{model_run_id}:{})});
}
export async function datasetTemplate(scene:string,import_ids:string[]):Promise<DatasetDraft>{requireAPI();return request(path(scene)+'/dataset-template',{import_ids});}
