import {describe,it,expect,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
vi.hoisted(()=>{Object.assign(globalThis,{location:{protocol:'file:',search:''}});});
import LocalMotionPanel from './LocalMotionPanel';
import data from './data/localMotionDemo.json';
import {blankReview,parseMotionReport,parseReview,roiDraft,validBox,latestDecisions,candidateEvaluationNote} from './localMotion';

const report=()=>parseMotionReport(JSON.stringify(data));
describe('B.1 local dynamics evidence and independent human records',()=>{
 it('renders executed synthetic evidence, privacy, fixed primary and no action F1',()=>{
  const html=renderToStaticMarkup(<LocalMotionPanel/>);
  expect(html).toContain('合成素材 · 工程演示');expect(html).toContain('无完整人工边界');expect(html).toContain('原片不会上传');
  expect(html).toContain('导出ROI配置草稿');expect(html).toContain('全部阈值和平滑组合');
 });
 it('rejects unsupported report types, sealed-style admission and nonfinite curves',()=>{
  const r=structuredClone(data) as any;r.formal_research_passed=true;expect(()=>parseMotionReport(JSON.stringify(r))).toThrow();
  r.formal_research_passed=false;r.arms[0].series[0].points[0].pts_ms=-1;expect(()=>parseMotionReport(JSON.stringify(r))).toThrow();
 });
 it('binds review to source, feature recipe and exact experiment',()=>{
  const r=report();const d=blankReview(r);d.source_sha256='b'.repeat(64);expect(()=>parseReview(JSON.stringify(d),r)).toThrow('不匹配');
 });
 it('keeps confirm/revise/delete events without editing original predictions',()=>{
  const r=report(),original=JSON.stringify(r),d=blankReview(r),c=r.primary.candidates[0];expect(c).toBeTruthy();
  d.events=[{id:'e1',target_id:c.id,action:'confirm',kind:'unspecified',note:''},{id:'e2',target_id:c.id,action:'adjust',pts_ms:c.pts_ms+100,kind:'onset',note:''},{id:'e3',target_id:c.id,action:'delete',kind:'unspecified',note:''}];
  const parsed=parseReview(JSON.stringify(d),r);expect(latestDecisions(parsed).get(c.id)?.action).toBe('delete');expect(JSON.stringify(r)).toBe(original);
  expect(candidateEvaluationNote(r,true)).toContain('旧指标失效');
 });
 it('rejects empty/out-of-bounds/global-cropped ROI drafts',()=>{
  expect(validBox([0,0,0,1])).toBe(false);expect(validBox([0,0,1.1,1])).toBe(false);
  const r=report(),rois=structuredClone(r.settings.rois);rois[0].xyxy=[.1,0,1,1];expect(()=>roiDraft(r,rois)).toThrow();
 });
 it('exports new ROI settings without pretending existing curves were recomputed',()=>{
  const r=report(),rois=structuredClone(r.settings.rois);rois[1].xyxy=[.1,.1,.8,.8];const draft=roiDraft(r,rois);
  expect(draft.local_motion.rois[1].xyxy).toEqual([.1,.1,.8,.8]);expect(r.settings.rois[1].xyxy).not.toEqual(rois[1].xyxy);
 });
});
