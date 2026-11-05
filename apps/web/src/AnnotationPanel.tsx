import {useEffect,useRef,useState} from 'react';
import {Download,Upload,Play,Pause,ChevronLeft,ChevronRight,Lock,EyeOff} from 'lucide-react';
import {sha256} from 'js-sha256';
import sourceData from './data/annotationDemoSource.json';
import {append,blankAnnotation,boundaryKinds,compile,digest,expose,hasExposure,recordExposure,fork,frameAt,intervalKinds,parseAnnotation,parseSource,raterAgreement,seal} from './annotation';
import type {AnnotationDoc,AnnotationEvent,SourcePacket} from './annotation';
import {downloadText} from './service';

const demo=parseSource(JSON.stringify(sourceData));
const fmt=(t:number)=>(t/1000).toFixed(3)+' s';
const storageKey=(s:SourcePacket)=>'jianwei-b11-annotation:'+s.descriptor_hash;

function EvaluationView({result,onSeek}:{result:any;onSeek:(pts:number)=>void}){
 const [method,setMethod]=useState('local_combined_original');const m=result.methods.find((v:any)=>v.method===method)??result.methods[0];
 const n=(v:number|null|undefined)=>v==null?'未验证':v.toFixed(3);
 return <div data-testid="fixed-evaluation"><p>原候选run {result.candidate_run_id} · 代码 {result.code_identity.git_head} · {result.status}。显示导入的CLI结果，未调阈值；工业结果仅属回顾性探索。</p>
  <div className="lm-scroll"><table className="lm-table"><thead><tr><th>方法</th><th>P / R / F1</th><th>FP / FN</th><th>误差MAE ms</th><th>误报/分钟</th></tr></thead><tbody>{result.methods.map((v:any)=><tr key={v.method}><td>{v.method}</td><td>{[v.precision,v.recall,v.f1].map(n).join(' / ')}</td><td>{v.false_positive??'—'} / {v.false_negative??'—'}</td><td>{n(v.boundary_error_mae_ms)}</td><td>{n(v.false_positives_per_minute)}</td></tr>)}</tbody></table></div>
  <label className="field"><span>逐边界复核方法</span><select aria-label="逐边界评价方法" value={m.method} onChange={e=>setMethod(e.target.value)}>{result.methods.map((v:any)=><option key={v.method} value={v.method}>{v.method}</option>)}</select></label>
  {result.status==='blocked'?<p className="lm-warning">评价受阻：{result.annotation.reason}；没有把未执行值显示成0。</p>:<>
   <p>原候选 {m.predictions_total}；匹配 {m.true_positive}、误检 {m.false_positive}、无法评价 {m.excluded_predictions}；漏检 {m.false_negative}；可能重复命中附近的额外点 {m.extra_predictions_near_already_matched_truth}。</p>
   <div className="lm-scroll"><table className="lm-table" data-testid="boundary-matches"><thead><tr><th>预测PTS</th><th>真人PTS</th><th>有符号误差ms</th></tr></thead><tbody>{m.matches.map((v:any,i:number)=><tr key={i}><td><button className="lm-link" onClick={()=>onSeek(v.prediction_ms)}>{v.prediction_ms} ms</button></td><td><button className="lm-link" onClick={()=>onSeek(v.truth_ms)}>{v.truth_ms} ms</button></td><td>{v.error_ms}</td></tr>)}</tbody></table></div>
   <p>误检点（可回查）：</p><div className="button-row">{m.unmatched_predictions_ms.length?m.unmatched_predictions_ms.map((t:number,i:number)=><button key={i} className="button secondary small" onClick={()=>onSeek(t)}>{t} ms</button>):<span>无</span>}</div>
   <p>漏检边界（包含未产生完整窗口的尾部）：</p><div className="button-row">{m.unmatched_truth_ms.length?m.unmatched_truth_ms.map((t:number,i:number)=><button key={i} className="button secondary small" onClick={()=>onSeek(t)}>{t} ms</button>):<span>无</span>}</div>
   <p className="muted">光照区间误检 {m.interference_context.lighting_unmatched_predictions}，无动作区间误检 {m.interference_context.no_action_unmatched_predictions}，背景无效区间误检 {m.interference_context.background_invalid_unmatched_predictions}；区间可能重叠，不能相加当总数。</p>
   <p>完整审核 {(result.annotation.coverage_ratio*100).toFixed(1)}%；无法判断/遮挡 {(result.annotation.uncertain_or_occluded_ratio*100).toFixed(1)}%；实际评价时长 {fmt(m.evaluated_duration_ms)}。所有方法相同范围和掩码。</p>
   <details><summary>固定时间容差敏感性，主规则仍±500ms</summary><div className="lm-scroll"><table className="lm-table"><thead><tr><th>容差ms</th><th>P / R / F1</th></tr></thead><tbody>{[m,...m.tolerance_sensitivity].sort((a,b)=>a.tolerance_ms-b.tolerance_ms).map((v:any)=><tr key={v.tolerance_ms}><td>±{v.tolerance_ms}</td><td>{[v.precision,v.recall,v.f1].map(n).join(' / ')}</td></tr>)}</tbody></table></div></details>
  </>}
  <p className="lm-warning">原特征支持到 {result.feature_support_end_ms} ms，完整原片到 {result.source_end_ms} ms，尾部没有自动排除。模型候选没有可观测的动作起止或工步语义，不能将时间匹配当语义分类正确。</p>
 </div>;
}

export default function AnnotationPanel(){
 const [source,setSource]=useState(demo),[doc,setDoc]=useState(()=>blankAnnotation(demo)),[hydrated,setHydrated]=useState(false);
 const [frame,setFrame]=useState(0),[media,setMedia]=useState(''),[mediaStatus,setMediaStatus]=useState('只选择本机原片，不上传。先核对SHA与显示尺寸。'),[observed,setObserved]=useState<number|null>(null);
 const [notice,setNotice]=useState(''),[boundaryType,setBoundaryType]=useState('onset'),[time,setTime]=useState('0'),[note,setNote]=useState('');
 const [intervalType,setIntervalType]=useState('reviewed'),[left,setLeft]=useState('0'),[right,setRight]=useState('1000');
 const [second,setSecond]=useState<AnnotationDoc|null>(null),[evaluation,setEvaluation]=useState<any>(null);
 const video=useRef<HTMLVideoElement>(null),nonce=useRef(0),receiptNonce=useRef(0),packetInput=useRef<HTMLInputElement>(null),annotationInput=useRef<HTMLInputElement>(null),secondInput=useRef<HTMLInputElement>(null),evaluationInput=useRef<HTMLInputElement>(null);
 const current=source.frames[frame]??source.frames[0],frozen=doc.status==='sealed',compiled=compile(doc,source);
 useEffect(()=>{
  nonce.current++;setHydrated(false);setMedia('');setMediaStatus('只选择本机原片，不上传。先核对SHA与显示尺寸。');setObserved(null);setFrame(0);setTime(String(source.source.first_pts_ms));setLeft(String(source.source.first_pts_ms));setRight(String(source.source.last_pts_end_ms));setSecond(null);setEvaluation(null);
  let next=blankAnnotation(source);
  try{const stored=localStorage.getItem(storageKey(source));if(stored)next=parseAnnotation(stored,source);if(hasExposure())next=expose(next,'此浏览器此前访问算法/候选页面或其他人员标注');}
  catch{next=expose(next,'本机存储或历史恢复不可用，不能确认盲标；请导出保全');setNotice('恢复失败或本机存储不可用。盲标声明已保守降级，请导出保存。');}
  setDoc(next);setHydrated(true);
 },[source.descriptor_hash]);
 useEffect(()=>{if(!hydrated||doc.descriptor_hash!==source.descriptor_hash)return;try{localStorage.setItem(storageKey(source),JSON.stringify(doc));if(doc.sealed_hash)localStorage.setItem('jianwei-b11-sealed:'+doc.sealed_hash,JSON.stringify(doc));}catch{setNotice('本机保存失败，请立即导出JSON；未保证刷新恢复。');}},[doc,hydrated,source.descriptor_hash]);
 useEffect(()=>()=>{if(media)URL.revokeObjectURL(media);},[media]);
 useEffect(()=>{const changed=()=>{if(hasExposure())setDoc(old=>expose(old,'另一标签页已显示算法或他人标注'));};window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);},[]);
 function update(next:AnnotationDoc){try{compile(next,source);setDoc(next);setEvaluation(null);return true;}catch(e){setNotice((e as Error).message);return false;}}
 function act(op:AnnotationEvent['op'],values:Partial<AnnotationEvent>={}){try{if(update(append(doc,op,values)))setNotice('已追加人工操作日志；没有复制任何算法候选。');}catch(e){setNotice((e as Error).message);}}
 function seek(index:number){const i=Math.max(0,Math.min(source.frames.length-1,index)),f=source.frames[i];setFrame(i);setTime(String(f.pts_ms));setObserved(null);
  if(video.current&&media){const v=video.current,token=++receiptNonce.current,fileToken=nonce.current;v.pause();if('requestVideoFrameCallback' in v)v.requestVideoFrameCallback((_now,m)=>{if(token===receiptNonce.current&&fileToken===nonce.current)setObserved(m.mediaTime*1000+source.source.first_pts_ms);});v.currentTime=((f.pts_ms+f.end_ms)/2-source.source.first_pts_ms)/1000;}
 }
 async function associate(file?:File){if(!file)return;const token=++nonce.current;setMedia('');setObserved(null);setMediaStatus('正在本机核验原片SHA…');try{
  if(file.size!==source.source.bytes)throw Error('原片字节数不符');const hash=sha256.create(),reader=file.stream().getReader();try{for(;;){const {done,value}=await reader.read();if(done)break;hash.update(value);}}finally{reader.releaseLock();}
  if(token!==nonce.current)return;if(hash.hex()!==source.source.sha256)throw Error('原片SHA不匹配，未加载');if(source.source.first_pts_ms!==0)throw Error('非零起始PTS需要单独核验播放器时钟映射；未自动猜测');setMedia(URL.createObjectURL(file));setMediaStatus('SHA已通过，正在核验显示尺寸与帧回执…');
 }catch(e){if(token===nonce.current)setMediaStatus((e as Error).message);}}
 async function importPacket(file?:File){if(!file)return;try{if(file.size>32*1024*1024)throw Error('源描述过大');const next=parseSource(await file.text());if(next.descriptor_hash!==source.descriptor_hash){setHydrated(false);setDoc(blankAnnotation(next));setSource(next);}setNotice('已载入源PTS描述，不含算法候选。');}catch(e){setNotice((e as Error).message);}}
 async function importDoc(file?:File,asSecond=false){if(!file)return;try{if(file.size>8*1024*1024)throw Error('标注文件过大');const next=parseAnnotation(await file.text(),source);
  if(asSecond){if(!frozen)throw Error('先封存第一位独立标注，再查看第二位');raterAgreement(doc,next,source);setSecond(next);recordExposure('viewed_second_rater');setNotice('第二位标注独立保留；分歧需要真人裁定，没有自动合并真值。');}
  else{const restored=hasExposure()?expose(next,'恢复时浏览器已看过算法'):next;setDoc(restored);setEvaluation(null);setSecond(null);setNotice('标注版本已恢复；封存哈希与源PTS一致。');}
 }catch(e){setNotice((e as Error).message);}}
 function exportDoc(){try{compile(doc,source);downloadText(`b11-${doc.annotation_id}-r${doc.revision}-${doc.status}.json`,JSON.stringify(doc,null,2));}catch(e){setNotice((e as Error).message);}}
 function freeze(){try{const next=seal(append(doc,'attest',{note:'标注人声明完整原范围审核；包含漏检、尾部及不确定区间'}),source);setDoc(next);setNotice('已封存当前版本。仍需CLI对原B.1固定候选评价，未自动生成真实F1。');}catch(e){setNotice((e as Error).message);}}
 function newRevision(){let next=fork(doc);if(hasExposure())next=expose(next,'封存后已查看算法或他人标注，后续版本为assisted');update(next);setSecond(null);setNotice('原封存版本保留在本机和导出文件；新草稿继续追加日志。');}
 async function importEvaluation(file?:File){if(!file)return;try{if(file.size>16*1024*1024)throw Error('评价文件过大');const r=JSON.parse(await file.text());if(!frozen||r.schema!=='b11-frozen-boundary-evaluation-v1'||r.source_sha256!==source.source.sha256||r.descriptor_hash!==source.descriptor_hash||r.annotation.annotation_hash!==digest(doc)||r.annotation.sealed_hash!==doc.sealed_hash||r.original_candidates_unchanged!==true)throw Error('评价报告与当前封存标注/源版本不匹配');setEvaluation(r);recordExposure('viewed_evaluation');setNotice('显示CLI实际评价文件，未重新计算或调整主阈值。');}catch(e){setNotice((e as Error).message);}}
 let agreement:ReturnType<typeof raterAgreement>|null=null;try{if(second)agreement=raterAgreement(doc,second,source);}catch{}
 return <div className="lm-panel annotation-panel">
  <div className="lm-notice"><EyeOff size={18}/><strong>{source.evidence_class==='synthetic_engineering'?'合成素材 · 标注工程演示':'真实开发视频 · 等待真人标注'}</strong><span>独立入口不显示候选、响应曲线或推荐边界；本轮B.1.1，P0.4保持blocked。</span></div>
  <section className="panel"><div className="panel-heading"><h2>源文件与独立标注</h2><span className="pill">{doc.mode==='blind'?'盲标声明（自报）':'辅助标注 · 已知提示偏差'}</span></div>
   <div className="lm-fields"><label className="field"><span>标注人员</span><input aria-label="独立标注人员" disabled={frozen} value={doc.annotator} onChange={e=>update({...doc,annotator:e.target.value})}/></label><button className="button secondary" onClick={()=>packetInput.current?.click()}><Upload size={14}/>载入源PTS描述</button><button className="button secondary" onClick={()=>annotationInput.current?.click()}>导入/恢复标注</button><button className="button secondary" onClick={exportDoc}><Download size={14}/>导出标注JSON</button></div>
   <input hidden ref={packetInput} type="file" accept=".json" aria-label="独立标注源PTS文件" onChange={e=>{void importPacket(e.target.files?.[0]);e.target.value='';}}/><input hidden ref={annotationInput} type="file" accept=".json" aria-label="独立标注恢复文件" onChange={e=>{void importDoc(e.target.files?.[0]);e.target.value='';}}/>
   <p className="muted">完整范围 {fmt(source.source.first_pts_ms)}—{fmt(source.source.last_pts_end_ms)}，{source.frames.length} 个原始PTS帧。历史是否看过算法由人员自报及本机曝光记录辅助，不能提供身份认证或密码学盲法证明。</p>
   <button className="button secondary" disabled={frozen||doc.mode==='assisted'} onClick={()=>update(expose(doc,'标注人主动声明此前已看过算法或相关提示'))}>已看过算法，标记辅助标注</button>
   {notice&&<p className="lm-message" role="status">{notice}</p>}
  </section>
  <div className="lm-top-grid">
   <section className="panel"><div className="panel-heading"><h2>完整原片 · 原始帧定位</h2><span className="pill">{source.source.width} × {source.source.height}</span></div>
    <div className="lm-video-wrap" style={{aspectRatio:`${source.source.width}/${source.source.height}`}}>{media?<video ref={video} controls preload="auto" src={media} aria-label="独立标注原片" onLoadedData={()=>{if(video.current?.videoWidth!==source.source.width||video.current?.videoHeight!==source.source.height){setMedia('');setMediaStatus('显示尺寸不符，未验证逐帧');}else{setMediaStatus('原片SHA与显示尺寸已核验');seek(frame);}}} onPlay={()=>setObserved(null)} onPause={()=>{if(video.current&&media)seek(frameAt(source,video.current.currentTime*1000+source.source.first_pts_ms));}} onTimeUpdate={()=>{if(video.current&&!video.current.paused){setObserved(null);const i=frameAt(source,video.current.currentTime*1000+source.source.first_pts_ms);setFrame(i);setTime(String(source.frames[i].pts_ms));}}} onError={()=>setMediaStatus('解码失败，未验证播放/逐帧')}/>:<div className="lm-video-empty"><p>选择本机原片进行完整盲标</p><small>不上传，不自动填充动作边界</small></div>}</div>
    <label className="lm-file">原片文件<input type="file" accept="video/mp4" aria-label="独立标注原视频" onChange={e=>{void associate(e.target.files?.[0]);e.target.value='';}}/></label><p className="muted" role="status">{mediaStatus}</p>
    <div className="button-row"><button className="button secondary" disabled={!media} aria-label="上一原始帧" onClick={()=>seek(frame-1)}><ChevronLeft size={15}/>上一帧</button><button className="button secondary" disabled={!media} onClick={()=>void video.current?.play().catch(()=>setNotice('请用原生播放控件'))}><Play size={15}/>播放</button><button className="button secondary" disabled={!media} onClick={()=>video.current?.pause()}><Pause size={15}/>暂停</button><button className="button secondary" disabled={!media} aria-label="下一原始帧" onClick={()=>seek(frame+1)}>下一帧<ChevronRight size={15}/></button></div>
    <label className="field"><span>原始帧 {frame+1}/{source.frames.length} · PTS {current.pts_ms} ms</span><input aria-label="独立标注原始帧时间轴" type="range" min="0" max={source.frames.length-1} value={frame} onChange={e=>seek(Number(e.target.value))}/></label>
    <p className="muted" data-testid="annotation-frame-receipt">呈现帧PTS回执：{observed===null?'未验证':`${observed.toFixed(3)} ms；相对目标误差 ${Math.abs(observed-current.pts_ms).toFixed(3)} ms`}。使用原始PTS区间中点寻帧，不按平均fps猜测。</p>
   </section>
   <section className="panel"><div className="panel-heading"><h2>人工边界与完整覆盖</h2><span className="pill">{(compiled.coverage*100).toFixed(1)}% 已声明审核</span></div>
    <div className="annotation-coverage" aria-label="人工审核覆盖可视化">{compiled.reviewed.map(([a,b],i)=><span key={i} style={{left:`${(a-doc.start_ms)/(doc.end_ms-doc.start_ms)*100}%`,width:`${(b-a)/(doc.end_ms-doc.start_ms)*100}%`}}/>)}{compiled.excluded.map(([a,b],i)=><span className="uncertain" key={'unknown'+i} title="人工标记无法判断或遮挡" style={{left:`${(a-doc.start_ms)/(doc.end_ms-doc.start_ms)*100}%`,width:`${(b-a)/(doc.end_ms-doc.start_ms)*100}%`}}/>)}</div>
    <p className="muted">绿色仅表示人工声明审核，灰色为未审核，橙纹为无法判断/遮挡；不以播放时长自动充当审核证据。未审核：{compiled.missing.length?compiled.missing.map(([a,b])=>`${fmt(a)}–${fmt(b)}`).join('；'):'无'}。</p>
    <div className="lm-fields"><label className="field"><span>人工边界PTS（ms）</span><input aria-label="独立人工边界PTS" disabled={frozen} type="number" value={time} onChange={e=>setTime(e.target.value)}/></label><label className="field"><span>人工类型</span><select aria-label="独立人工边界类型" disabled={frozen} value={boundaryType} onChange={e=>setBoundaryType(e.target.value)}>{Object.entries(boundaryKinds).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div>
    <label className="field"><span>操作备注/分歧说明</span><input aria-label="人工操作备注" disabled={frozen} value={note} onChange={e=>setNote(e.target.value)}/></label><button className="button secondary" disabled={frozen} onClick={()=>act('boundary_add',{pts_ms:Number(time),kind:boundaryType,note})}>添加独立人工边界</button>
    <div className="lm-fields"><label className="field"><span>区间起点ms</span><input aria-label="审核区间起点" disabled={frozen} type="number" value={left} onChange={e=>setLeft(e.target.value)}/></label><label className="field"><span>终点ms</span><input aria-label="审核区间终点" disabled={frozen} type="number" value={right} onChange={e=>setRight(e.target.value)}/></label><label className="field"><span>区间类型</span><select aria-label="审核区间类型" disabled={frozen} value={intervalType} onChange={e=>setIntervalType(e.target.value)}>{Object.entries(intervalKinds).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label></div>
    <button className="button secondary" disabled={frozen} onClick={()=>act('interval_add',{pts_ms:Number(left),end_ms:Number(right),kind:intervalType,note})}>记录人工区间</button>
    <p className="lm-warning">无法判断/遮挡区间进入所有方法相同的排除掩码；光照、无动作和背景无效只作分层检查，不能选择性删掉误检。视频尾部仍需完整审核。</p>
   </section>
  </div>
  <section className="panel"><div className="panel-heading"><h2>人工事件与区间日志</h2><span className="pill">r{doc.revision} · {compiled.boundaries.length} 个当前边界</span></div>
   <div className="lm-scroll"><table className="lm-table" data-testid="independent-boundaries"><thead><tr><th>PTS / 类型</th><th>备注 / 复核</th><th>人工操作</th></tr></thead><tbody>{compiled.boundaries.map(b=><tr key={b.id}><td><button className="lm-link" onClick={()=>seek(frameAt(source,b.pts_ms))}>{b.pts_ms} ms</button> · {boundaryKinds[b.kind]}</td><td>{b.note||'—'} · {b.reviewed_by.length} 次复核</td><td><div className="button-row"><button disabled={frozen} className="button secondary small" onClick={()=>act('boundary_update',{target_id:b.id,pts_ms:Number(time),kind:boundaryType,note})}>按输入修订</button><button disabled={frozen} className="button secondary small" onClick={()=>act('boundary_review',{target_id:b.id,note})}>记录复核</button><button disabled={frozen} className="button secondary small" onClick={()=>act('boundary_delete',{target_id:b.id,note})}>删除人工边界</button></div></td></tr>)}</tbody></table></div>
   <div className="lm-scroll"><table className="lm-table" data-testid="independent-intervals"><thead><tr><th>人工区间</th><th>类别/备注</th><th>操作</th></tr></thead><tbody>{compiled.intervals.map(i=><tr key={i.id}><td>{fmt(i.start_ms)}—{fmt(i.end_ms)}</td><td>{intervalKinds[i.kind]} · {i.note||'—'}</td><td><div className="button-row"><button className="button secondary small" disabled={frozen} onClick={()=>act('interval_update',{target_id:i.id,pts_ms:Number(left),end_ms:Number(right),kind:intervalType,note})}>按输入修订区间</button><button className="button secondary small" disabled={frozen} onClick={()=>act('interval_delete',{target_id:i.id,note})}>撤回区间</button></div></td></tr>)}</tbody></table></div>
   <details><summary>查看不可覆盖的操作历史（{doc.events.length}条）</summary><ol className="annotation-log">{doc.events.map(e=><li key={e.id}>{e.at} · {e.actor} · {e.op} · {e.note||'—'}</li>)}</ol></details>
  </section>
  <section className="panel"><div className="panel-heading"><h2>封存标注版本与独立复核</h2><Lock size={17}/></div>
   <div className="annotation-attest"><label><input type="checkbox" aria-label="声明完整人工审核" disabled={frozen} checked={doc.coverage_complete} onChange={e=>update({...doc,coverage_complete:e.target.checked})}/>已审核完整原范围，包含未推荐边界、漏检及尾部</label><label><input type="checkbox" aria-label="人工标注真实性声明" disabled={frozen} checked={doc.human_attestation} onChange={e=>update({...doc,human_attestation:e.target.checked})}/>{source.evidence_class==='synthetic_engineering'?'本次仅为合成工程操作验证，不是工业真人真值':'确认事件来自真人审核；算法未自动填充真值'}</label></div>
   <div className="button-row"><button className="button primary" disabled={frozen} onClick={freeze}>封存当前标注版本</button><button className="button secondary" disabled={!frozen} onClick={newRevision}>新建修订草稿</button><button className="button secondary" disabled={!frozen} onClick={()=>secondInput.current?.click()}>载入第二位独立标注</button><button className="button secondary" disabled={!frozen} onClick={()=>evaluationInput.current?.click()}>载入固定预测评价</button></div>
   <input hidden ref={secondInput} type="file" accept=".json" aria-label="第二位独立标注文件" onChange={e=>{void importDoc(e.target.files?.[0],true);e.target.value='';}}/><input hidden ref={evaluationInput} type="file" accept=".json" aria-label="固定预测评价文件" onChange={e=>{void importEvaluation(e.target.files?.[0]);e.target.value='';}}/>
   <p className="lm-warning">{frozen?'标注版本已锁定；评价仍需对原B.1报告运行CLI，不会在页面中猜测指标。':'真实Precision / Recall / F1保持blocked，须完整覆盖并封存真人标注。'} 第二位审核未提供时保持受阻；两人一致性也不能自动成为共识真值。</p>
   {agreement&&<div><p>两人边界匹配 {agreement.matches.length}，第一位独有 {agreement.missingSecond}，第二位独有 {agreement.missingFirst}，类型分歧 {agreement.typeDisagreements.length}；固定±500ms，相同排除掩码。</p><ul>{agreement.typeDisagreements.map((m,i)=><li key={i}>{m.first.pts_ms} / {m.second.pts_ms} ms：{boundaryKinds[m.first.kind]} / {boundaryKinds[m.second.kind]}，待真人裁定。</li>)}</ul></div>}
   {evaluation&&<EvaluationView result={evaluation} onSeek={pts=>seek(frameAt(source,pts))}/>}
   <dl className="lm-version"><dt>源SHA</dt><dd><code>{source.source.sha256}</code></dd><dt>PTS版本</dt><dd><code>{source.descriptor_hash}</code></dd><dt>标注版本</dt><dd>{doc.annotation_id} · r{doc.revision} · {doc.status}</dd><dt>封存摘要</dt><dd><code>{doc.sealed_hash??'尚未封存'}</code></dd></dl>
  </section>
 </div>;
}
