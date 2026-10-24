import {isDemo} from './sceneService';
import {downloadText} from './service';
import exampleManifest from './ingestionExample.json';
import type {ImportJob,ImportManifest,ImportRequest,IngestionCapabilities,Upload} from './ingestionTypes';
async function request<T>(path:string,options:RequestInit={}):Promise<T>{
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);
 try{const response=await fetch(`/api/${path}`,{...options,signal:controller.signal});let result;try{result=await response.json();}catch{throw new Error(`服务返回了无法读取的结果（${response.status}）`);}if(!response.ok)throw new Error(result.detail?.message||`请求失败 ${response.status}`);return result;}catch(error){if((error as Error).name==='AbortError')throw new Error('请求超时，请刷新任务列表确认服务器状态');throw error;}finally{clearTimeout(timer);}
}
const prefix=(scene:string)=>`scenes/${encodeURIComponent(scene)}/imports`;
const demoKey=(scene:string)=>`jianwei.p0.2.reference.${scene}`;
const references=exampleManifest as {scenes:Record<string,ImportManifest>};
function demoJob(scene:string):ImportJob{const manifest=references.scenes[scene];if(!manifest)throw new Error('当前场景尚未随附实际示例清单，请使用完整 API 服务运行示例');return {id:`reference-${scene}`,status:'succeeded',format:'npz',source_id:manifest.source_id??'cam_01',created_at:'',updated_at:'',error:null,demo_reference:true,manifest:structuredClone(manifest)};}
export async function ingestionCapabilities():Promise<IngestionCapabilities>{return isDemo?{enabled:false,max_upload_bytes:0,formats:['mp4','npz'],encoder_adapter:'existing_features',encoder_weights_available:false,example_available:true}:request('ingestion/capabilities');}
export async function uploadArtifact(file:File,kind:Upload['kind']):Promise<Upload>{if(isDemo)throw new Error('真实文件接入需要启动本地 API 服务');return request(`uploads?kind=${kind}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});}
export async function importJobs(scene:string):Promise<ImportJob[]>{if(isDemo)return localStorage.getItem(demoKey(scene))==='shown'&&references.scenes[scene]?[demoJob(scene)]:[];return (await request<{items:ImportJob[]}>(prefix(scene))).items;}
export async function importJob(scene:string,id:string):Promise<ImportJob>{if(isDemo){const job=demoJob(scene);if(job.id!==id)throw new Error('示例记录不存在');return job;}return request(`${prefix(scene)}/${encodeURIComponent(id)}`);}
export async function submitImport(scene:string,body:ImportRequest):Promise<ImportJob>{if(isDemo)throw new Error('浏览器演示不运行导入任务');return request(prefix(scene),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
export async function runImportExample(scene:string):Promise<ImportJob>{if(isDemo){const job=demoJob(scene);localStorage.setItem(demoKey(scene),'shown');return job;}return request(`${prefix(scene)}/example`,{method:'POST'});}
export async function retryImport(scene:string,id:string):Promise<ImportJob>{if(isDemo)throw new Error('静态示例记录无需重试');return request(`${prefix(scene)}/${encodeURIComponent(id)}/retry`,{method:'POST'});}
export async function downloadManifest(scene:string,job:ImportJob):Promise<void>{const manifest=isDemo?job.manifest:await request<ImportManifest>(`${prefix(scene)}/${encodeURIComponent(job.id)}/manifest`);downloadText(`${job.id}.manifest.json`,JSON.stringify(manifest,null,2));}
