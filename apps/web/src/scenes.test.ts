import {beforeEach,describe,it,expect,vi} from 'vitest';
import {dumpYaml,parseYaml,contentHash} from './validation';
import {editableScene,previewScene,parseScene} from './sceneValidation';
import type {SceneKnowledge} from './sceneTypes';
import legacyYaml from '../../../knowledge/labels.yaml?raw';
const examples=import.meta.glob('../../../knowledge/scenes/*.yaml',{eager:true,query:'?raw',import:'default'}) as Record<string,string>;
const assembly=parseScene(examples['../../../knowledge/scenes/electronics_assembly.yaml']);
const anonymous=parseScene(examples['../../../knowledge/scenes/anonymous_observation.yaml']);

describe('four shared scenario contracts',()=>{
 it('accepts every supplied scene including empty labels without a workflow',()=>{for(const text of Object.values(examples)){const base=parseScene(text);expect(previewScene(text,base).valid).toBe(true);}expect(anonymous.actions).toHaveLength(0);expect(anonymous.workflow).toBeNull();});
 it('separates repeated action semantics from node identity and diff',()=>{const next=structuredClone(assembly);expect(next.workflow!.nodes.filter(n=>n.action_id==='inspect')).toHaveLength(2);next.workflow!.nodes[2].min_count=0;const p=previewScene(dumpYaml(next),assembly);expect(p.valid).toBe(true);expect(p.diff?.workflow_nodes.changed).toEqual([{id:'node_3',fields:['min_count']}]);next.workflow!.nodes[2].node_id='node_1';expect(previewScene(dumpYaml(next),assembly).valid).toBe(false);});
 it('rejects missing compliance workflow, forged modes and cross-scene references',()=>{for(const mutate of [(v:SceneKnowledge)=>{v.workflow=null;},(v:SceneKnowledge)=>{v.task_modes=['activity_recognition'];},(v:SceneKnowledge)=>{v.workflow!.nodes[0].action_id='other.action';}]){const v=structuredClone(assembly);mutate(v);expect(previewScene(dumpYaml(v),assembly).valid).toBe(false);}});
 it('keeps semantic drafts editable but never drops unknown fields',()=>{const v=structuredClone(assembly);v.actions[0].name='';expect(editableScene(dumpYaml(v))).not.toBeNull();expect(previewScene(dumpYaml(v),assembly).valid).toBe(false);expect(editableScene(dumpYaml({...v,typo:true}))).toBeNull();});
 it('upgrades legacy knowledge explicitly, preserving action IDs',()=>{const legacy=parseYaml(legacyYaml) as {actions:{id:string}[]};const next=parseScene(legacyYaml);expect(next.actions.map(a=>a.id)).toEqual(legacy.actions.map(a=>a.id));expect(next.workflow!.nodes.every(n=>n.node_id!==n.action_id)).toBe(true);expect(previewScene(legacyYaml,next).valid).toBe(true);});
 it('cannot turn a draft rule into an approved event or execute arbitrary rule types',()=>{const base=parseScene(examples['../../../knowledge/scenes/workshop_safety.yaml']);const v=structuredClone(base);v.events[0].status='approved';expect(previewScene(dumpYaml(v),base).valid).toBe(false);const raw=JSON.parse(JSON.stringify(base));raw.events[0].trigger={type:'execute_script',script:'do not execute'};expect(previewScene(dumpYaml(raw),base).valid).toBe(false);});
});

let storage=new Map<string,string>();
beforeEach(()=>{vi.resetModules();storage=new Map();vi.stubGlobal('location',{protocol:'file:',search:''});vi.stubGlobal('localStorage',{getItem:(k:string)=>storage.get(k)??null,setItem:(k:string,v:string)=>storage.set(k,v)});});
describe('scene-scoped offline persistence',()=>{
 it('isolates all four scenes and preserves baseline hashes after refresh',async()=>{const s=await import('./sceneService');const root=await s.bootstrapScene();for(const scene of root.scenes){const base=await s.bootstrapScene(scene.id);const next=structuredClone(base.knowledge.data);next.knowledge_version+='-reviewed';const text=dumpYaml(next);const saved=await s.saveSceneCandidate(scene.id,text,base.knowledge.hash);expect(saved.created).toBe(true);expect((await s.saveSceneCandidate(scene.id,text,base.knowledge.hash)).created).toBe(false);expect((await s.bootstrapScene(scene.id)).knowledge.hash).toBe(base.knowledge.hash);const other=root.scenes.find(v=>v.id!==scene.id)!;await expect(s.sceneVersionDetails(other.id,saved.version.id)).rejects.toThrow();}vi.resetModules();const restarted=await import('./sceneService');for(const scene of root.scenes)expect((await restarted.sceneVersions(scene.id)).length).toBe(2);});
 it('rejects stale baselines and mode changes before storing',async()=>{const s=await import('./sceneService');const b=await s.bootstrapScene('anonymous_observation');await expect(s.saveSceneCandidate(b.active_scene,b.knowledge.yaml,'old')).rejects.toThrow();const raw=structuredClone(b.knowledge.data);raw.task_modes=['procedure_compliance'];await expect(s.saveSceneCandidate(b.active_scene,dumpYaml(raw),b.knowledge.hash)).rejects.toThrow();expect(storage.size).toBe(0);});
 it('retains old browser candidates and exposes an explicit migrated hash',async()=>{const raw=parseYaml(legacyYaml) as {actions:{name:string}[]};raw.actions[0].name='旧候选';const text=dumpYaml(raw);const oldHash=contentHash(raw);storage.set('jianwei.p0.1.candidates.v1',JSON.stringify([{id:'legacy',status:'candidate',hash:oldHash,yaml:text,diff:{changed:[]}}]));const s=await import('./sceneService');const row=await s.sceneVersionDetails('garment_sop','legacy');expect(row.hash).toBe(oldHash);expect(row.migration?.normalized_hash).not.toBe(oldHash);expect(storage.has('jianwei.p0.1.candidates.v1')).toBe(true);expect((await s.sceneVersions('anonymous_observation')).length).toBe(1);});
});

import parity from '../../../tests/fixtures/v31-parity.json';
describe('Python/TypeScript contract parity',()=>{
 for(const c of parity.cases)it(c.name,()=>{const base=parity.baselines[c.scene_id as keyof typeof parity.baselines] as SceneKnowledge;const result=previewScene(dumpYaml(c.value),base);expect(result.valid).toBe(c.valid);expect(result.hash).toBe(c.hash);expect(result.diff).toEqual(c.diff);});
});
