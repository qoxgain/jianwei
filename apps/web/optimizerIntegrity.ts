import type {Plugin as BuildPlugin} from 'rolldown';
import type {IncomingMessage,ServerResponse} from 'node:http';
import {createHash} from 'node:crypto';
import {lstatSync,readFileSync,realpathSync} from 'node:fs';
import {relative,resolve,isAbsolute} from 'node:path';

export const reviewedImports=['ajv/dist/2020','js-sha256','lucide-react','react','react-dom','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime','yaml'];
const sha=(bytes:Buffer)=>createHash('sha256').update(bytes).digest('hex');

/** Attest actual bundler outputs in memory BEFORE they reach mutable disk. */
export function optimizerIntegrity(cache:string){
 const approved=new Map<string,{bytes:Buffer;sha256:string}>();
 const plugin:BuildPlugin={name:'jianwei-attest-optimizer-output',generateBundle(_options,bundle){
  approved.clear();
  for(const file of Object.values(bundle)){
   const bytes=Buffer.from(file.type==='chunk'?file.code:file.source);
   approved.set(file.fileName,{bytes,sha256:sha(bytes)});
   if(file.type==='chunk'&&file.map){
    const map=Buffer.from(file.map.toString());
    approved.set(file.fileName+'.map',{bytes:map,sha256:sha(map)});
   }
  }
 }};
 function member(path:string){const name=relative(cache,resolve(path)).replaceAll('\\','/');return !isAbsolute(name)&&name!=='..'&&!name.startsWith('../')?name:null;}
 function checked(path:string){
  const name=member(path),value=name===null?undefined:approved.get(name);
  if(!value)throw new Error('Not an attested optimizer output');
  const stat=lstatSync(path);
  if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1||realpathSync(path)!==path||sha(readFileSync(path))!==value.sha256)
   throw new Error('Optimizer output differs from trusted in-memory build');
  return value.bytes;
 }
 function serve(req:IncomingMessage,res:ServerResponse,path:string){
  try{
   const query=new URL(req.url??'/','http://localhost').searchParams;
   if(!['GET','HEAD'].includes(req.method??'')||req.headers.range||[...query].some(([key,value])=>
    !((key==='v'&&/^[a-f0-9]{8,64}$/i.test(value))||(key==='t'&&/^\d+$/.test(value))||(key==='import'&&value===''))))
    throw new Error('Only native module and debugging-map requests are supported');
   const bytes=checked(path);res.statusCode=200;
   res.setHeader('Content-Type',path.endsWith('.map')?'application/json':'text/javascript');
   const name=member(path)!;
   if(!name.endsWith('.map')&&approved.has(name+'.map'))res.setHeader('SourceMap',encodeURIComponent(name.split('/').at(-1)!+'.map'));
   res.setHeader('Cache-Control','no-store');res.setHeader('Content-Length',bytes.length);
   // Do not validate disk and then delegate a second disk read (TOCTOU).
   res.end(req.method==='HEAD'?undefined:bytes);
  }catch{res.statusCode=403;res.end('Unverified optimizer output or unsupported access form');}
 }
 return {plugin,checked,serve,contains:(path:string)=>member(path)!==null};
}
