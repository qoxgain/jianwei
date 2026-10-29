import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {captureMetadata} from './ingestionTypes';
class TestXHR {
 static instances:TestXHR[]=[];
 upload:{onprogress:((event:{loaded:number;total:number;lengthComputable:boolean})=>void)|null}={onprogress:null};
 method='';url='';timeout=0;responseType='';headers:Record<string,string>={};body:unknown;
 status=201;response:unknown={id:'upload',sha256:'a'.repeat(64),size_bytes:3,kind:'mp4'};
 onload:(()=>void)|null=null;onerror:(()=>void)|null=null;ontimeout:(()=>void)|null=null;onabort:(()=>void)|null=null;
 constructor(){TestXHR.instances.push(this);}
 open(method:string,url:string){this.method=method;this.url=url;}
 setRequestHeader(name:string,value:string){this.headers[name]=value;}
 send(body:unknown){this.body=body;}
 abort(){this.onabort?.();}
}
beforeEach(()=>{vi.resetModules();vi.stubGlobal('location',{protocol:'http:',search:''});vi.stubGlobal('XMLHttpRequest',TestXHR);TestXHR.instances=[];});
afterEach(()=>vi.unstubAllGlobals());
const file=()=>new File(['123'],'岗1.mp4');
describe('streaming browser upload transport',()=>{
 it('uses configured server deadline and reports actual byte progress without reading the entire file into JS',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({upload_timeout_seconds:900})});vi.stubGlobal('fetch',fetch);
  const {uploadArtifact}=await import('./ingestionService');const input=file();const onProgress=vi.fn();const promise=uploadArtifact(input,'mp4',{onProgress});
  await vi.waitFor(()=>expect(TestXHR.instances).toHaveLength(1));const xhr=TestXHR.instances[0];
  expect(fetch.mock.calls[0][0]).toBe('/api/ingestion/capabilities');expect(xhr.timeout).toBe(900000);
  expect(xhr.url).toBe('/api/uploads?kind=mp4');expect(xhr.method).toBe('POST');expect(xhr.body).toBe(input);expect(xhr.headers['Content-Type']).toBe('application/octet-stream');
  xhr.upload.onprogress?.({loaded:2,total:3,lengthComputable:true});expect(onProgress).toHaveBeenCalledWith(2,3);
  xhr.onload?.();await expect(promise).resolves.toEqual(xhr.response);
 });
 it('cancels an in-flight upload and a cancelled signal never sends bytes',async()=>{
  const {uploadArtifact}=await import('./ingestionService');const controller=new AbortController();
  const promise=uploadArtifact(file(),'mp4',{timeoutSeconds:900,signal:controller.signal});const rejection=expect(promise).rejects.toThrow('上传已取消');controller.abort();await rejection;
  await expect(uploadArtifact(file(),'mp4',{timeoutSeconds:900,signal:controller.signal})).rejects.toThrow('上传已取消');expect(TestXHR.instances).toHaveLength(1);
 });
 it('surfaces timeout, connection failure and structured server errors without treating any as completion',async()=>{
  const {uploadArtifact}=await import('./ingestionService');
  for(const [event,message] of [['ontimeout','上传超时'],['onerror','连接中断']] as const){const promise=uploadArtifact(file(),'mp4',{timeoutSeconds:2});const reject=expect(promise).rejects.toThrow(message);TestXHR.instances.at(-1)![event]?.();await reject;}
  const promise=uploadArtifact(file(),'mp4',{timeoutSeconds:2});const reject=expect(promise).rejects.toThrow('超过配置上限');const xhr=TestXHR.instances.at(-1)!;xhr.status=413;xhr.response={detail:{message:'超过配置上限'}};xhr.onload?.();await reject;
 });
 it('scopes durable job cancellation and retry independently from aborting network upload',async()=>{
  const fetch=vi.fn().mockResolvedValue({ok:true,json:async()=>({id:'job',cancel_requested:true})});vi.stubGlobal('fetch',fetch);
  const {cancelImport,retryImport}=await import('./ingestionService');await cancelImport('anonymous_observation','job');await retryImport('anonymous_observation','job');
  expect(fetch.mock.calls.map(c=>[c[0],c[1].method])).toEqual([['/api/scenes/anonymous_observation/imports/job/cancel','POST'],['/api/scenes/anonymous_observation/imports/job/retry','POST']]);
 });
});
describe('capture review inputs',()=>{
 it('preserves unknown capture without inventing identifiers or dates',()=>expect(captureMetadata('null','[]')).toEqual({acquisition:null,view_intervals:[]}));
 it('requires actual string session identity and zoned time before confirmed capture',()=>{
  const valid={capture_session_id:'session_1',captured_at:'2026-10-28T08:00:00+08:00',review_status:'confirmed'};
  expect(captureMetadata(JSON.stringify(valid),'[]').acquisition).toEqual(valid);
  for(const patch of [{capture_session_id:123},{capture_session_id:null},{captured_at:null},{captured_at:'2026-10-28T08:00:00'},{camera_id:'guessed'}])expect(()=>captureMetadata(JSON.stringify({...valid,...patch}),'[]')).toThrow();
 });
 it('rejects missing reviewers and overlapping intervals while allowing unreviewed coverage',()=>{
  const interval={domain_index:0,start_ms:0,end_ms:1000,status:'usable',reason:'固定画面',reviewer:'human_1'};
  expect(captureMetadata('null',JSON.stringify([interval,{...interval,start_ms:1000,end_ms:2000,status:'unreviewed',reviewer:null}])).view_intervals).toHaveLength(2);
  for(const intervals of [[{...interval,reviewer:null}],[{...interval,reviewer:123}],[interval,{...interval,start_ms:500,end_ms:2000}],[{...interval,end_ms:0}]])expect(()=>captureMetadata('null',JSON.stringify(intervals))).toThrow();
 });
});
