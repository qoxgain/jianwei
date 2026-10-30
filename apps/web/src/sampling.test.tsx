import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import SamplingDiagnostics from './SamplingDiagnostics';
import type {ReadinessReport,SamplingDiagnostics as Diagnostic} from './readinessTypes';

const diagnostic:Diagnostic={source_hash:'abc123456789000',eligible_rows:59,causal_windows:0,uncapped_causal_windows:0,
 observation_windows:2,verified_business_cycles:0,rejected_windows:{target_outside_tolerance:37,future_not_available:8},messages:['未来目标超出时间容差'],
 horizons:[{horizon_ms:500,uncapped_causal_windows:0,first_history_example:{issued_ms:16000,requested_start_ms:16500,actual_start_ms:18000,overshoot_ms:1500}}]};
function report(d?:Diagnostic):Pick<ReadinessReport,'sampling_plan'|'records'>{return {records:d?[{import_id:'recorded',source_hash:d.source_hash,split:'unassigned',data_origin:'real_video',missing_records:['captured_at'],issues:[],sampling_diagnostics:d}]:[]};}

describe('sampling explanations',()=>{
 it('preserves older offline reports with no sampling fields',()=>{expect(renderToStaticMarkup(<SamplingDiagnostics report={report()}/>)).toBe('');});
 it('explains actual zero windows and never equates observed windows with business cycles',()=>{const html=renderToStaticMarkup(<SamplingDiagnostics report={report(diagnostic)}/>);for(const text of ['59 个有效片段 → 0 个因果窗口','目标超出时间容差','1.5 秒','已核验业务周期 0 个','计数不代表训练准入或学习效果','封存测试不提供采样选参诊断'])expect(html).toContain(text);});
 it('keeps positive windows separate from research admission',()=>{const html=renderToStaticMarkup(<SamplingDiagnostics report={report({...diagnostic,eligible_rows:37,causal_windows:16})}/>);expect(html).toContain('37 个有效片段 → 16 个因果窗口');expect(html).toContain('计数不代表训练准入或学习效果');expect(html).not.toContain('训练准入通过');});
 it('labels the current configured grid separately from recorded data',()=>{const r=report(diagnostic);r.sampling_plan={clip_ms:2000,stride_ms:500,frames_per_clip:16,history_span_ms:5500,minimum_episode_ms:12500,compatible_grid:true,policy:{history_windows:8,horizons_ms:[500,2000,5000],target_tolerance_ms:1000,policy_hash:'hash'},note:'配置变更不会改变已有缓存，改变采样须重提特征。'};const html=renderToStaticMarkup(<SamplingDiagnostics report={r}/>);expect(html).toContain('当前配置的理想网格：时间约束相容');expect(html).toContain('12.5 秒');expect(html).toContain('按此来源的实际时间戳检查');expect(html).toContain('改变采样须重提特征');});
});
