import systemYaml from '../../../config/system.yaml?raw';
import Ajv2020 from 'ajv/dist/2020';
import configSchema from '../../../schemas/scene_config.schema.json';
import { contentHash, parseYaml } from './validation';
import { parseScene, previewScene, sceneDiff } from './sceneValidation';
import type { Scene, SceneBootstrap, ScenePreview, SceneVersion, Profile } from './sceneTypes';
declare const __DEMO_BUILD__:boolean;
export const isDemo=__DEMO_BUILD__||location.protocol==='file:'||new URLSearchParams(location.search).get('demo')==='1';
type Config={config_version:string;active_scene:string;active_profile:string;scenes:Record<string,Omit<Scene,'id'|'sources'|'config_hash'|'knowledge_hash'|'data_status'|'rules_status'>>;profiles:Record<string,{mode:string;tenant_id:string;sources:{id:string;type:string;scene_ids:string[];roi_xyxy:number[]}[]}>};
const {session:_legacySession,knowledge_file:_legacyKnowledge,...config}=parseYaml(systemYaml) as Config & {session?:null;knowledge_file?:null};
const yamlFiles=import.meta.glob('../../../knowledge/scenes/*.yaml',{eager:true,query:'?raw',import:'default'}) as Record<string,string>;
const validConfig=new Ajv2020({strict:false}).compile(configSchema)(config);
function sceneBase(id:string){const spec=config.scenes[id];if(!spec)throw new Error('场景不存在');const source=yamlFiles[`../../../${spec.knowledge_file}`];if(!source)throw new Error('场景知识缺失');const data=parseScene(source);return {yaml:source,data,hash:contentHash(data),version:data.knowledge_version};}
function localScene(id:string):Scene{const spec=config.scenes[id],base=sceneBase(id);const sources=config.profiles[config.active_profile].sources.filter(s=>s.scene_ids.includes(id)).map(({id,type,roi_xyxy})=>({id,type,roi_xyxy}));return {id,...spec,sources,config_hash:contentHash({scene:spec,sources,profile:config.active_profile}),knowledge_hash:base.hash,data_status:'not_connected',rules_status:base.data.events.length?'configured_unverified':'collection_only'};}
function key(id:string){return `jianwei.p0.1.1.${config.profiles[config.active_profile].tenant_id}.${id}.candidates`;}
function baseline(id:string):SceneVersion{const b=sceneBase(id);return {id:`baseline-${id}`,hash:b.hash,version:b.version,status:'baseline',base_hash:null,created_at:'',diff:sceneDiff(b.data,b.data),yaml:b.yaml};}
function stored(id:string):SceneVersion[]{
  const value=localStorage.getItem(key(id));if(!value)return [];
  let parsed:unknown;try{parsed=JSON.parse(value);}catch{throw new Error('候选存储无法读取，请先保留原始存储内容。');}
  if(!Array.isArray(parsed)||parsed.some(v=>!v||typeof v.id!=='string'||typeof v.yaml!=='string'||v.status!=='candidate'))throw new Error('候选存储格式错误');
  const b=sceneBase(id);for(const row of parsed){const p=previewScene(row.yaml,b.data);if(!p.valid||p.hash!==row.hash)throw new Error('候选内容或场景校验失败');}
  return parsed as SceneVersion[];
}
function legacyStored(id:string):SceneVersion[]{
  // Keep existing P0.1 browser history read-only, with explicit normalized hash.
  if(id!==config.active_scene)return [];
  const value=localStorage.getItem('jianwei.p0.1.candidates.v1');if(!value)return [];
  const rows=JSON.parse(value);if(!Array.isArray(rows))throw new Error('旧版候选存储格式错误');
  return rows.map((row:SceneVersion)=>{if(typeof row.yaml!=='string')throw new Error('旧版候选缺少YAML');const result=previewScene(row.yaml!,sceneBase(id).data);if(!result.valid||!result.data||!result.hash)throw new Error('旧候选无法迁移，请保留原始内容核对');return {...row,migration:{old_hash:row.hash,normalized_hash:result.hash,yaml:row.yaml!,data:result.data,node_map:{}}};});
}
async function request<T>(path:string,data?:unknown):Promise<T>{
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
  try{const response=await fetch(`/api/${path}`,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined,signal:controller.signal});const result=await response.json();if(!response.ok)throw new Error(result.detail?.message||`请求失败 ${response.status}`);return result;}finally{clearTimeout(timer);}
}
function localProfile(id:string):Profile{if(!config.profiles[id])throw new Error('环境不存在');return {id,name:({demo:'演示环境',lab:'研发环境',site:'现场环境'} as Record<string,string>)[id]??id,mode:config.profiles[id].mode,static_valid:Boolean(validConfig),runtime_ready:false,checks:[{code:'schema',status:validConfig?'passed':'blocked',message:'配置字段Schema检查；完整语义检查请运行后端doctor'},{code:'runtime',status:'not_run',message:'未连接视频、模型或外部服务'}],blockers:[{code:'offline',message:'离线页面无法验证运行环境'}]};}
export async function bootstrapScene(id?:string):Promise<SceneBootstrap>{
  if(!isDemo){if(!id)id=(await request<{active_scene:string}>('scenes')).active_scene;return request(`scenes/${encodeURIComponent(id)}`);}
  id=id??config.active_scene;const b=sceneBase(id);
  return {project:{name:'见微',title:'多模态自学习感知平台',version:'3.0',stage:'P0.1.1'},active_scene:id,tenant_id:config.profiles[config.active_profile].tenant_id,active_profile:config.active_profile,config_hash:contentHash(config),scene:localScene(id),scenes:Object.keys(config.scenes).map(localScene),profiles:Object.keys(config.profiles).map(localProfile),knowledge:b,versions:await sceneVersions(id),capabilities:{configuration:true,knowledge_preview:true,knowledge_versioning:true,video_ingest:false,training:false,live_inference:false,authentication:false},blockers:[]};
}
export async function scenePreview(id:string,yaml:string):Promise<ScenePreview>{return isDemo?previewScene(yaml,sceneBase(id).data):request(`scenes/${encodeURIComponent(id)}/knowledge/preview`,{yaml});}
export async function sceneVersions(id:string):Promise<SceneVersion[]>{return isDemo?[baseline(id),...legacyStored(id),...stored(id)]:(await request<{versions:SceneVersion[]}>(`scenes/${encodeURIComponent(id)}/knowledge/versions`)).versions;}
export async function sceneVersionDetails(id:string,versionId:string):Promise<SceneVersion>{
  if(!isDemo)return (await request<{version:SceneVersion}>(`scenes/${encodeURIComponent(id)}/knowledge/versions/${encodeURIComponent(versionId)}`)).version;
  const found=(await sceneVersions(id)).find(v=>v.id===versionId);if(!found)throw new Error('知识版本不存在');return found;
}
export async function saveSceneCandidate(id:string,yaml:string,base_hash:string):Promise<{created:boolean;version:SceneVersion}>{
  if(!isDemo)return request(`scenes/${encodeURIComponent(id)}/knowledge/versions`,{yaml,base_hash});
  const b=sceneBase(id);if(base_hash!==b.hash)throw new Error('基线版本变化，请重新预览');
  const result=previewScene(yaml,b.data);if(!result.valid||!result.hash||!result.data||!result.diff)throw new Error('校验未通过');
  const exists=(await sceneVersions(id)).find(v=>v.hash===result.hash||v.migration?.normalized_hash===result.hash);if(exists)return {created:false,version:exists};
  const row:SceneVersion={id:`local-${id}-${result.hash.slice(0,12)}`,hash:result.hash,version:result.data.knowledge_version,status:'candidate',base_hash,created_at:new Date().toISOString(),yaml,diff:result.diff};
  localStorage.setItem(key(id),JSON.stringify([...stored(id),row]));return {created:true,version:row};
}
export async function validateSceneProfile(id:string):Promise<Profile>{return isDemo?localProfile(id):request('config/validate',{profile:id});}
