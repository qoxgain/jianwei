import {useEffect,useMemo,useRef,useState} from 'react';
import {Download,Upload,Play,ScanLine,CheckCircle2,TriangleAlert} from 'lucide-react';
import {stringify} from 'yaml';
import {sha256} from 'js-sha256';
import demoData from './data/localMotionDemo.json';
import {blankReview,candidateEvaluationNote,formatPts,latestDecisions,parseMotionReport,parseReview,roiDraft,validBox} from './localMotion';
import type {ManualDocument,ManualEvent,MotionReport,MotionSeries,MotionCandidate,Roi} from './localMotion';
import {downloadText} from './service';

const demo=parseMotionReport(JSON.stringify(demoData));
const roleNames:Record<string,string>={global:'全局画面',hands:'手部操作区域',interaction:'工件交互区域',background:'背景负对照',offset_control:'ROI偏移对照'};
const methods:Record<string,string>={r3d:'冻结R3D变化',frame_difference:'连续采样帧差',block_motion:'轻量块运动'};
const colors=['#167d8d','#e18b35','#816bc5','#448451','#c55769','#7a817e'];
const number=(v:number|null|undefined)=>v==null?'—':v.toFixed(4);

function Curves({series,start,end,cursor,onSeek,marks}:{series:MotionSeries[];start:number;end:number;cursor:number;onSeek:(pts:number)=>void;marks:MotionCandidate[]}){
 const values=series.flatMap(s=>s.points.filter(p=>p.normalized!==null).map(p=>p.normalized!));
 const low=Math.min(0,values.reduce((a,b)=>Math.min(a,b),0)),high=Math.max(1,values.reduce((a,b)=>Math.max(a,b),1));
 const x=(v:number)=>42+(v-start)/(end-start)*716,y=(v:number)=>190-(v-low)/(high-low)*164;
 return <svg className="lm-chart" viewBox="0 0 800 232" role="img" aria-label="局部动态响应曲线，点击可定位PTS" onClick={e=>{const rect=e.currentTarget.getBoundingClientRect();const px=(e.clientX-rect.left)/rect.width*800;onSeek(Math.round(Math.max(start,Math.min(end,start+(px-42)/716*(end-start)))));}}>
  <rect x="42" y="26" width="716" height="164" fill="#f4f8f8"/><path d="M42 26V190H758" fill="none" stroke="#c4d4d4"/>
  {[low,0,high].map((v,i)=><g key={i}><path d={`M42 ${y(v)}H758`} stroke="#dce6e6"/><text x="5" y={y(v)+4} fontSize="11" fill="#5e6d6e">{v.toFixed(1)}</text></g>)}
  {series.map((s,i)=>{let previous=false;const path=s.points.filter((_,j)=>j%Math.max(1,Math.ceil(s.points.length/600))===0).map(p=>{if(p.normalized===null){previous=false;return '';}const out=`${previous?'L':'M'}${x(p.pts_ms).toFixed(2)},${y(p.normalized).toFixed(2)}`;previous=true;return out;}).join(' ');return <path key={s.id} d={path} stroke={colors[i%colors.length]} strokeWidth="2" fill="none"/>;})}
  {marks.map(c=><g key={c.id}><title>候选 {formatPts(c.pts_ms)}，非动作真值</title><path d={`M${x(c.pts_ms)} 26V190`} stroke="#c87824" strokeOpacity=".3"/><circle cx={x(c.pts_ms)} cy="190" r="4" fill="#c87824" onClick={e=>{e.stopPropagation();onSeek(c.pts_ms);}}/></g>)}
  <path d={`M${x(cursor)} 22V194`} stroke="#203e47" strokeDasharray="4 3"/>
  <text x="42" y="217" fontSize="11" fill="#5e6d6e">{formatPts(start)}</text><text x="690" y="217" fontSize="11" fill="#5e6d6e">{formatPts(end)}</text>
 </svg>;
}

export default function LocalMotionPanel(){
 const [runs,setRuns]=useState<MotionReport[]>([demo]),[runId,setRunId]=useState(demo.run_id);
 const report=runs.find(r=>r.run_id===runId)!;
 const [armId,setArmId]=useState(report.primary.arm),[shown,setShown]=useState<string[]>(['global:r3d','hands:r3d','hands:frame_difference','hands:block_motion','background:frame_difference']);
 const [cursor,setCursor]=useState(report.settings.start_ms),[media,setMedia]=useState(''),[mediaStatus,setMediaStatus]=useState('选择原片后核验SHA；原片不会上传');
 const [notice,setNotice]=useState(''),[rois,setRois]=useState<Roi[]>(structuredClone(report.settings.rois)),[roiId,setRoiId]=useState('hands');
 const [drawing,setDrawing]=useState(false),[manual,setManual]=useState<ManualDocument>(()=>blankReview(report)),[edited,setEdited]=useState(false);
 const [newBoundary,setNewBoundary]=useState('0'),[kind,setKind]=useState<ManualEvent['kind']>('unspecified');
 const video=useRef<HTMLVideoElement>(null),reportInput=useRef<HTMLInputElement>(null),reviewInput=useRef<HTMLInputElement>(null),fileNonce=useRef(0),drag=useRef<number[]|null>(null);
 const arm=report.arms.find(a=>a.id===armId)??report.arms[0],roi=rois.find(r=>r.id===roiId)??rois[0];
 const decisions=useMemo(()=>latestDecisions(manual),[manual]);
 const draftChanged=JSON.stringify(rois)!==JSON.stringify(report.settings.rois);
 const visible=arm.series.filter(s=>shown.includes(s.id));
 useEffect(()=>{
  fileNonce.current++;setArmId(report.primary.arm);setCursor(report.settings.start_ms);setMedia('');setMediaStatus('选择原片后核验SHA；原片不会上传');setRois(structuredClone(report.settings.rois));setRoiId(report.settings.rois.find(r=>r.role==='hands')?.id??report.settings.rois[0].id);setDrawing(false);setEdited(false);
  try{const saved=localStorage.getItem('jianwei-b1-review:'+report.run_id);const next=saved?parseReview(saved,report):report.manual_review?.manual_document??blankReview(report);setManual(next);setEdited(report.manual_review?JSON.stringify(next)!==JSON.stringify(report.manual_review.manual_document):next.events.length>0);}catch{setManual(blankReview(report));setNotice('本机旧复核记录不匹配，未自动套用。');}
 },[report.run_id]);
 useEffect(()=>()=>{if(media)URL.revokeObjectURL(media);},[media]);
 useEffect(()=>{try{localStorage.setItem('jianwei-b1-review:'+manual.run_id,JSON.stringify(manual));}catch{setNotice('本机复核存储不可用，请导出JSON保全。');}},[manual]);
 async function loadReport(file?:File){if(!file)return;try{if(file.size>8*1024*1024)throw Error('研究报告超过8 MiB限制');const next=parseMotionReport(await file.text());setRuns(old=>[...old.filter(v=>v.run_id!==next.run_id),next]);setRunId(next.run_id);setNotice('已载入本地实验记录；没有上传文件或重新计算。');}catch(e){setNotice((e as Error).message);}}
 async function associate(file?:File){
  if(!file)return;const nonce=++fileNonce.current;setMedia('');setMediaStatus('正在核验本机原片SHA…');
  try{if(file.size!==report.source.bytes)throw Error('原片字节数不匹配，未加载视频');const hash=sha256(await file.arrayBuffer());if(nonce!==fileNonce.current)return;if(hash!==report.source.sha256)throw Error('原片SHA不匹配，未加载视频');if(report.source.first_pts_ms!==0)throw Error('源PTS起点非零，需要显式播放器时钟映射，当前未自动定位');setMedia(URL.createObjectURL(file));setMediaStatus('原片SHA已核验，正在读取视频画面…');}catch(e){if(nonce===fileNonce.current)setMediaStatus((e as Error).message);}
 }
 function seek(pts:number){const target=Math.max(report.settings.start_ms,Math.min(report.settings.end_ms,Math.round(pts)));setCursor(target);setNewBoundary(String(target));if(video.current&&media)video.current.currentTime=target/1000;}
 function updateReview(next:ManualDocument){setManual(next);setEdited(true);}
 function action(target:string|null,type:ManualEvent['action'],value?:number){
  const event:ManualEvent={id:crypto.randomUUID(),target_id:target,action:type,pts_ms:value??null,kind,note:''};
  const next={...manual,events:[...manual.events,event]};
  try{parseReview(JSON.stringify(next),report);updateReview(next);setNotice('已追加本机复核记录；算法候选保持原样。');}catch(e){setNotice((e as Error).message);}
 }
 function exportReview(){try{if(!manual.annotator.trim())throw Error('请填写审核人再导出');parseReview(JSON.stringify(manual),report);downloadText(`b1-${report.run_id.slice(0,12)}-manual.json`,JSON.stringify(manual,null,2));}catch(e){setNotice((e as Error).message);}}
 async function importReview(file?:File){if(!file)return;try{if(file.size>2*1024*1024)throw Error('复核文件超过限制');updateReview(parseReview(await file.text(),report));setNotice('已核验并载入独立人工复核文件。');}catch(e){setNotice((e as Error).message);}}
 function changeBox(index:number,value:number){setRois(rois.map(r=>r.id===roi.id?{...r,xyxy:r.xyxy.map((v,i)=>i===index?value:v)}:r));}
 function point(e:React.PointerEvent<SVGSVGElement>){const b=e.currentTarget.getBoundingClientRect();return [Math.max(0,Math.min(1,(e.clientX-b.left)/b.width)),Math.max(0,Math.min(1,(e.clientY-b.top)/b.height))];}
 function pointerDown(e:React.PointerEvent<SVGSVGElement>){if(!drawing)return;drag.current=point(e);e.currentTarget.setPointerCapture(e.pointerId);}
 function pointerMove(e:React.PointerEvent<SVGSVGElement>){if(!drawing||!drag.current)return;const p=point(e),start=drag.current;const box=[Math.min(start[0],p[0]),Math.min(start[1],p[1]),Math.max(start[0],p[0]),Math.max(start[1],p[1])];setRois(old=>old.map(r=>r.id===roi.id?{...r,xyxy:box.map(v=>Math.round(v*10000)/10000)}:r));}
 return <div className="lm-panel">
  <div className="lm-notice"><strong>{report.evidence_class==='synthetic_engineering'?'合成素材 · 工程演示':'真实开发视频 · 回顾性探索'}</strong><span>无动作识别结论，不开放正式科研或P0.4；来源以实验文件为准，本轮仅B.1。</span></div>
  <div className="panel lm-toolbar"><label className="field"><span>实验版本</span><select aria-label="局部动态实验版本" value={runId} onChange={e=>setRunId(e.target.value)}>{runs.map(r=><option key={r.run_id} value={r.run_id}>{r.source.asset_id} · {r.run_id.slice(0,12)}</option>)}</select></label><button className="button secondary" onClick={()=>reportInput.current?.click()}><Upload size={15}/>载入实验JSON</button><button className="button secondary" onClick={()=>downloadText(`b1-${report.run_id.slice(0,12)}-report.json`,JSON.stringify(report,null,2))}><Download size={15}/>导出实验结果</button><input hidden ref={reportInput} type="file" accept=".json" aria-label="局部动态实验JSON" onChange={e=>{void loadReport(e.target.files?.[0]);e.target.value='';}}/></div>
  {notice&&<div className="lm-message" role="status">{notice}</div>}
  <div className="lm-top-grid">
   <section className="panel"><div className="panel-heading"><h2>原片与固定ROI</h2><span className="pill">{report.source.width} × {report.source.height}</span></div>
    <div className="lm-video-wrap" style={{aspectRatio:`${report.source.width}/${report.source.height}`}}>
     {media?<video ref={video} src={media} controls preload="auto" aria-label="局部动态原片" onLoadedData={()=>{if(video.current?.videoWidth!==report.source.width||video.current?.videoHeight!==report.source.height){setMediaStatus('解码显示尺寸不匹配，定位未验证');setMedia('');}else{setMediaStatus('原片SHA与显示尺寸均已核验');seek(cursor);}}} onError={()=>setMediaStatus('原片无法解码，播放未验证；没有用图片冒充视频')} onTimeUpdate={()=>{if(video.current)setCursor(Math.round(video.current.currentTime*1000));}}/>:<div className="lm-video-empty"><ScanLine size={30}/><p>选择本机原片以查看画面</p><small>Demo不含工业视频、帧图或权重</small></div>}
     <svg className={`lm-roi-overlay ${drawing?'drawing':''}`} viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="ROI框选画布" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={()=>{drag.current=null;}}>
      {rois.map((r,i)=><g key={r.id}><rect x={r.xyxy[0]*1000} y={r.xyxy[1]*1000} width={Math.max(0,(r.xyxy[2]-r.xyxy[0])*1000)} height={Math.max(0,(r.xyxy[3]-r.xyxy[1])*1000)} fill="none" stroke={colors[i%colors.length]} strokeWidth={r.id===roi.id?7:3}/></g>)}
     </svg>
    </div>
    <label className="lm-file">选择本机原视频<input type="file" accept="video/mp4" aria-label="局部动态原视频文件" onChange={e=>{void associate(e.target.files?.[0]);e.target.value='';}}/></label><p className="muted" role="status">{mediaStatus}</p>
    <div className="lm-fields"><label className="field"><span>编辑ROI</span><select aria-label="编辑ROI" value={roi.id} onChange={e=>{setRoiId(e.target.value);setDrawing(false);}}>{rois.map(r=><option key={r.id} value={r.id}>{roleNames[r.role]} · {r.id}</option>)}</select></label><button disabled={roi.role==='global'} className={`button ${drawing?'primary':'secondary'}`} onClick={()=>{if(!drawing)video.current?.pause();setDrawing(!drawing);}}>{drawing?'退出框选':'框选ROI'}</button></div>
    <div className="lm-box-fields">{['x1','y1','x2','y2'].map((label,i)=><label className="field" key={label}><span>{label}</span><input type="number" min="0" max="1" step=".01" aria-label={`ROI ${label}`} value={Number.isFinite(roi.xyxy[i])?roi.xyxy[i]:''} onChange={e=>changeBox(i,Number(e.target.value))}/></label>)}</div>
    <label className="field"><span>可见性记录</span><select aria-label="ROI可见性" value={roi.visibility} onChange={e=>setRois(rois.map(r=>r.id===roi.id?{...r,visibility:e.target.value}:r))}><option value="unverified">未逐帧审核</option><option value="visible">人工认为可见</option><option value="occluded">严重遮挡，重提时排除</option></select></label>
    {!validBox(roi.xyxy)&&<p role="alert">ROI为空或越界；禁止导出。</p>}
    {draftChanged&&<p className="lm-warning">ROI草稿已变化。曲线仍对应原已执行ROI，须写入新的system.yaml并重新提取。</p>}
    <button className="button secondary" onClick={()=>{try{downloadText('b1-roi-draft.yaml',stringify(roiDraft(report,rois)));setNotice('只导出配置片段；未修改原实验或生成新特征。');}catch(e){setNotice((e as Error).message);}}}><Download size={15}/>导出ROI配置草稿</button>
   </section>
   <section className="panel lm-curves"><div className="panel-heading"><h2>对齐动态响应</h2><span className="pill">描述统计 ≠ 动作准确率</span></div>
    <label className="field"><span>窗口与采样密度</span><select aria-label="局部动态窗口密度" value={arm.id} onChange={e=>setArmId(e.target.value)}>{report.arms.map(a=><option key={a.id} value={a.id}>{a.window_ms} ms · {a.fps} fps</option>)}</select></label>
    <div className="lm-series-controls">{arm.series.map(s=><label key={s.id}><input type="checkbox" checked={shown.includes(s.id)} onChange={e=>setShown(e.target.checked?[...shown,s.id]:shown.filter(id=>id!==s.id))}/>{roleNames[s.role]} / {methods[s.method]}</label>)}</div>
    <Curves series={visible} start={report.settings.start_ms} end={report.settings.end_ms} cursor={cursor} onSeek={seek} marks={arm.id===report.primary.arm?report.primary.candidates:[]}/>
    <div className="lm-legend">{visible.map((s,i)=><span key={s.id}><i style={{background:colors[i%colors.length]}}/>{s.roi} / {methods[s.method]}</span>)}</div>
    <p className="muted">前缀MAD标准化，离线平滑；异常区间不补造数值。R3D沿用官方中心裁剪，ROI放大及视野变化也是混杂因素。</p>
    <label className="field"><span>原始时间轴PTS · {formatPts(cursor)}</span><input type="range" min={report.settings.start_ms} max={report.settings.end_ms} step="1" value={cursor} aria-label="局部动态PTS时间轴" onChange={e=>seek(Number(e.target.value))}/></label>
    <div className="button-row"><button className="button secondary" disabled={!media} onClick={()=>void video.current?.play().catch(()=>setMediaStatus('浏览器阻止播放，请使用原生播放按钮'))}><Play size={14}/>播放原片</button><code>{cursor} ms</code></div>
    <div className="lm-scroll"><table className="lm-table"><thead><tr><th>曲线</th><th>有效 / 缺失</th><th>原值中位数</th><th>P90</th></tr></thead><tbody>{visible.map(s=><tr key={s.id}><td>{s.id}</td><td>{s.stats.valid} / {s.stats.missing}</td><td>{number(s.stats.p50)}</td><td>{number(s.stats.p90)}</td></tr>)}</tbody></table></div>
   </section>
  </div>
  <section className="panel"><div className="panel-heading"><h2>变化候选与人工边界</h2><span className="pill">{report.primary.count} 个主候选 · 尚未赋动作语义</span></div><p className="muted">固定主实验 {report.primary.arm}，阈值 {report.primary.threshold}，平滑 {report.primary.smoothing_ms} ms。上限不是配额；时间点支持片段而非精确动作边界。人工记录只存本机并独立导出。</p>
   <div className="lm-fields"><label className="field"><span>审核人</span><input aria-label="边界审核人" value={manual.annotator} onChange={e=>updateReview({...manual,annotator:e.target.value})}/></label><label className="field"><span>人工边界类型</span><select aria-label="人工边界类型" value={kind} onChange={e=>setKind(e.target.value as ManualEvent['kind'])}><option value="unspecified">未指定</option><option value="onset">开始</option><option value="offset">结束</option><option value="pause">停顿</option><option value="switch">工步切换（人工）</option></select></label><button className="button secondary" onClick={exportReview}><Download size={14}/>导出人工复核</button><button className="button secondary" onClick={()=>reviewInput.current?.click()}>导入复核JSON</button><input hidden ref={reviewInput} type="file" accept=".json" aria-label="人工边界复核JSON" onChange={e=>{void importReview(e.target.files?.[0]);e.target.value='';}}/></div>
   <div className="lm-scroll"><table className="lm-table" data-testid="lm-candidates"><thead><tr><th>定位 / PTS</th><th>来源与候选类型</th><th>支持区间</th><th>复核状态</th><th>人工操作</th></tr></thead><tbody>{report.primary.candidates.map(c=><tr key={c.id}><td><button className="lm-link" onClick={()=>seek(c.pts_ms)}>{formatPts(c.pts_ms)}</button><small>{c.pts_ms} ms</small></td><td>{c.roi} · {c.kind}<small>{c.series} / z={c.score.toFixed(2)}</small></td><td>{formatPts(c.start_ms)} – {formatPts(c.end_ms)}</td><td>{decisions.get(c.id)?.action??'待复核'}</td><td><div className="button-row"><button className="button secondary small" onClick={()=>action(c.id,'confirm')}>确认</button><button className="button secondary small" onClick={()=>{const pts=Number(newBoundary);if(Number.isFinite(pts))action(c.id,'adjust',pts);}}>按当前输入修订</button><button className="button secondary small" onClick={()=>action(c.id,'delete')}>删除候选</button></div></td></tr>)}</tbody></table></div>
   {!report.primary.count&&<p>当前配置没有主候选，未强行补齐数量；仍可添加独立人工边界。</p>}
   <div className="lm-fields"><label className="field"><span>人工PTS（ms）</span><input type="number" aria-label="人工边界PTS" value={newBoundary} onChange={e=>setNewBoundary(e.target.value)}/></label><button className="button secondary" onClick={()=>action(null,'add',Number(newBoundary))}>新增人工边界</button><label><input type="checkbox" aria-label="声明完整边界覆盖" checked={manual.coverage_complete} onChange={e=>updateReview({...manual,coverage_complete:e.target.checked})}/>已完整审核整个范围（包含漏检）</label></div>
   <p className="lm-warning">{candidateEvaluationNote(report,edited)}。仅确认推荐点不具备Recall/F1真值；导出后使用 analyze_b1 --review 生成可复核评价，再载入新报告。</p>
   {report.manual_review&&!edited&&<p>Precision {number(report.manual_review.evaluation.precision)} · Recall {number(report.manual_review.evaluation.recall)} · F1 {number(report.manual_review.evaluation.f1)}；{report.manual_review.evaluation.annotation_mode}</p>}
   <p className="muted">人工覆盖 {formatPts(manual.start_ms)} – {formatPts(manual.end_ms)}；独立人工事件 {manual.events.length} 条；不会改写 {report.primary.candidates.length} 个算法候选。固定评价容差 {report.settings.boundary_tolerance_ms} ms，只评价边界时间，不评价动作语义。</p>
  </section>
  <section className="panel"><div className="panel-heading"><h2>全部参数对照与负结果</h2><span className="pill">操作 / 背景 / 偏移</span></div>
   <div className="lm-scroll"><table className="lm-table"><thead><tr><th>操作曲线 / 背景对照</th><th>共同PTS点</th><th>相关性</th><th>均值比</th></tr></thead><tbody>{arm.operation_background.map(v=><tr key={v.operation}><td>{v.operation} / {v.control}</td><td>{v.aligned_points}</td><td>{number(v.correlation)}</td><td>{number(v.mean_ratio)}</td></tr>)}</tbody></table></div>
   <p className="muted">相关性与幅度比只描述响应；背景低纹理、光照和ROI尺寸都会影响它们。没有把比值更大认定为检测更好。</p>
   {arm.offset_sensitivity.length>0&&<div className="lm-scroll"><table className="lm-table"><thead><tr><th>偏移对照</th><th>相关性</th><th>相对绝对变化</th></tr></thead><tbody>{arm.offset_sensitivity.map(v=><tr key={v.offset}><td>{v.base} / {v.offset}</td><td>{number(v.correlation)}</td><td>{number(v.relative_absolute_change)}</td></tr>)}</tbody></table></div>}
   <details><summary>查看当前窗口的全部阈值和平滑组合（{arm.grid.length} 项）</summary><div className="lm-scroll lm-grid-table"><table className="lm-table"><thead><tr><th>曲线</th><th>阈值</th><th>平滑ms</th><th>候选数</th></tr></thead><tbody>{arm.grid.map((g,i)=><tr key={i}><td>{g.series}</td><td>{g.threshold}</td><td>{g.smoothing_ms}</td><td>{g.count}</td></tr>)}</tbody></table></div></details>
  </section>
  <section className="panel"><div className="panel-heading"><h2>实验版本与可复现信息</h2><CheckCircle2 size={18}/></div><dl className="lm-version"><dt>来源</dt><dd>{report.source.asset_id} · {report.evidence_class}</dd><dt>原片SHA</dt><dd><code>{report.source.sha256}</code></dd><dt>冻结模型</dt><dd>{report.encoder}<br/><code>{report.model_sha256}</code></dd><dt>特征配方</dt><dd><code>{report.recipe_hash}</code></dd><dt>分析 / 实验ID</dt><dd><code>{report.run_id}</code></dd><dt>执行范围</dt><dd>{report.feature_rows} 条ROI记录；{report.arms.length} 个窗口/密度组合；数据库未参与离线提取。</dd></dl><p className="muted"><TriangleAlert size={14}/> 固定16帧R3D输入；低采样密度会重复原帧，不能当作独立观测。现有开发视频不能充当正式独立测试；本轮不启动B.2。</p></section>
 </div>;
}
