import {useEffect,useState} from 'react';
import type {RefObject} from 'react';
import {ExternalLink,LoaderCircle,TriangleAlert} from 'lucide-react';

/** Original bytes remain the evidence identity; compatibility video is display-only. */
export default function ReviewVideo({originalUrl,previewUrl,label,videoRef,onReady}:{originalUrl:string;previewUrl?:string;label:string;videoRef:RefObject<HTMLVideoElement|null>;onReady?:()=>void}){
 const [state,setState]=useState<'loading'|'ready'|'failed'>('loading');
 const url=previewUrl??originalUrl;
 useEffect(()=>setState('loading'),[url]);
 return <div className="review-video">
  <video key={url} ref={videoRef} controls preload="auto" src={url} aria-label={label}
   onLoadedData={event=>{if(event.currentTarget.videoWidth>0&&event.currentTarget.videoHeight>0){setState('ready');onReady?.();}else setState('failed');}}
   onError={()=>setState('failed')}/>
  {state==='loading'&&<p className="ingest-note" role="status"><LoaderCircle className="spin" size={14}/> {previewUrl?'正在准备或加载兼容预览，首次转换可能需要数分钟。':'正在加载原始视频画面…'}</p>}
  {state==='failed'&&<p className="ingest-note" role="status"><TriangleAlert size={14}/> 当前预览无法显示视频画面，可下载原片复核。仅有音频或时长信息不代表视频播放成功。</p>}
  {previewUrl&&<p className="ingest-note">兼容预览仅用于回查画面。特征、时间和来源指纹仍以原始录像为依据。</p>}
  <a className="button secondary small" href={originalUrl} target="_blank" rel="noreferrer">查看原片<ExternalLink size={14}/></a>
 </div>;
}
