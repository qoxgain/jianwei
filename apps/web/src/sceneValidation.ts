import Ajv2020 from 'ajv/dist/2020';
import { sha256 } from 'js-sha256';
import schema from '../../../schemas/scene_knowledge.schema.json';
import { canonical, contentHash, parseYaml, previewKnowledge } from './validation';
import type { Knowledge } from './types';
import type { SceneKnowledge, ScenePreview, SceneDiff, EntityDiff, TaskMode } from './sceneTypes';
const ajv = new Ajv2020({allErrors:true,strict:false});
const validate = ajv.compile(schema);
function relaxed(value:unknown):unknown {
  if(Array.isArray(value)) return value.map(relaxed);
  if(value && typeof value==='object') return Object.fromEntries(Object.entries(value).filter(([k])=>!['minLength','maxLength','minItems','maxItems','minimum','maximum','exclusiveMinimum','exclusiveMaximum','pattern'].includes(k)).map(([k,v])=>[k,k==='type'&&v==='integer'?'number':relaxed(v)]));
  return value;
}
const formValidate=ajv.compile(relaxed(schema) as object);
export function upgradeLegacy(value:Knowledge):SceneKnowledge {
  const map=Object.fromEntries(value.sop.nodes.map((n,i)=>[n.action_id,`node_${String(i+1).padStart(3,'0')}_${sha256(n.action_id).slice(0,12)}`]));
  const {sop,...rest}=value;
  return {...rest,schema_version:'3.1',task_modes:['activity_recognition','procedure_compliance'],states:[],events:[],workflow:{...sop,nodes:sop.nodes.map(n=>({...n,node_id:map[n.action_id],state_id:null})),edges:sop.edges.map(e=>({from_node:map[e.from_action],to_node:map[e.to_action]})),repeatable_edges:sop.repeatable_edges.map(e=>({from_node:map[e.from_action],to_node:map[e.to_action]}))}};
}
export function parseScene(source:string):SceneKnowledge {
  let value=parseYaml(source) as SceneKnowledge|Knowledge;
  if(value?.schema_version==='3.0') {
    const result=previewKnowledge(source,value);
    if(!result.valid) throw new Error('旧知识包未通过校验：'+result.errors.map(e=>e.message).join('；'));
    value=upgradeLegacy(value);
  }
  return value as SceneKnowledge;
}
export function editableScene(source:string):SceneKnowledge|null {
  try {const value=parseScene(source); return formValidate(value)?value:null;} catch {return null;}
}
function entityDiff(old:Record<string,unknown>,next:Record<string,unknown>):EntityDiff {
  const a=Object.keys(old), b=Object.keys(next);
  return {added:b.filter(k=>!(k in old)).sort(),removed:a.filter(k=>!(k in next)).sort(),changed:a.filter(k=>k in next&&canonical(old[k])!==canonical(next[k])).sort().map(k=>({id:k,fields:Object.keys(old[k] as object).filter(f=>canonical((old[k] as Record<string,unknown>)[f])!==canonical((next[k] as Record<string,unknown>)[f])).sort()}))};
}
const keyed=(items:{id:string}[])=>Object.fromEntries(items.map(x=>[x.id,x]));
export function sceneDiff(before:SceneKnowledge,after:SceneKnowledge):SceneDiff {
  return {...entityDiff(keyed(before.actions),keyed(after.actions)),sop_changed:canonical(before.workflow)!==canonical(after.workflow),
    metadata_changed:Object.keys(before).filter(k=>!['actions','workflow'].includes(k)&&canonical(before[k as keyof SceneKnowledge])!==canonical(after[k as keyof SceneKnowledge])).sort(),
    states:entityDiff(keyed(before.states),keyed(after.states)),events:entityDiff(keyed(before.events),keyed(after.events)),
    workflow_nodes:entityDiff(Object.fromEntries((before.workflow?.nodes??[]).map(n=>[n.node_id,n])),Object.fromEntries((after.workflow?.nodes??[]).map(n=>[n.node_id,n])))};
}
export function previewScene(source:string,base:SceneKnowledge,modes:TaskMode[]=base.task_modes):ScenePreview {
  const errors:ScenePreview['errors']=[], add=(path:string,message:string)=>errors.push({path,message});
  const fail=():ScenePreview=>({valid:false,errors,hash:null,diff:null,data:null,impact:[]});
  let data:SceneKnowledge;
  try {data=parseScene(source);} catch(e) {add('yaml',(e as Error).message);return fail();}
  if(!validate(data)) {for(const e of validate.errors??[]) add(e.instancePath||'/',e.message??'字段无效');return fail();}
  if(data.scene_id!==base.scene_id) add('scene_id','场景不一致');
  if(!data.knowledge_version.trim()) add('knowledge_version','版本不能为空');
  if(new Set(data.task_modes).size!==data.task_modes.length||canonical([...data.task_modes].sort())!==canonical([...modes].sort())) add('task_modes','任务模式与场景注册不一致');
  if(data.approval_status==='approved'&&!data.approved_by?.trim()) add('approved_by','正式知识需要批准人');
  for(const key of ['actions','states','events'] as const) {
    const ids=new Set<string>(); for(const item of data[key]) {if(ids.has(item.id)||!item.id.trim()||!item.name.trim()) add(key,'重复ID或空名称');ids.add(item.id);}
  }
  const actions=new Map(data.actions.map(a=>[a.id,a])),states=new Map(data.states.map(s=>[s.id,s]));
  for(const a of data.actions) {
    if(a.confusable_with.some(id=>!actions.has(id))) add(`actions/${a.id}`,'混淆类引用不存在');
    const d=a.duration_prior_seconds;if(d!==null&&(d.length!==2||d[0]<0||d[0]>d[1])) add(`actions/${a.id}`,'duration范围无效');
    if([...a.positive_examples,...a.negative_examples,...a.prototype_refs].length) add(`actions/${a.id}`,'样本与原型登记库尚未接入');
  }
  if(data.curated_memory.length) add('curated_memory','机器记忆登记库尚未接入');
  if(data.task_modes.includes('procedure_compliance')&&!data.workflow) add('workflow','流程合规需要workflow');
  const wf=data.workflow;
  if(wf) {
    const graph=new Map<string,string[]>();
    for(const n of wf.nodes) {
      if(graph.has(n.node_id)) add('workflow/nodes','重复node_id');graph.set(n.node_id,[]);
      if((n.action_id===null)===(n.state_id===null)) add('workflow/nodes','节点须且仅引用动作或状态之一');
      const ref=n.action_id!==null?actions.get(n.action_id):states.get(n.state_id!);
      if(!ref) add('workflow/nodes','节点引用不存在');
      if(ref&&data.approval_status==='approved'&&ref.status!=='approved') add('workflow/nodes','正式规范不能引用草案');
      if(n.min_count>n.max_count) add('workflow/nodes','min_count大于max_count');
    }
    const pairs=new Set<string>();
    for(const e of [...wf.edges,...wf.repeatable_edges]) {
      if(!graph.has(e.from_node)||!graph.has(e.to_node)) add('workflow/edges','边引用不存在');
      const p=canonical(e);if(pairs.has(p)) add('workflow/edges','重复或普通/返工混用边');pairs.add(p);
    }
    for(const e of wf.edges) graph.get(e.from_node)?.push(e.to_node);
    const active=new Set<string>(),done=new Set<string>();let cycle=false;
    function visit(n:string) {if(active.has(n)){cycle=true;return;}if(done.has(n))return;active.add(n);for(const next of graph.get(n)??[])visit(next);active.delete(n);done.add(n);}
    for(const n of graph.keys())visit(n);if(cycle)add('workflow/edges','未声明的流程环路');
    if(wf.ordering==='unordered'&&wf.edges.length)add('workflow/edges','无序流程不得有顺序边');
  }
  for(const event of data.events) {
    const rule=event.trigger;
    const refs=rule.type==='state_transition'?[states.get(rule.from_state),states.get(rule.to_state)]:rule.type==='time_evidence'||rule.type==='object_interaction'?[actions.get(rule.action_id)]:[];
    if(refs.some(r=>!r))add('events','规则引用不存在');
    if(event.status==='approved'&&refs.some(r=>r&&r.status!=='approved'))add('events','正式规则不能引用草案');
  }
  if(errors.length)return fail();
  return {valid:true,errors,hash:contentHash(data),data,diff:sceneDiff(base,data),impact:['仅保存不可变候选，不激活、不修改场景基线。','动作、事件与流程变更需要独立核验；当前尚无视频、模型或真实效果。']};
}
