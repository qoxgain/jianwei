import {useEffect,useRef,useState} from 'react';
import {Activity,Download,FlaskConical,RefreshCw,TriangleAlert} from 'lucide-react';
import {isDemo} from './sceneService';
import {downloadText} from './service';
import {partitionSelection} from './temporalTypes';
import type {TemporalInput,TemporalReport,TemporalRun} from './temporalTypes';
import {createTemporalExample,evaluateTemporal,retryTemporal,temporalArtifact,temporalInputs,temporalRun,temporalRuns,trainTemporal} from './temporalService';
import ReadinessPanel from './ReadinessPanel';
const names:Record<string,string>={persistence:'持久性',empirical:'经验转移',ridge:'Ridge',causal_tcn:'因果 TCN',shuffled_ridge:'打乱目标对照'};
const statuses={queued:'排队中',running:'研究中',succeeded:'完成 · 未激活',blocked:'条件未满足',failed:'执行失败'};
const average=(values:number[])=>values.reduce((a,b)=>a+b,0)/values.length;
const number=(value:number|null|undefined)=>value===null||value===undefined?'—':value.toFixed(4);
function Result({report}:{report:TemporalReport}){
 const metrics=report.test??report.calibration,selected=report.model?.selected_model??report.selected_model;
 return <div className="temporal-result">
  <p className="ingest-note">{report.data_origin==='synthetic_features'?'合成机制实验，不能外推现场效果。':'基于导入特征的研究；来源和外部向量生产链见完整清单。'} 所有模型保持候选，没有自动上线。</p>
  <div className="ingest-metrics"><div><strong>{metrics?.independent_sources??0}</strong><span>{report.test?'封存测试':'校准'}独立源</span></div><div><strong>{metrics?.windows??0}</strong><span>相关时序窗口</span></div><div><strong>{selected?names[selected]: '—'}</strong><span>校准集选择</span></div><div><strong>未验证</strong><span>业务动作准确率</span></div></div>
  {metrics&&<div className="ingest-table-wrap"><table><caption>{report.test?'封存测试结果':'校准比较'} · 特征误差越小越好</caption><thead><tr><th>模型</th><th>按源平均 MSE</th><th>原空间余弦</th><th>匿名状态 NLL</th></tr></thead><tbody>{Object.entries(metrics.metrics).map(([name,m])=><tr key={name}><td>{names[name]??name}{name===selected?' · 已选择':''}</td><td>{number(metrics.source_macro_mse[name])}</td><td>{number(average(m.original_feature_cosine))}</td><td>{number(average(m.anonymous_state_nll))}</td></tr>)}</tbody></table></div>}
  <p className="ingest-note">窗口来自同一视频时并不独立。选模只用校准数据；test结果不会改变候选选择。匿名状态指标不是事件识别率。</p>
  {report.training&&<p>TCN 训练损失：{number(report.training.initial_loss)} → {number(report.training.epoch_losses.at(-1))}；参数实际变化 {number(report.training.parameter_delta_l2)}。</p>}
  {report.projection&&<p>PCA {report.projection.dimension} 维，训练集保留方差 {(report.projection.explained_variance_ratio*100).toFixed(1)}%。视觉编码器保持冻结。</p>}
  <div className="temporal-facts"><p>变化点候选分段 <strong>{report.anonymous_discovery.segments.length}</strong>；均匀分段对照 <strong>{report.anonymous_discovery.uniform_segments.length}</strong>。没有边界真值，不计算分段准确率。</p><p>下一不同匿名簇：{report.next_anonymous_state.observed_transitions} 个可观察转移，{report.next_anonymous_state.censored_windows} 个窗口未观察到后续变化；NLL {number(report.next_anonymous_state.nll)}。</p></div>
  <dl className="ingest-facts"><div><dt>数据快照</dt><dd><code className="ingest-hash">{report.snapshot.hash}</code></dd></div>{report.model&&<><div><dt>实际权重 SHA256</dt><dd><code className="ingest-hash">{report.model.weights_sha256}</code></dd></div><div><dt>匿名簇版本</dt><dd><code className="ingest-hash">{report.model.cluster_version}</code></dd></div></>}</dl>
 </div>;
}
export default function TemporalPanel({scene}:{scene:string}){
 const [inputs,setInputs]=useState<TemporalInput[]>([]),[runs,setRuns]=useState<TemporalRun[]>([]),[selected,setSelected]=useState(''),[detail,setDetail]=useState<TemporalRun|null>(null);
 const [ids,setIds]=useState<string[]>([]),[testIds,setTestIds]=useState<string[]>([]),[model,setModel]=useState(''),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const active=useRef(true),refreshing=useRef(false);
 async function refresh(){if(refreshing.current)return;refreshing.current=true;try{const [a,b]=await Promise.all([temporalInputs(scene),temporalRuns(scene)]);if(active.current){setInputs(a);setRuns(b);setSelected(old=>old||b[0]?.id||'');setModel(old=>old||b.find(r=>r.kind==='train'&&r.status==='succeeded')?.id||'');}}catch(e){if(active.current)setError((e as Error).message);}finally{refreshing.current=false;}}
 useEffect(()=>{active.current=true;void refresh();const timer=setInterval(()=>void refresh(),2000);return()=>{active.current=false;clearInterval(timer);};},[scene]);
 const status=runs.find(r=>r.id===selected)?.status;
 useEffect(()=>{let alive=true;setDetail(null);if(selected)void temporalRun(scene,selected).then(r=>{if(alive)setDetail(r);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[scene,selected,status]);
 async function perform(label:string,operation:()=>Promise<void>){setBusy(label);setError('');try{await operation();await refresh();}catch(e){if(active.current)setError((e as Error).message);}finally{if(active.current)setBusy('');}}
 function toggle(id:string,test:boolean){const change=(items:string[])=>items.includes(id)?items.filter(v=>v!==id):[...items,id];if(test)setTestIds(change);else setIds(change);}
 async function example(){await perform('准备独立来源',async()=>{const result=await createTemporalExample(scene);if(active.current){setIds(result.train_import_ids);setTestIds(result.test_import_ids);setNotice(result.notice);}});}
 async function train(){await perform('提交训练',async()=>{const chosen=partitionSelection(inputs,ids,'train');const result=await trainTemporal(scene,chosen);if(active.current){setSelected(result.id);setModel(result.id);}});}
 async function evaluate(){await perform('提交独立评估',async()=>{if(!model)throw new Error('请先选择成功生成的候选模型');const chosen=partitionSelection(inputs,testIds,'evaluate');const result=await evaluateTemporal(scene,model,chosen);if(active.current)setSelected(result.id);});}
 const candidateReady=runs.some(r=>r.id===model&&r.kind==='train'&&r.status==='succeeded');
 return <div className="temporal-workspace">
  <section className="panel ingest-start"><div className="ingest-start-icon"><FlaskConical size={27}/></div><div><h2>先验证时序是否可学习</h2><p>{isDemo?'查看随附的实际合成研究记录。浏览器不执行训练或测试。':'从已接入的独立视频特征开始；无需动作标签或 SOP。'}</p><small>冻结编码器 · 训练与封存测试分开 · 候选不自动激活</small></div>{!isDemo&&<button className="button primary" onClick={example} disabled={!!busy}>准备合成研究示例</button>}</section>
  {error&&<div className="ingest-alert" role="alert"><TriangleAlert size={18}/>{error}</div>}{notice&&<p className="ingest-feedback" role="status">{notice}</p>}
  <div className="temporal-grid">
   <section className="panel"><div className="panel-heading"><h2>{isDemo?'研究记录':'选择研究数据'}</h2><button className="icon-button" aria-label="刷新研究任务" onClick={()=>void refresh()}><RefreshCw size={17}/></button></div><div className="jw-fields">
    {!isDemo&&<><p className="ingest-note">上传真实数据时，在“数据接入”选择整个原始视频的分区。未分配或未核验来源不能参加训练。</p><div className="temporal-inputs">{inputs.map(row=><label key={row.id} className="temporal-input"><input type="checkbox" disabled={!!busy} checked={(row.split==='test'?testIds:ids).includes(row.id)} onChange={()=>toggle(row.id,row.split==='test')}/><span><strong>{row.split} · {row.eligible_rows} 行</strong><small>{row.data_origin==='synthetic_features'?'合成源':'上传来源'} · {row.source_hash.slice(0,12)}</small></span></label>)}</div>{!inputs.length&&<p>尚无成功导入；可以先准备合成示例。</p>}
    <ReadinessPanel scene={scene} ids={[...ids,...testIds]} model={model}/>
    <button className="button primary" disabled={!!busy||!ids.length} onClick={train}>用训练 / 校准分区建立候选</button>
    <label className="field"><span>用于封存测试的候选</span><select value={model} onChange={e=>setModel(e.target.value)}><option value="">选择已完成训练</option>{runs.filter(r=>r.kind==='train'&&r.status==='succeeded').map(r=><option key={r.id} value={r.id}>{r.created_at?new Date(r.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):r.id}</option>)}</select></label>
    <button className="button secondary" disabled={!!busy||!candidateReady||!testIds.length} onClick={evaluate}>读取封存 test，运行独立评估</button><p className="ingest-note">此操作会读取选中的封存测试数据。查看结果后继续修改模型，应另设新的封存测试集，避免反复针对同一测试集调参。</p></>}
    {isDemo&&<ReadinessPanel scene={scene} ids={[]} model=""/>}
    {!!busy&&<p role="status">{busy}…</p>}
    <div className="ingest-job-list">{runs.map(r=><button key={r.id} className={`ingest-job ${selected===r.id?'selected':''}`} onClick={()=>setSelected(r.id)}><strong>{r.kind==='train'?'时序训练与校准':'独立封存评估'}</strong><span>{r.demo_reference?'随附已执行记录':statuses[r.status]}</span><small>{r.id.slice(0,16)}</small></button>)}</div>
    {!runs.length&&<div className="jw-empty"><Activity/><p>尚无研究结果。没有结果时不显示识别率或学习提升。</p></div>}
   </div></section>
   <section className="panel"><div className="panel-heading"><h2>{detail?.kind==='evaluate'?'独立测试报告':'候选研究报告'}</h2>{detail?.report&&<button className="button secondary small" onClick={()=>downloadText(`${detail.id}.research.json`,JSON.stringify(detail.report,null,2))}><Download size={14}/>导出报告</button>}</div><div className="jw-fields">
    {detail?.error&&<div className="ingest-alert"><div><strong>{detail.error.message}</strong><p>{detail.error.code}</p>{!isDemo&&<button className="button secondary" onClick={()=>void perform('重试研究',async()=>{await retryTemporal(scene,detail.id);})}>重试</button>}</div></div>}
    {detail?.status==='running'||detail?.status==='queued'?<p role="status">服务器正在处理，刷新页面可恢复任务记录。</p>:null}
    {detail?.report?<><Result report={detail.report}/>{!isDemo&&<div className="button-row"><a className="button secondary" href={temporalArtifact(scene,detail.id,'report.json')}>完整报告</a>{detail.kind==='train'&&<a className="button secondary" href={temporalArtifact(scene,detail.id,'weights.npz')}>下载候选权重</a>}<a className="button secondary" href={temporalArtifact(scene,detail.id,`${detail.kind==='train'?'calibration':'test'}-predictions.jsonl`)}>预测与观测记录</a></div>}</>:!detail?.error&&<div className="jw-empty"><FlaskConical size={32}/><h2>从证据开始研究</h2><p>结果将保留独立源数、实际权重、各基线表现与失败原因。</p></div>}
   </div></section>
  </div>
 </div>;
}
