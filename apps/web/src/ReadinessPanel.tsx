import {useEffect,useRef,useState} from 'react';
import {ClipboardCheck,Download} from 'lucide-react';
import {isDemo} from './sceneService';
import {downloadText} from './service';
import {checkReadiness,datasetTemplate} from './temporalService';
import type {ReadinessReport} from './readinessTypes';
import SamplingDiagnostics from './SamplingDiagnostics';
const labels:Record<string,string>={train:'训练',calibration:'校准',test:'封存测试',unassigned:'未分区'};
const fields:Record<string,string>={dataset_manifest:'登记来源清单',capture_session_id:'采集会话',camera_id:'相机编号',view_id:'视角',captured_at:'采集时间',provenance_review:'来源审查',near_duplicate_review:'近重复审查',split:'固定分区'};

export default function ReadinessPanel({scene,ids,model}:{scene:string;ids:string[];model:string}){
 const [report,setReport]=useState<ReadinessReport|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const selection=JSON.stringify([scene,[...ids].sort(),model]),generation=useRef(0),current=useRef(selection);
 current.current=selection;
 useEffect(()=>{generation.current++;setReport(null);setError('');setNotice('');setBusy(false);return()=>{generation.current++;};},[selection]);
 async function run(kind:'check'|'template'){
  const runId=++generation.current,key=selection;setBusy(true);setError('');
  try{
   if(kind==='check'){
    const next=await checkReadiness(scene,ids,model||undefined);
    if(generation.current===runId&&current.current===key)setReport(next);
   }else{
    const draft=await datasetTemplate(scene,ids);
    if(generation.current===runId&&current.current===key){downloadText(`${scene}.dataset-draft.json`,JSON.stringify(draft,null,2));setNotice(draft.notice);}
   }
  }catch(e){if(generation.current===runId&&current.current===key)setError((e as Error).message);}
  finally{if(generation.current===runId&&current.current===key)setBusy(false);}
 }
 return <section className="readiness-panel" aria-label="研究条件预检">
  <h3><ClipboardCheck size={18}/>研究条件预检</h3>
  <p className="ingest-note">核对采集记录、分区和有效时间窗口。此检查不读取特征值，也不运行训练或封存测试。</p>
  <div className="button-row"><button className="button secondary" disabled={busy||(!isDemo&&!ids.length)} onClick={()=>void run('check')}>{isDemo?'查看预检示例':'检查选中来源'}</button>{!isDemo&&<button className="button secondary" disabled={busy||!ids.length} onClick={()=>void run('template')}>导出采集记录草稿</button>}</div>
  {busy&&<p role="status">检查元数据…</p>}{error&&<p className="ingest-alert" role="alert">{error}</p>}{notice&&<p className="ingest-note" role="status">{notice}</p>}
  {report&&<div className="readiness-result">
   <p className="ingest-note">{isDemo?'随附已执行的合成来源预检。 ':''}{report.note}</p>
   <SamplingDiagnostics report={report}/>
   <div className="ingest-table-wrap"><table><caption>按来源统计，相关窗口不算独立样本</caption><thead><tr><th>分区</th><th>源文件</th><th>已核验独立组</th><th>完整窗口</th><th>因果窗口</th></tr></thead><tbody>{Object.entries(report.partitions).map(([split,p])=><tr key={split}><th>{labels[split]??split}</th><td>{p.source_files}</td><td>{p.reviewed_independent_groups}{p.minimum_sources!==null?` / 至少 ${p.minimum_sources}`:''}</td><td>{p.complete_episodes}</td><td>{split==='unassigned'?'未参与':p.causal_windows}</td></tr>)}</tbody></table></div>
   {(['train','evaluation'] as const).map(kind=><div className="readiness-gate" key={kind}><strong>{kind==='train'?'训练准入':'独立评估准入'}：{report[kind].status==='metadata_ready'?'元数据通过':'条件未满足'}</strong>{report[kind].blockers.map(issue=><p key={issue.code+issue.message}>{issue.message} <small>{issue.code}</small></p>)}</div>)}
   {report.records.filter(r=>r.missing_records.length).map(r=><p className="ingest-note" key={r.import_id}>来源 {r.source_hash?.slice(0,12)??r.import_id.slice(0,12)} 待补：{r.missing_records.map(f=>fields[f]??f).join('、')}。</p>)}
   <button className="button secondary small" onClick={()=>downloadText(`${scene}.readiness.json`,JSON.stringify(report,null,2))}><Download size={14}/>导出预检报告</button>
  </div>}
 </section>;
}
