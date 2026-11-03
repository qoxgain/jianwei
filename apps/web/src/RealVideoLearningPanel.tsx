import {useEffect,useRef,useState} from 'react';
import {sha256} from 'js-sha256';
import recorded from './data/realVideoLearning.json?raw';
import RobustnessPanel from './RobustnessPanel';
import {downloadText} from './service';

type Metric={feature_mse:number[];normalized_feature_mse:number[];feature_cosine:number[]};
type Trace={issued_ms:number;target_ms:number[][];mse:Record<string,number[]>;target_l2:number[];prediction_l2:Record<string,number[]>};
type Mode={status:string;reason?:string;counts:Record<string,number>;intervals_ms:Record<string,number[]>;weights_sha256?:string;weight_reload_exact?:boolean;selected_by_validation?:string;training?:{initial_loss:number;epoch_losses:number[];parameter_delta_l2:number};metrics?:Record<string,Record<string,Metric>>;prediction_traces?:Record<string,Trace[]>;timeline?:{start_ms:number;anonymous_cluster:number}[];shared_rows:Record<string,number>};
type Asset={asset_id:string;source_sha256:string;domain:string;rows:number;duration_ms:number;modes:Record<string,Mode>;progress?:unknown;feature_space_id:string;learning:{forecast_horizons_seconds:number[]};C:{status:string;reason:string};error?:string};
type Report={assets:Asset[];pending?:string[];formal_status:string;scope:string};
const data=JSON.parse(recorded) as Report;
const names:Record<string,string>={persistence:'Persistence',train_mean:'训练均值',ridge:'Ridge',causal_tcn:'Causal TCN',shuffled_ridge:'Shuffled Ridge',shuffled_tcn:'Shuffled TCN',initial_tcn:'未训练 TCN'};
const colors=['#22c3b6','#efab59','#919cf8','#ed758b'];
const mean=(x:number[])=>x.reduce((a,b)=>a+b,0)/x.length;
export function Curve({series,label}:{series:{name:string;values:number[]}[];label:string}){
 const all=series.flatMap(s=>s.values),max=Math.max(...all,1e-9),min=Math.min(...all,0),range=max-min||1;
 return <figure className="real-curve"><figcaption>{label}</figcaption><svg viewBox="0 0 640 180" role="img" aria-label={label}>
  <text x="4" y="14" fill="currentColor" fontSize="11">{max.toPrecision(3)}</text><text x="4" y="169" fill="currentColor" fontSize="11">{min.toPrecision(3)}</text>
  <path d="M50 15V160H630" fill="none" stroke="currentColor" opacity=".4"/>
  {series.map((s,j)=><polyline key={s.name} points={s.values.map((v,i)=>`${50+i*580/Math.max(s.values.length-1,1)},${160-(v-min)/range*140}`).join(' ')} fill="none" stroke={colors[j%colors.length]} strokeWidth="2"/>)}
 </svg><div>{series.map((s,i)=><span key={s.name} style={{color:colors[i%colors.length],marginRight:16}}>{s.name}</span>)}</div></figure>;
}

function HistoricalLearningPanel(){
 const [id,setId]=useState(data.assets[0]?.asset_id??''),[mode,setMode]=useState('B'),[horizon,setHorizon]=useState(0),[split,setSplit]=useState('holdout');
 const [video,setVideo]=useState(''),[message,setMessage]=useState('');const generation=useRef(0),player=useRef<HTMLVideoElement>(null);
 const asset=data.assets.find(a=>a.asset_id===id),result=asset?.modes?.[mode];
 useEffect(()=>{generation.current++;setVideo('');setMessage('');},[id]);
 useEffect(()=>()=>{if(video)URL.revokeObjectURL(video);},[video]);
 async function localFile(file:File){const token=++generation.current;setMessage('校验原片 SHA256…');const hash=sha256.create();for(let i=0;i<file.size;i+=2097152){hash.update(await file.slice(i,i+2097152).arrayBuffer());if(token!==generation.current)return;}
  if(hash.hex()!==asset?.source_sha256){setMessage('SHA256 不匹配，未加载此文件。');return;}setVideo(URL.createObjectURL(file));setMessage('原片 SHA256 已匹配，仅在本机回查，未上传。');}
 const metrics=result?.metrics?.[split],trace=result?.prediction_traces?.[split]??[];
 return <div className="real-learning"><section className="panel jw-fields">
  <h2>真实视频学习实验</h2><p className="ingest-alert">回顾性探索 · 正式科研验收仍 blocked · 已核验独立采集会话 0</p>
  <p>这里保留 2026-11-02 探索分支实际解码、冻结编码、训练和回放的历史记录。切换页面不会启动训练，不读取正式封存数据，也不激活模型。</p>
  <div className="jw-fields"><label>真实来源<select aria-label="真实来源" value={id} onChange={e=>setId(e.target.value)}>{data.assets.map(a=><option key={a.asset_id}>{a.asset_id}</option>)}</select></label>
  <label>实验方案<select aria-label="实验方案" value={mode} onChange={e=>setMode(e.target.value)}><option value="A">A · 同视频拟合（无泛化证据）</option><option value="B">B · 同视频时间隔离留出</option></select></label></div>
  {asset&&<><p>{asset.domain} · {(asset.duration_ms/1000).toFixed(2)} 秒 · {asset.rows} 个真实 512 维片段 · 来源采集身份未核验</p><code className="ingest-hash">{asset.source_sha256}</code><details><summary>实际提取进度与版本记录</summary><pre>{JSON.stringify({progress:asset.progress,feature_space_id:asset.feature_space_id},null,2)}</pre></details><p>跨视频 C：{asset.C?.status}。来源独立性及视角可比性未核实，不提供同分布跨视频结论。</p></>}
  {result&&<><p>执行状态：{result.status} {result.reason??''}</p><div className="ingest-table-wrap"><table><caption>原始时间先划分，再构建窗口；B 保留 2 秒保护间隔</caption><thead><tr><th>区间</th><th>时间（秒）</th><th>因果窗口</th></tr></thead><tbody>{Object.entries(result.intervals_ms).map(([key,v])=><tr key={key}><th>{key}</th><td>{v.map(t=>(t/1000).toFixed(2)).join(' – ')}</td><td>{result.counts[key]}</td></tr>)}</tbody></table></div><p>B 各区间共享片段数：{JSON.stringify(result.shared_rows)}。A 刻意复用同一数据，只证明拟合。</p></>}
  {result?.status==='executed'&&<>
   <p>TCN 参数更新 L2：{result.training?.parameter_delta_l2.toFixed(5)}；权重精确重载：{String(result.weight_reload_exact)}；验证区间选定：{result.selected_by_validation}。</p><code className="ingest-hash">{result.weights_sha256}</code>
   <Curve label="TCN 实际训练损失 · 横轴为 epoch" series={[{name:'训练损失',values:[result.training!.initial_loss,...result.training!.epoch_losses]}]}/>
   <div className="button-row"><label>评价区间<select aria-label="评价区间" value={split} onChange={e=>setSplit(e.target.value)}>{['train','validation','holdout'].map(k=><option key={k}>{k}</option>)}</select></label><label>预测跨度<select aria-label="预测跨度" value={horizon} onChange={e=>setHorizon(Number(e.target.value))}>{(asset?.learning.forecast_horizons_seconds??[]).map((h,i)=><option key={h} value={i}>{h} 秒</option>)}</select></label></div>
   <div className="ingest-table-wrap"><table><caption>实际特征预测；改善为正表示误差下降，不是动作准确率</caption><thead><tr><th>模型</th><th>特征 MSE</th><th>余弦</th><th>对 Persistence</th><th>对 Ridge</th></tr></thead><tbody>{Object.entries(metrics??{}).map(([name,m])=><tr key={name}><th>{names[name]??name}</th><td>{m.feature_mse[horizon].toFixed(6)}</td><td>{m.feature_cosine[horizon].toFixed(4)}</td>{['persistence','ridge'].map(base=>{const b=metrics![base].feature_mse[horizon];return <td key={base}>{b>1e-12?`${((b-m.feature_mse[horizon])/b*100).toFixed(2)}%`:'不可定义'}</td>;})}</tr>)}</tbody></table></div>
   <Curve label="未来观测与预测的逐窗口 MSE · 横轴为原时间顺序" series={['persistence','ridge','causal_tcn','shuffled_tcn'].map(n=>({name:n,values:trace.map(t=>t.mse[n][horizon])}))}/>
   <Curve label="特征向量 L2 范数 · 范数接近不等于向量预测正确" series={[{name:'实际未来观测',values:trace.map(t=>t.target_l2[horizon])},{name:'TCN 预测',values:trace.map(t=>t.prediction_l2.causal_tcn[horizon])}]}/>
   <Curve label="训练数据拟合的匿名簇回放 · 不是业务状态真值" series={[{name:'匿名簇编号',values:(result.timeline??[]).map(t=>t.anonymous_cluster)}]}/>
   <p>初始 TCN、训练均值和打乱目标对照用于判断改善是否来自时序学习。重叠窗口不作独立样本，不计算显著性区间。跨工位差异不等于泛化能力。</p>
  </>}
  <label>选择本机原视频（不上传）<input aria-label="本机原视频" type="file" accept="video/*" onChange={e=>{const f=e.target.files?.[0];if(f)void localFile(f);}}/></label><p role="status">{message}</p>
  {video&&<><video ref={player} src={video} controls playsInline onError={()=>setMessage('当前浏览器无法播放此原片编码，请使用本机兼容播放器。')}/><div className="button-row">{trace.filter((_,i)=>i===0||i===Math.floor(trace.length/2)||i===trace.length-1).map(t=><button className="button secondary" key={t.issued_ms} onClick={()=>{if(player.current)player.current.currentTime=t.issued_ms/1000;}}>回查 {(t.issued_ms/1000).toFixed(2)} 秒</button>)}</div></>}
  <button className="button secondary" onClick={()=>downloadText('exploratory-real-video.json',JSON.stringify(data,null,2))}>导出实际实验报告</button>
  <p>均值比较仅供回顾性探索，不计算动作识别准确率，不批准 SOP，不自动准入 P0.4。数值特征和权重仅保留本地，不随公开 Demo 上传。</p>
 </section></div>;
}

export default function RealVideoLearningPanel(){
 const [view,setView]=useState('current');
 return <div className="real-learning"><div className="button-row"><button className={`button ${view==='current'?'primary':'secondary'}`} onClick={()=>setView('current')}>P0.3.6 稳健性研究</button><button className={`button ${view==='history'?'primary':'secondary'}`} onClick={()=>setView('history')}>P0.3.5 历史探索</button></div>{view==='current'?<RobustnessPanel/>:<HistoricalLearningPanel/>}</div>;
}
