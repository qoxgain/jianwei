import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
vi.hoisted(()=>{vi.stubGlobal('location',{protocol:'http:',search:''});});
import {evidenceInterval,reviewerIdentity,toggleSection} from './sopService';
describe('human SOP review boundaries',()=>{
 it('keeps mappings many-to-many without mutating other station candidates',()=>{
  const rows=[{asset_id:'station_1',section_ids:['DGC01'],notes:'待核实'},{asset_id:'station_2',section_ids:['DGC01'],notes:''}];
  const added=toggleSection(rows,'station_1','DGC02',true);expect(added[0].section_ids).toEqual(['DGC01','DGC02']);expect(added[1]).toBe(rows[1]);expect(rows[0].section_ids).toEqual(['DGC01']);
  expect(toggleSection(added,'station_1','DGC02',true)[0].section_ids).toEqual(['DGC01','DGC02']);expect(toggleSection(added,'station_1','DGC01',false)[0].section_ids).toEqual(['DGC02']);
 });
 it('requires traceable reviewer identity and an explanation',()=>{expect(reviewerIdentity(' human_1 ','  复核原文  ')).toEqual({reviewer:'human_1',reason:'复核原文'});for(const [name,reason] of [['','x'],['unknown name','x'],['human_1','  ']])expect(()=>reviewerIdentity(name,reason)).toThrow();});
 it('bounds references to real source time with millisecond resolution',()=>{
  expect(evidenceInterval('1.25','2.5',20.44)).toEqual({start_ms:1250,end_ms:2500});
  for(const [start,end] of [['','2'],['-1','2'],['2','1'],['0','20.441'],['0','Infinity'],['0.0001','0.0002']])expect(()=>evidenceInterval(start,end,20.44)).toThrow();
 });
});
beforeEach(()=>{vi.resetModules();vi.stubGlobal('location',{protocol:'http:',search:''});});
afterEach(()=>vi.unstubAllGlobals());
describe('SOP review transport',()=>{
 it('sends distinct revision, decision and evidence records with the reviewed base version',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({revision:'a'.repeat(64),activated:false,is_ground_truth:false})});vi.stubGlobal('fetch',fetch);
  const service=await import('./sopService');const revision={base_revision:'a'.repeat(64),reviewer:'human_1',reason:'核对原文',mappings:[{asset_id:'station_1',section_ids:['DGC01','DGC02'],notes:''}],disputes:[]};
  await service.saveSopRevision('electronics_assembly',revision);await service.decideSop('electronics_assembly',{revision:revision.base_revision,action:'approve',reviewer:'human_1',reason:'映射已核对'});await service.recordSopEvidence('electronics_assembly',{revision:revision.base_revision,kind:'prediction',asset_id:'station_1',section_id:'DGC01',start_ms:0,end_ms:1000,producer:'model_1',note:'尚待观测验证',evidence_type:'visible_operation',evidence_reference:null});
  expect(fetch.mock.calls.map(c=>c[0])).toEqual(['/api/scenes/electronics_assembly/sop/revisions','/api/scenes/electronics_assembly/sop/decisions','/api/scenes/electronics_assembly/sop/evidence']);expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(revision);expect(JSON.parse(fetch.mock.calls[2][1].body).kind).toBe('prediction');
 });
 it('shows version conflict without silently retrying or overwriting newer review',async()=>{const fetch=vi.fn().mockResolvedValue({ok:false,status:409,json:async()=>({detail:{message:'SOP 复核版本已变化'}})});vi.stubGlobal('fetch',fetch);const service=await import('./sopService');await expect(service.decideSop('electronics_assembly',{revision:'a'.repeat(64),action:'revoke',reviewer:'human_1',reason:'复核'})).rejects.toThrow('版本已变化');expect(fetch).toHaveBeenCalledTimes(1);});
 it('never writes human decisions or fetches private media from offline demo',async()=>{vi.stubGlobal('location',{protocol:'file:',search:''});const fetch=vi.fn();vi.stubGlobal('fetch',fetch);const service=await import('./sopService');await expect(service.getSop('electronics_assembly')).rejects.toThrow('完整 API');await expect(service.decideSop('electronics_assembly',{revision:'a'.repeat(64),action:'approve',reviewer:'human_1',reason:'复核'})).rejects.toThrow('完整 API');expect(fetch).not.toHaveBeenCalled();});
});
