import {useEffect,useState} from 'react';
import {Download,LockKeyhole} from 'lucide-react';
import {downloadText} from './service';
import {researchProtocol} from './temporalService';
import type {ProtocolPreview} from './researchTypes';

const fields:Record<string,string>={research_protocol:'研究协议',dataset_manifest:'采集来源清单',protocol_id:'协议编号',registered_by:'登记人',registered_at:'登记时间',min_independent_sessions:'区间估计所需独立会话数',minimum_relative_improvement:'相对改善门槛'};
const splits:Record<string,string>={train:'训练',calibration:'校准',test:'封存测试'};
export function ProtocolDetails({report}:{report:ProtocolPreview}){
 const policy=report.protocol;
 return <div className="protocol-details">
  <p className="protocol-state"><span className={`pill ${report.status==='configured'?'teal':'amber'}`}>{report.offline?'离线未核验':!policy?'待登记':report.status==='configured'?'配置已登记':'来源条件待补齐'}</span><span>协议预览不运行训练、不读取特征值。</span></p>
  {report.offline?<p className="ingest-note">离线 Demo 无法核验服务器研究配置。连接本地 API 后可查看和导出当前协议；随附历史研究记录仍可查看。</p>:<>
   <p className="ingest-note">训练前，在 system.yaml 的 temporal.research_protocol 固定主要指标、会话数与通过门槛，并审核来源清单。开始训练时，服务端会将协议、来源分区和版本绑定到候选。修改协议后须使用新的封存测试资料。</p>
   {policy&&<><dl className="protocol-policy"><div><dt>协议编号</dt><dd>{policy.protocol_id}</dd></div><div><dt>登记人 / 时间</dt><dd>{policy.registered_by}<br/>{policy.registered_at}</dd></div><div><dt>主要指标</dt><dd>按会话平均 MSE · 越低越好</dd></div><div><dt>预先固定的通过标准</dt><dd>相对持久性、打乱目标两项对照的改善区间下界，均严格大于 {(policy.minimum_relative_improvement*100).toFixed(1)}%。</dd></div><div><dt>区间估计门槛</dt><dd>至少 {policy.min_independent_sessions} 个独立会话，{(policy.confidence_level*100).toFixed(1)}% 区间；按会话配对重采样 {policy.bootstrap_replicates} 次。</dd></div></dl><p className="ingest-note">会话数门槛用于限制计算，不保证统计充分性或样本代表性。界面登记状态不表示真实学习效果通过。</p></>}
   {!!report.missing_fields.length&&<p>待补记录：{report.missing_fields.map(field=>fields[field]??fields[field.split('.').at(-1)??'']??field).join('、')}。</p>}
   {report.blockers.map((issue,index)=><p className="protocol-blocker" key={issue.code+index}>{issue.message}<small>{issue.code}</small></p>)}
   {!!report.rosters.length&&<details><summary>查看固定来源分区</summary>{report.rosters.map(roster=><div key={roster.view_id} className="protocol-roster"><strong>视角 {roster.view_id}</strong><div className="ingest-table-wrap"><table><caption>来源清单只反映已登记记录；独立性仍需采集审查</caption><thead><tr><th>分区</th><th>源文件 SHA</th><th>采集会话</th></tr></thead><tbody>{Object.entries(roster.partitions).flatMap(([split,rows])=>rows.map(row=><tr key={split+row.source_sha256}><th>{splits[split]??split}</th><td><code title={row.source_sha256}>{row.source_sha256.slice(0,16)}</code></td><td>{row.capture_session_id??'待补齐'}</td></tr>))}</tbody></table></div></div>)}</details>}
   <details><summary>查看协议哈希与版本绑定</summary><dl className="ingest-facts"><div><dt>协议配置哈希</dt><dd><code className="ingest-hash">{report.protocol_hash??'待登记'}</code></dd></div></dl><pre>{JSON.stringify(report.bindings,null,2)}</pre><p className="ingest-note">这是当前配置的只读预览。实际冻结记录随训练任务保存，不能由预览代替。</p></details>
  </>}
 </div>;
}

export default function ResearchProtocolPanel({scene}:{scene:string}){
 const [state,setState]=useState<{scene:string;report:ProtocolPreview|null;error:string}>({scene,report:null,error:''});
 useEffect(()=>{let current=true;setState({scene,report:null,error:''});void researchProtocol(scene).then(report=>{if(current)setState({scene,report,error:''});}).catch(error=>{if(current)setState({scene,report:null,error:(error as Error).message});});return()=>{current=false;};},[scene]);
 const report=state.scene===scene?state.report:null,error=state.scene===scene?state.error:'';
 return <section className="panel research-protocol-panel" aria-label="预注册研究协议"><div className="panel-heading"><h2><LockKeyhole size={18}/>预注册研究协议</h2><button className="button secondary small" disabled={!report||report.offline} onClick={()=>{if(report)downloadText(`${scene}.research-protocol.json`,JSON.stringify(report,null,2));}}><Download size={14}/>导出研究协议</button></div><div className="jw-fields">{error?<p role="alert" className="ingest-alert">研究协议读取失败：{error}</p>:report?<ProtocolDetails report={report}/>:<p role="status">读取协议元数据…</p>}</div></section>;
}
