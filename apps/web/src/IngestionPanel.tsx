import {useCallback,useEffect,useRef,useState} from 'react';
import {CheckCircle2,Clock3,Database,Download,FileUp,LoaderCircle,Play,RefreshCw,TriangleAlert} from 'lucide-react';
import {sha256} from 'js-sha256';
import {isDemo} from './sceneService';
import {downloadText} from './service';
import {downloadManifest,importJob,importJobs,ingestionCapabilities,retryImport,runImportExample,submitImport,uploadArtifact} from './ingestionService';
import {importMetadata,parseFeatureSpace} from './ingestionTypes';
import type {ImportForm,ImportJob,ImportManifest,IngestionCapabilities} from './ingestionTypes';
import type {Scene} from './sceneTypes';

const statusNames={queued:'排队中',running:'处理中',succeeded:'导入完成',blocked:'等待条件',failed:'导入失败'};
const isPending=(job:ImportJob)=>job.status==='queued'||job.status==='running';
const json=(value:unknown)=>JSON.stringify(value,null,2);
const featureTemplate={contract_version:'3.1',encoder_sha:'',preprocessing_sha:'填写实际预处理 SHA256',sampling_sha:'填写实际采样 SHA256',roi_policy_hash:'填写实际 ROI 策略 SHA256',pooling:'填写实际 pooling',dimension:'填写实际维度（正整数）'};
const initialForm=(scene:Scene):ImportForm=>({sourceId:scene.sources.find(s=>s.type==='file')?.id??'',format:'mp4',contextFields:{},episodesText:'[]',episodeOrigin:scene.episode_policy.mode==='external_cycle'?'external':'manual',anchorsText:'[]',featureSpaceText:json(featureTemplate),sourceHash:''});
function Field({label,help,children}:{label:string;help?:string;children:React.ReactNode}){return <label className="field"><span>{label}</span>{children}{help&&<small>{help}</small>}</label>;}
function Hash({value}:{value:unknown}){return <code className="ingest-hash">{typeof value==='string'&&value?value:'未提供'}</code>;}
function records(value:unknown):Record<string,unknown>[]{return Array.isArray(value)?value.filter(v=>v&&typeof v==='object'):[];}
function text(value:unknown,fallback='未记录'){return typeof value==='string'||typeof value==='number'?String(value):fallback;}

function ManifestDetails({manifest}:{manifest:ImportManifest}){
 const episodes=records(manifest.episodes),domains=records(manifest.time_domains??manifest.domains),batches=records(manifest.feature_batches);
 const rowCount=manifest.total_rows??manifest.feature_rows??manifest.row_count??batches.length;
 return <div className="ingest-manifest">
  <div className="ingest-metrics"><div><strong>{text(rowCount,'—')}</strong><span>特征行</span></div><div><strong>{episodes.length}</strong><span>周期</span></div><div><strong>{domains.length}</strong><span>时间域</span></div><div><strong>{text(manifest.split,'unassigned')}</strong><span>数据分区</span></div></div>
  <dl className="ingest-facts"><div><dt>数据来源</dt><dd>{manifest.data_origin==='synthetic_features'?'合成特征 · 仅验证导入流程':'用户上传数据'}</dd></div><div><dt>来源核验</dt><dd>{manifest.source_verified?'原视频字节已核验':manifest.source_verification==='synthetic'?'明确的合成来源':'来源声明未核验'}</dd></div><div><dt>原始来源 SHA256</dt><dd><Hash value={manifest.source_hash}/></dd></div><div><dt>输入文件 SHA256</dt><dd><Hash value={manifest.artifact_hash}/></dd></div><div><dt>特征空间 SHA256</dt><dd><Hash value={manifest.feature_space_id}/></dd></div></dl>
  {manifest.feature_space&&<p className="ingest-note">特征维度 {manifest.feature_space.dimension} · {manifest.feature_space.pooling}。文件指纹不等于已证明外部向量由该编码器生成。</p>}
  {!!episodes.length&&<details open><summary>周期边界</summary><div className="ingest-table-wrap"><table><thead><tr><th>周期</th><th>时间域</th><th>PTS 毫秒</th><th>边界状态</th></tr></thead><tbody>{episodes.map((episode,i)=><tr key={i}><td>{text(episode.episode_id)}</td><td>{text(episode.time_domain_id??episode.domain_index)}</td><td>{text(episode.pts_start_ms??episode.start_ms)} — {text(episode.pts_end_ms??episode.end_ms)}</td><td>{episode.complete===false?'不完整':episode.complete===true?'完整':'见清单'}</td></tr>)}</tbody></table></div></details>}
  {!!domains.length&&<details><summary>时间域与采集时钟</summary>{domains.map((domain,i)=><p className="ingest-note" key={i}>{text(domain.time_domain_id??domain.id??domain.domain_index)} · PTS {text(domain.start_ms??domain.pts_start_ms)} — {text(domain.end_ms??domain.pts_end_ms)} ms · UTC {text(domain.capture_start_utc??domain.utc_start,'未知，未推测采集时间')}</p>)}</details>}
  <details><summary>质量检测与完整清单</summary><pre>{json(manifest)}</pre></details>
 </div>;
}

export default function IngestionPanel({scene}:{scene:Scene}){
 const [form,setForm]=useState<ImportForm>(()=>initialForm(scene));
 const [input,setInput]=useState<File|null>(null),[fingerprint,setFingerprint]=useState<File|null>(null),[sourceVideo,setSourceVideo]=useState<File|null>(null);
 const [capabilities,setCapabilities]=useState<IngestionCapabilities|null>(null),[jobs,setJobs]=useState<ImportJob[]>([]),[selected,setSelected]=useState('');
 const [detail,setDetail]=useState<ImportJob|null>(null),[detailLoading,setDetailLoading]=useState(false);
 const [busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[loading,setLoading]=useState(true);
 const active=useRef(true),refreshToken=useRef(0);
 const refresh=useCallback(async()=>{const token=++refreshToken.current;try{const rows=await importJobs(scene.id);if(active.current&&token===refreshToken.current)setJobs(rows);}catch(e){if(active.current&&token===refreshToken.current)setError((e as Error).message);}},[scene.id]);
 useEffect(()=>{active.current=true;void Promise.all([ingestionCapabilities().then(c=>{if(active.current)setCapabilities(c);}),refresh()]).catch(e=>{if(active.current)setError((e as Error).message);}).finally(()=>{if(active.current)setLoading(false);});return()=>{active.current=false;refreshToken.current++;};},[refresh]);
 useEffect(()=>{if(!jobs.some(isPending)||isDemo)return;const timer=setTimeout(()=>void refresh(),1000);return()=>clearTimeout(timer);},[jobs,refresh]);
 const update=(patch:Partial<ImportForm>)=>setForm(previous=>({...previous,...patch}));
 const summary=jobs.find(j=>j.id===selected)??jobs[0];
 const chosen=detail&&detail.id===summary?.id&&detail.updated_at===summary.updated_at&&detail.status===summary.status?detail:summary;
 useEffect(()=>{
  let current=true;
  if(!summary){setDetail(null);setDetailLoading(false);return;}
  if(summary.manifest||summary.demo_reference){setDetail(summary);setDetailLoading(false);return;}
  setDetailLoading(true);
  void importJob(scene.id,summary.id).then(job=>{if(current&&active.current)setDetail(job);}).catch(e=>{if(current&&active.current)setError((e as Error).message);}).finally(()=>{if(current&&active.current)setDetailLoading(false);});
  return()=>{current=false;};
 },[scene.id,summary?.id,summary?.updated_at,summary?.status,summary?.has_manifest]);
 const availableSources=scene.sources.filter(s=>s.type==='file');
 const requiresCycles=!scene.episode_policy.mode.includes('fixed_window');
 const blockedForm=isDemo||!capabilities?.enabled||!!busy;
 const acceptJob=async(job:ImportJob)=>{if(!active.current)return;setSelected(job.id);setJobs(previous=>[job,...previous.filter(item=>item.id!==job.id)]);await refresh();};
 async function operation(label:string,work:()=>Promise<void>){setBusy(label);setError('');setNotice('');try{await work();}catch(e){if(active.current)setError((e as Error).message);}finally{if(active.current)setBusy('');}}
 async function upload(){await operation('校验并上传文件',async()=>{
  if(!input)throw new Error('请先选择输入文件');
  if(!capabilities?.enabled)throw new Error('当前服务未启用导入');
  for(const file of [input,fingerprint,sourceVideo])if(file&&file.size>capabilities.max_upload_bytes)throw new Error(`文件不能超过 ${Math.floor(capabilities.max_upload_bytes/1048576)} MiB`);
  const metadata=importMetadata(scene,form);
  let space;
  if(form.format==='npz'){
   if(!fingerprint)throw new Error('NPZ 需要编码器指纹文件');
   if(!sourceVideo&&!form.sourceHash.trim())throw new Error('请提供原视频文件或声明原始来源 SHA256');
   space=parseFeatureSpace(form.featureSpaceText,sha256(await fingerprint.arrayBuffer()));
  }
  const uploaded=await uploadArtifact(input,form.format);
  const encoder=form.format==='npz'&&fingerprint?await uploadArtifact(fingerprint,'encoder'):undefined;
  const source=form.format==='npz'&&sourceVideo?await uploadArtifact(sourceVideo,'mp4'):undefined;
  await acceptJob(await submitImport(scene.id,{...metadata,upload_id:uploaded.id,...(space?{feature_space:space}:{}),...(encoder?{encoder_artifact_id:encoder.id}:{}),...(source?{source_upload_id:source.id}:{})}));
  if(active.current)setNotice('已提交导入；任务和结果会保存在服务器，刷新后可继续查看。');
 });}
 async function example(){await operation(isDemo?'读取随附示例':'创建示例导入',async()=>{await acceptJob(await runImportExample(scene.id));if(active.current)setNotice(isDemo?'正在查看随附的服务端示例记录；浏览器没有执行导入。':'已通过服务端实际导入路径提交合成特征示例。');});}
 function template(){downloadText(`${scene.id}.import-template.json`,json({format:form.format,source_id:form.sourceId,data_origin:'real_video',requested_split:'unassigned',context:Object.fromEntries(Object.entries(scene.context_schema).map(([key,spec])=>[key,spec.type==='boolean'?false:spec.type==='integer'||spec.type==='number'?0:'请填写'])),episode_origin:requiresCycles?form.episodeOrigin:null,episodes:requiresCycles?[{episode_id:'cycle_1',domain_index:0,start_ms:0,end_ms:10000}]:[],clock_anchors:[],...(form.format==='npz'?{feature_space:featureTemplate,declared_source_hash:'填写原视频 SHA256'}:{})}));}
 return <div className="ingest-workspace">
  <section className="panel ingest-start"><div className="ingest-start-icon"><Database size={26}/></div><div><h2>先走通一份数据</h2><p>{isDemo?'查看实际服务端生成的合成示例清单。此页面离线运行，文件导入需启动完整应用。':'没有现场数据也可以开始：使用确定性合成特征，验证上传、周期校验、缓存与清单。'}</p><small>示例不产生动作标签、识别准确率或学习收益。</small></div><button className="button primary" onClick={example} disabled={!!busy||loading||capabilities?.example_available===false}><Play size={16}/>{isDemo?'查看示例流程':'运行示例导入'}</button></section>
  {error&&<div className="ingest-alert" role="alert"><TriangleAlert size={18}/><span>{error}</span></div>}
  {notice&&<p className="ingest-feedback" role="status"><CheckCircle2 size={16}/>{notice}</p>}
  <div className="ingest-grid">
   <section className="panel ingest-upload"><div className="panel-heading"><h2>接入文件</h2><span className="pill">MP4 / NPZ</span></div><div className="jw-fields">
    {isDemo&&<p className="ingest-note">离线演示保留完整表单供查看。启动本地 API 服务后，可上传自己的文件。</p>}
    {!isDemo&&capabilities&&!capabilities.enabled&&<p className="ingest-note">当前配置未开启 ingestion，请先更新运行配置。</p>}
    <fieldset disabled={blockedForm}>
     <div className="ingest-form-row"><Field label="输入格式"><select value={form.format} onChange={e=>{update({format:e.target.value as 'mp4'|'npz'});setInput(null);}}><option value="mp4">MP4 视频</option><option value="npz">NPZ 已有特征</option></select></Field><Field label="文件来源"><select value={form.sourceId} onChange={e=>update({sourceId:e.target.value})}>{!availableSources.length&&<option value="">请先注册文件来源</option>}{availableSources.map(source=><option key={source.id} value={source.id}>{source.id}</option>)}</select></Field></div>
     <Field label={form.format==='mp4'?'MP4 视频文件':'NPZ 特征文件'} help={capabilities?.max_upload_bytes?`单文件上限 ${Math.floor(capabilities.max_upload_bytes/1048576)} MiB`:'选择原始文件；文件名不用于来源身份'}><input key={form.format} type="file" accept={form.format==='mp4'?'.mp4,video/mp4':'.npz'} onChange={e=>setInput(e.target.files?.[0]??null)}/></Field>
     {!!Object.keys(scene.context_schema).length&&<div className="ingest-context"><h3>场景上下文</h3>{Object.entries(scene.context_schema).map(([key,spec])=><Field key={key} label={`${key}${scene.session_policy.required_context_keys.includes(key)?'（必填）':''}`} help={spec.description}>{spec.type==='boolean'?<select value={form.contextFields[key]??''} onChange={e=>update({contextFields:{...form.contextFields,[key]:e.target.value}})}><option value="">请选择</option><option value="true">true</option><option value="false">false</option></select>:<input value={form.contextFields[key]??''} inputMode={spec.type==='integer'||spec.type==='number'?'decimal':undefined} onChange={e=>update({contextFields:{...form.contextFields,[key]:e.target.value}})}/>}</Field>)}</div>}
     <details className="ingest-settings" open={requiresCycles}><summary>{requiresCycles?'填写完整周期边界':'周期与采集时间（可选）'}</summary><p className="ingest-note">{requiresCycles?'由人工或外部系统提供真实周期边界，不能跨越时间域与缺口。':`当前按 ${scene.episode_policy.window_seconds} 秒固定窗口分组；末尾不足窗口会明确标记。`} PTS 单位为毫秒。</p>
      {requiresCycles&&<Field label="周期边界来源"><select value={form.episodeOrigin??'manual'} onChange={e=>update({episodeOrigin:e.target.value as 'manual'|'external'})}>{scene.episode_policy.mode!=='external_cycle'&&<option value="manual">人工提供</option>}{scene.episode_policy.mode!=='manual_cycle'&&<option value="external">外部系统</option>}</select></Field>}
      <Field label="周期边界 JSON" help={'格式：[{"episode_id":"cycle_1","domain_index":0,"start_ms":0,"end_ms":10000}]'}><textarea rows={4} spellCheck={false} value={form.episodesText} onChange={e=>update({episodesText:e.target.value})}/></Field>
     </details>
     <details className="ingest-settings"><summary>采集 UTC 锚点（可选）</summary><p className="ingest-note">未知时保持 []。PTS 重置后的新时间域需要独立锚点；不会使用文件修改时间代替。</p><Field label="采集锚点 JSON" help={'格式：[{"domain_index":0,"pts_ms":0,"capture_utc":"2026-10-23T08:00:00+08:00"}]'}><textarea rows={3} spellCheck={false} value={form.anchorsText} onChange={e=>update({anchorsText:e.target.value})}/></Field></details>
     {form.format==='npz'&&<div className="ingest-npz"><h3>已有特征的来源凭据</h3><p className="ingest-note">NPZ 必须包含 features[N,D]、pts_ms[N]、durations_ms[N]。特征只接受有限的 float32 / float64 数值。</p><Field label="编码器指纹文件" help="用于计算文件 SHA256，不会执行上传的模型。"><input type="file" onChange={e=>setFingerprint(e.target.files?.[0]??null)}/></Field><Field label="特征空间 JSON" help="encoder_sha 可留空，由所选文件计算；其他哈希需来自实际处理配置。"><textarea rows={8} spellCheck={false} value={form.featureSpaceText} onChange={e=>update({featureSpaceText:e.target.value})}/></Field><Field label="对应原视频（可选）"><input type="file" accept=".mp4,video/mp4" onChange={e=>setSourceVideo(e.target.files?.[0]??null)}/></Field><Field label="声明原始来源 SHA256" help="没有原视频时必须填写；此时来源保持未核验，不能分配训练或评测分区。"><input value={form.sourceHash} onChange={e=>update({sourceHash:e.target.value})} spellCheck={false}/></Field></div>}
     <button className="button primary ingest-submit" disabled={!input||!availableSources.length} onClick={upload}>{busy?<LoaderCircle size={16} className="spin"/>:<FileUp size={16}/>}上传并开始导入</button>
    </fieldset>
    <button className="button secondary" onClick={template}><Download size={15}/>下载元信息模板</button>
    <p className="ingest-note">{capabilities?.encoder_adapter==='existing_features'?'当前编码器配置接收已有特征；MP4 会完成解码检查，并等待真实权重后提取特征。':'MP4 仅使用服务器配置的冻结编码器。缺少权重或依赖时任务会说明等待原因。'}</p>
   </div></section>
   <section className="panel ingest-results"><div className="panel-heading"><h2>导入任务</h2><button className="icon-button" aria-label="刷新导入任务" disabled={!!busy} onClick={()=>void operation('刷新任务',refresh)}><RefreshCw size={17}/></button></div>
    {busy&&<div className="ingest-progress" role="status"><LoaderCircle size={16} className="spin"/>{busy}…</div>}
    {loading?<div className="jw-empty">正在读取导入记录…</div>:!jobs.length?<div className="jw-empty"><Database size={30}/><h2>还没有导入记录</h2><p>先运行示例，或选择文件开始。<br/>无需预先定义动作标签或 SOP。</p></div>:<><div className="ingest-job-list" aria-label="导入任务列表">{jobs.map(job=><button key={job.id} className={`ingest-job ${chosen?.id===job.id?'selected':''}`} onClick={()=>setSelected(job.id)}><span className={`ingest-status ${job.status}`}>{isPending(job)?<Clock3 size={14}/>:job.status==='succeeded'?<CheckCircle2 size={14}/>:<TriangleAlert size={14}/>} {job.demo_reference?'随附示例记录':statusNames[job.status]}</span><strong>{job.format.toUpperCase()} · {job.source_id}</strong><small>{job.created_at?new Date(job.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'服务端生成的合成示例'}</small></button>)}</div>
    {chosen&&<div className="ingest-job-detail"><div className="ingest-detail-heading"><div><h3>{chosen.demo_reference?'示例流程清单':statusNames[chosen.status]}</h3><code>{chosen.id}</code></div>{chosen.manifest&&<button className="button secondary small" onClick={()=>void operation('导出清单',()=>downloadManifest(scene.id,chosen))}><Download size={14}/>导出清单</button>}</div>
     {chosen.demo_reference&&<p className="ingest-note">这是随附的实际服务端示例记录。浏览器仅展示清单，不执行上传、解码或特征提取。</p>}
     {isPending(chosen)&&<p className="ingest-note"><LoaderCircle className="spin" size={14}/> 正在后台处理，页面会自动更新；刷新页面不会取消任务。</p>}
     {chosen.error&&<div className="ingest-blocker"><TriangleAlert size={18}/><div><strong>{chosen.error.message}</strong><code>{chosen.error.code}</code><p>补齐条件后可重试。配置或知识基线变化时，请新建导入保留原记录。</p>{!isDemo&&<button className="button secondary small" disabled={!!busy} onClick={()=>void operation('重试导入',async()=>{await acceptJob(await retryImport(scene.id,chosen.id));})}>重试导入</button>}</div></div>}
     {detailLoading&&<p className="ingest-note" role="status">正在读取任务详情…</p>}
     {chosen.manifest&&<ManifestDetails manifest={chosen.manifest}/>}<div className="ingest-flow"><span>文件指纹</span><span>边界校验</span><span>质量 / 特征</span><span>持久清单</span></div>
    </div>}</>}
   </section>
  </div>
 </div>;
}
