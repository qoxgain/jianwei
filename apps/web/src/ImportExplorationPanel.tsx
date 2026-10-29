import {useEffect,useRef,useState} from 'react';
import {Download,LoaderCircle,Play,TriangleAlert} from 'lucide-react';
import {importExploration} from './ingestionService';
import {downloadText} from './service';
import type {ExplorationReport} from './ingestionTypes';
import ReviewVideo from './ReviewVideo';
const seconds=(value:number)=>(value/1000).toFixed(2);

export default function ImportExplorationPanel({scene,importId}:{scene:string;importId:string}){
 const [report,setReport]=useState<ExplorationReport|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState<number|null>(null);
 const video=useRef<HTMLVideoElement>(null),active=useRef(true),pendingSeek=useRef<number|null>(null);
 useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
 async function load(){setBusy(true);setError('');try{const value=await importExploration(scene,importId);if(active.current)setReport(value);}catch(e){if(active.current)setError((e as Error).message);}finally{if(active.current)setBusy(false);}}
 function jump(index:number){if(!report)return;const element=video.current;setSelected(index);const at=report.segments[index].representative.start_ms/1000;pendingSeek.current=at;if(element&&element.readyState>=1){element.currentTime=at;pendingSeek.current=null;void element.play().catch(()=>{});}}
 function ready(){if(video.current&&pendingSeek.current!==null){video.current.currentTime=pendingSeek.current;pendingSeek.current=null;}}
 return <section className="import-exploration">
  <h3>回看本视频的匿名变化</h3><p className="ingest-note">只分析当前未分区来源。匿名分组与变化点供人工查看，不代表动作类别、学习收益或 SOP 合规结论。</p>
  {!report&&<button className="button secondary" onClick={load} disabled={busy}>{busy?<LoaderCircle className="spin" size={15}/>:<Play size={15}/>}查看匿名探索</button>}
  {error&&<p className="ingest-alert" role="alert"><TriangleAlert size={16}/>{error}</p>}
  {report&&<>
   <div className="ingest-metrics"><div><strong>{(report.coverage.fraction*100).toFixed(1)}%</strong><span>特征时间覆盖</span></div><div><strong>{report.eligible_rows}</strong><span>有效片段</span></div><div><strong>{report.segments.length}</strong><span>匿名分段</span></div><div><strong>{report.feature_space.dimension}</strong><span>冻结特征维度</span></div></div>
   <p className="ingest-note">覆盖 {seconds(report.coverage.covered_ms)} / {seconds(report.coverage.source_domain_ms)} 秒，重叠区间仅计一次。每个来源独立形成匿名分组；不能把不同工位的同名簇视为同一动作。</p>
   <ReviewVideo originalUrl={report.media_url} previewUrl={report.preview_url} label="匿名探索视频回查" videoRef={video} onReady={ready}/>
   {!report.direct_video_seek_supported&&<p className="ingest-note">此来源存在多个时间域或非零起点，当前播放器不能直接按原始 PTS 定位；请导出报告核对时间域。</p>}
   <div className="exploration-segments" aria-label="匿名分段列表">{report.segments.map((segment,index)=><div key={`${segment.time_domain_id}-${segment.start_ms}-${index}`} className={selected===index?'selected':''}><div><strong>{segment.anonymous_cluster_id}</strong><span>{seconds(segment.start_ms)}–{seconds(segment.end_ms)} 秒</span><small>代表片段 {seconds(segment.representative.start_ms)}–{seconds(segment.representative.end_ms)} 秒</small></div><button className="button secondary small" disabled={!report.direct_video_seek_supported} onClick={()=>jump(index)}>查看代表片段</button></div>)}</div>
   <details><summary>覆盖、排除区间与来源记录</summary><pre>{JSON.stringify({source_hash:report.source_hash,view_id:report.view_id,coverage:report.coverage,discarded_clips:report.discarded_clips,rejected_rows:report.rejected_rows,quality:report.quality,limitations:report.limitations},null,2)}</pre></details>
   <button className="button secondary small" onClick={()=>downloadText(`${importId}.exploration.json`,JSON.stringify(report,null,2))}><Download size={14}/>下载探索报告</button>
  </>}
 </section>;
}
