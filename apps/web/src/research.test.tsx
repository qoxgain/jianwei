import {beforeEach,describe,expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {ProtocolDetails} from './ResearchProtocolPanel';
import SessionStatistics from './SessionStatistics';
import {ReadinessPartitions} from './ReadinessPanel';
import readinessExample from './readinessExample.json';
import type {ReadinessReport} from './readinessTypes';
import type {ProtocolPreview,SessionStatistics as Statistics,SessionMetrics} from './researchTypes';
vi.hoisted(()=>{Object.defineProperty(globalThis,'location',{value:{protocol:'http:',search:''},configurable:true,writable:true});});

// Display-contract fixtures only; these numbers are not recorded experiments.
const metrics:SessionMetrics={normalized_feature_mse:[.1,.2],original_feature_cosine:[.9,.8],anonymous_state_nll:[.3,.4],anonymous_state_brier:[.2,.3]};
const policy={protocol_id:'display_fixture',registered_by:'fixture_reviewer',registered_at:'2026-10-01T08:00:00+08:00',primary_metric:'source_macro_mse' as const,min_independent_sessions:3,confidence_level:.95,bootstrap_replicates:200,bootstrap_seed:23,minimum_relative_improvement:.05,controls:['persistence','shuffled_ridge'] as ['persistence','shuffled_ridge']};
function preview():ProtocolPreview{return {stage:'P0.3.5',scene_id:'anonymous_observation',status:'blocked',protocol_hash:null,protocol:null,missing_fields:['research_protocol'],bindings:{config_hash:'fixture_hash'},rosters:[],blockers:[{code:'TEMPORAL_PROTOCOL_REQUIRED',message:'真实研究需先登记协议'}],metadata_only:true,feature_values_read:false,test_values_read:false,training_executed:false,jobs_created:false};}
function statistics():Statistics{return {contract_version:'p035-session-bootstrap-v1',split:'test',data_origin:'real_video',protocol_id:'display_fixture',protocol_hash:'fixture',selected_model:'ridge',source_files:1,independent_sessions:1,resampling_units:1,horizons_seconds:[.5,2],per_session:[{capture_session_id:'fixture_session_1',resampling_unit:'fixture_session_1',source_hash:'fixture_source_sha',metrics:{ridge:metrics},source_macro_mse:{ridge:.15}}],session_macro_metrics:{ridge:{normalized_feature_mse:{mean:[.1,.2],confidence_interval:null},original_feature_cosine:{mean:[.9,.8],confidence_interval:null},anonymous_state_nll:{mean:[.3,.4],confidence_interval:null},anonymous_state_brier:{mean:[.2,.3],confidence_interval:null},source_macro_mse:{mean:.15,confidence_interval:null}}},comparisons:{persistence:{baseline_mean:.4,selected_model_mean:.15,relative_improvement:.625,confidence_interval:null,status:'insufficient_sessions'},shuffled_ridge:{baseline_mean:.5,selected_model_mean:.15,relative_improvement:.7,confidence_interval:null,status:'insufficient_sessions'}},uncertainty:{status:'insufficient_sessions',method:'paired_session_percentile_bootstrap',confidence_level:.95,bootstrap_replicates:200,bootstrap_seed:23,minimum_sessions:3,interval_scope:'display fixture only'},gate:{status:'blocked',reasons:['insufficient_independent_sessions'],primary_metric:'source_macro_mse',minimum_relative_improvement:.05,real_effect_pass:false,auto_activated:false,p04_admitted:false},limitations:['固定模型的区间不保证样本代表性。']};}

describe('readonly research protocol display',()=>{
 it('shows missing configuration and the registration boundary without invented approval',()=>{const html=renderToStaticMarkup(<ProtocolDetails report={preview()}/>);for(const text of ['待登记','真实研究需先登记协议','temporal.research_protocol','实际冻结记录随训练任务保存','不读取特征值'])expect(html).toContain(text);expect(html).not.toContain('配置已登记');});
 it('shows prespecified two-control strict threshold without claiming research passed',()=>{const report=preview();report.protocol=policy;report.status='configured';report.missing_fields=[];report.blockers=[];const html=renderToStaticMarkup(<ProtocolDetails report={report}/>);for(const text of ['配置已登记','均严格大于 5.0%','至少 3 个独立会话','不表示真实学习效果通过','fixture_reviewer'])expect(html).toContain(text);});
 it('keeps configured protocol and unreviewed source conditions distinct',()=>{const report=preview();report.protocol=policy;report.missing_fields=['capture_session_id'];const html=renderToStaticMarkup(<ProtocolDetails report={report}/>);expect(html).toContain('来源条件待补齐');expect(html).not.toContain('配置已登记');});
 it('does not imply offline configuration or hashes were read from a server',()=>{const report=preview();report.offline=true;const html=renderToStaticMarkup(<ProtocolDetails report={report}/>);expect(html).toContain('离线未核验');expect(html).toContain('无法核验服务器研究配置');expect(html).not.toContain('fixture_hash');});
});

describe('independent-session evidence display',()=>{
 it('does not turn a positive point improvement into acceptance when session count is insufficient',()=>{const html=renderToStaticMarkup(<SessionStatistics statistics={statistics()}/>);for(const text of ['不足以评估','1 个已核验独立会话','62.5%','独立会话数不足，不输出置信区间','区间未估计','0.5 秒','2 秒','fixture_session_1'])expect(html).toContain(text);expect(html).not.toContain('达到门槛</td>');});
 it('renders zero-baseline comparison as undefined instead of a confidence interval or infinity',()=>{const data=statistics();data.comparisons.persistence={baseline_mean:0,selected_model_mean:.1,relative_improvement:null,confidence_interval:null,status:'undefined_baseline'};data.gate.reasons=['undefined_control_baseline'];const html=renderToStaticMarkup(<SessionStatistics statistics={data}/>);expect(html).toContain('相对改善未定义');expect(html).toContain('对照接近零');expect(html).not.toMatch(/NaN|Infinity/);});
 it('keeps synthetic mechanism records separate from verified acquisition sessions',()=>{const data=statistics();data.data_origin='synthetic_features';data.independent_sessions=0;data.gate.status='mechanism_only';data.gate.reasons=['synthetic_data_not_real_effect_evidence'];data.per_session[0].capture_session_id=null;const html=renderToStaticMarkup(<SessionStatistics statistics={data}/>);expect(html).toContain('合成机制验证 · 真实效果未验收');expect(html).toContain('已核验真实采集会话 0 个');expect(html).toContain('逐合成来源');expect(html).not.toContain('fixture_session_1');});
 it('shows fixed-model failures and successful intervals without automatically admitting P0.4',()=>{const data=statistics();data.uncertainty.status='estimated';data.gate.status='failed';data.gate.reasons=['pre_registered_control_threshold_not_met'];data.comparisons.persistence.confidence_interval={lower:.1,upper:.3};data.comparisons.persistence.status='passed';data.comparisons.shuffled_ridge.confidence_interval={lower:-.1,upper:.2};data.comparisons.shuffled_ridge.status='failed';const html=renderToStaticMarkup(<SessionStatistics statistics={data}/>);for(const text of ['未达到预注册门槛','[10.0%, 30.0%]','[-10.0%, 20.0%]','不自动进入 P0.4','不改变候选或研究结论'])expect(html).toContain(text);});
 it('labels calibration as non-final even when descriptive values are present',()=>{const data=statistics();data.split='calibration';data.gate.status='not_final';data.gate.reasons=['calibration_not_final_test'];const html=renderToStaticMarkup(<SessionStatistics statistics={data}/>);expect(html).toContain('校准结果 · 尚非最终评估');expect(html).toContain('最终效果须在独立封存数据上评估');});
});

describe('sealed-window precheck boundary',()=>{
 it('shows withheld test counts as uninspected, while retaining a real zero for training',()=>{
  const report=structuredClone(readinessExample.scenes.anonymous_observation) as ReadinessReport;
  report.evaluation.windows_checked=false;report.partitions.test.causal_windows=null;report.partitions.test.sources_with_causal_windows=null;report.partitions.train.causal_windows=0;
  const html=renderToStaticMarkup(<ReadinessPartitions report={report}/>);
  expect(html).toContain('未检查</td>');expect(html).toContain('不能用测试窗口选择采样或预测跨度');expect(html).toContain('<td>0</td>');expect(html).not.toContain('null');
 });
 it('retains historical numeric reports without relabeling them as a new precheck',()=>{
  const report=structuredClone(readinessExample.scenes.anonymous_observation) as ReadinessReport;
  const html=renderToStaticMarkup(<ReadinessPartitions report={report}/>);
  expect(html).not.toContain('封存测试窗口未检查');expect(html).toContain(String(report.partitions.test.causal_windows));
 });
});

beforeEach(()=>{vi.resetModules();vi.stubGlobal('location',{protocol:'http:',search:''});});
describe('protocol preview transport',()=>{
 it('makes one scoped metadata GET without creating a job or writing configuration',async()=>{const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>preview()});vi.stubGlobal('fetch',fetch);const service=await import('./temporalService');const result=await service.researchProtocol('anonymous_observation');expect(result.metadata_only).toBe(true);expect(fetch).toHaveBeenCalledTimes(1);expect(fetch.mock.calls[0][0]).toBe('/api/scenes/anonymous_observation/temporal/protocol');expect(fetch.mock.calls[0][1].method).toBe('GET');expect(fetch.mock.calls[0][1].body).toBeUndefined();});
 it('keeps the offline preview explicitly unverified without network calls',async()=>{vi.stubGlobal('location',{protocol:'file:',search:''});const fetch=vi.fn();vi.stubGlobal('fetch',fetch);const service=await import('./temporalService');const result=await service.researchProtocol('anonymous_observation');expect(result.offline).toBe(true);expect(result.protocol_hash).toBeNull();expect(result.protocol).toBeNull();expect(fetch).not.toHaveBeenCalled();});
});
