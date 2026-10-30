import type {ReadinessReport} from './readinessTypes';

const reasons:Record<string,string>={future_not_available:'尾部未来特征不足',target_outside_tolerance:'目标超出时间容差',quality_or_continuity_gap:'质量或连续性屏障',resource_subsampled:'资源上限抽样'};
const seconds=(ms:number)=>`${ms/1000} 秒`;

export default function SamplingDiagnostics({report}:{report:Pick<ReadinessReport,'sampling_plan'|'records'>}){
 const plan=report.sampling_plan,records=report.records.filter(r=>r.sampling_diagnostics);
 if(!plan&&!records.length)return null;
 return <section className="sampling-diagnostics" aria-label="采样与预测跨度">
  <h4>采样与预测跨度</h4>
  {plan&&<>
   <p>当前配置的理想网格：{plan.compatible_grid?'时间约束相容':'时间约束不相容'}。片段 {seconds(plan.clip_ms)}，步长 {seconds(plan.stride_ms)}；{plan.policy.history_windows} 个历史片段覆盖 {seconds(plan.history_span_ms)}。</p>
   <p>预测跨度 {plan.policy.horizons_ms.map(seconds).join('、')}，目标容差 {seconds(plan.policy.target_tolerance_ms)}。产生一个完整因果窗口至少需要 {seconds(plan.minimum_episode_ms)} 的连续覆盖，并须满足时间容差与质量约束。</p>
   <p className="ingest-note">{plan.note}</p>
  </>}
  {records.map(r=>{const d=r.sampling_diagnostics!;return <details key={r.import_id}>
   <summary>来源 {d.source_hash.slice(0,12)}：{d.eligible_rows} 个有效片段 → {d.causal_windows} 个因果窗口</summary>
   <p className="ingest-note">按此来源的实际时间戳检查。观察窗口 {d.observation_windows} 个，已核验业务周期 {d.verified_business_cycles} 个。计数不代表训练准入或学习效果。</p>
   {Object.entries(d.rejected_windows).map(([reason,count])=><p key={reason}>{reasons[reason]??reason}：{count}</p>)}
   <div className="ingest-table-wrap"><table><caption>各预测跨度单独检查，不能相加作为训练样本数</caption><thead><tr><th>跨度</th><th>可用窗口（抽样前）</th><th>首个历史窗口的目标偏差</th></tr></thead><tbody>{d.horizons.map(h=><tr key={h.horizon_ms}><td>{seconds(h.horizon_ms)}</td><td>{h.uncapped_causal_windows}</td><td>{h.first_history_example?.overshoot_ms!=null?seconds(h.first_history_example.overshoot_ms):'没有可用目标示例'}</td></tr>)}</tbody></table></div>
   {d.messages.map(message=><p className="ingest-note" key={message}>{message}</p>)}
  </details>;})}
  <p className="ingest-note">封存测试不提供采样选参诊断；独立评估按已固定候选模型的协议检查。</p>
 </section>;
}
