import {describe,it,expect,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
vi.hoisted(()=>{Object.assign(globalThis,{location:{protocol:'file:',search:''}});});
import AnnotationPanel from './AnnotationPanel';
import packet from './data/annotationDemoSource.json';
import {append,blankAnnotation,compile,digest,expose,fork,frameAt,gaps,parseAnnotation,parseSource,raterAgreement,seal} from './annotation';

const source=()=>parseSource(JSON.stringify(packet));
function draft(){const d=blankAnnotation(source());d.annotator='合成工程测试，不是真人';return d;}
function complete(){let d=draft();d=append(d,'interval_add',{pts_ms:0,end_ms:12000,kind:'reviewed'});d=append(d,'boundary_add',{pts_ms:3000,kind:'onset'});return seal({...d,coverage_complete:true,human_attestation:true},source());}

describe('B.1.1 independent source-only annotation',()=>{
 it('renders no candidates or response curves and clearly blocks real metrics',()=>{
  const html=renderToStaticMarkup(<AnnotationPanel/>);expect(html).toContain('合成素材 · 标注工程演示');expect(html).toContain('真实Precision / Recall / F1保持blocked');
  expect(html).not.toContain('lm-chart');expect(html).not.toContain('lm-candidates');expect(html).toContain('不以播放时长自动充当审核证据');
 });
 it('verifies cross-language original source digest and rejects report/unknown data',()=>{
  expect(source().descriptor_hash).toBe(packet.descriptor_hash);expect(()=>parseSource(JSON.stringify({...packet,schema:'b1-local-motion-report-v1'}))).toThrow();
  const {descriptor_hash,...body}=packet;const extra={...body,candidates:[]};expect(()=>parseSource(JSON.stringify({...extra,descriptor_hash:digest(extra)}))).toThrow('算法');
 });
 it('keeps holes and the half-second tail visible',()=>{expect(gaps(0,12000,[[0,11500]])).toEqual([[11500,12000]]);});
 it('seals only complete coverage and rejects mutated revisions',()=>{
  const d=draft();expect(()=>seal({...d,coverage_complete:true,human_attestation:true},source())).toThrow('补齐');
  const frozen=complete();expect(compile(frozen,source()).eligible).toBe(true);
  frozen.events[0].note='mutated';expect(()=>compile(frozen,source())).toThrow('封存');
 });
 it('persists add, update, review and delete separately from the current truth',()=>{
  let d=draft();d=append(d,'boundary_add',{pts_ms:1000,kind:'onset'});const id=d.events[0].id;
  d=append(d,'boundary_update',{target_id:id,pts_ms:1100,kind:'offset'});d=append(d,'boundary_review',{target_id:id});
  expect(compile(d,source()).boundaries[0].reviewed_by.length).toBe(1);d=append(d,'boundary_delete',{target_id:id});
  expect(compile(d,source()).boundaries).toHaveLength(0);expect(d.events).toHaveLength(4);
 });
 it('cannot restore exposure as a blind draft and preserves a frozen pre-exposure revision',()=>{
  const d=expose(draft(),'viewed algorithm');expect(d.mode).toBe('assisted');expect(()=>compile({...d,mode:'blind'},source())).toThrow('盲标');
  const frozen=complete();expect(expose(frozen,'after freeze')).toEqual(frozen);const next=expose(fork(frozen),'after freeze');
  expect(next.mode).toBe('assisted');expect(next.parent_sealed_hash).toBe(frozen.sealed_hash);expect(frozen.status).toBe('sealed');
 });
 it('restores exact sealed documents and refuses cross-source annotations',()=>{
  const frozen=complete();expect(parseAnnotation(JSON.stringify(frozen),source())).toEqual(frozen);
  expect(()=>parseAnnotation(JSON.stringify({...frozen,source_sha256:'b'.repeat(64)}),source())).toThrow('不匹配');
 });
 it('uses actual PTS lookup rather than a guessed fps',()=>{
  const s=source(),f=s.frames[7];expect(frameAt(s,f.pts_ms)).toBe(7);expect(frameAt(s,f.end_ms-1)).toBe(7);expect(frameAt(s,f.end_ms)).toBe(8);
 });
 it('shows second-rater type disagreements without producing consensus truth',()=>{
  const a=complete();const b=structuredClone(a);b.annotation_id='second';b.annotator='第二合成测试员';b.status='draft';b.events[1].kind='offset';const frozen=seal(b,source());
  const r=raterAgreement(a,frozen,source());expect(r.matches).toHaveLength(1);expect(r.typeDisagreements).toHaveLength(1);expect(a.events[1].kind).toBe('onset');
 });
 it('unknown or invalid updates cannot append a fake successful state',()=>{
  const d=append(draft(),'boundary_add',{pts_ms:-1,kind:'onset'});expect(()=>compile(d,source())).toThrow();
  expect(()=>compile(append(draft(),'boundary_add',{pts_ms:1000,kind:'toString'}),source())).toThrow();
 });
});
