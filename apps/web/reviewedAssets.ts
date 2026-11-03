import type {Plugin} from 'vite';
import {createHash} from 'node:crypto';
import {lstatSync,readFileSync,realpathSync,readdirSync,writeFileSync} from 'node:fs';
import {isAbsolute,relative,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const repo=fileURLToPath(new URL('../../',import.meta.url));
const web=resolve(repo,'apps/web');
const manifest=JSON.parse(readFileSync(resolve(web,'browser-files.json'),'utf8')) as {files:Record<string,string>;assets:Record<string,string>};
const digest=(data:Buffer|string)=>createHash('sha256').update(data).digest('hex');
const within=(root:string,file:string)=>{const p=relative(root,file);return !isAbsolute(p)&&p!=='..'&&!p.startsWith('../')&&!p.startsWith('..\\');};
const deps=realpathSync(resolve(web,'node_modules'));
function clean(id:string){return id.split('?')[0].split('#')[0];}
function inspect(file:string){
 const path=resolve(file),stat=lstatSync(path);
 if(!stat.isFile()||stat.isSymbolicLink()||stat.nlink!==1)throw new Error('Unreviewed link or non-regular browser input: '+relative(repo,path));
 const actual=realpathSync(path);
 if(actual!==path)throw new Error('Browser input path alias is not approved');
 const key=relative(repo,path).replaceAll('\\','/');
 if(Object.hasOwn(manifest.files,key)){
  if(digest(readFileSync(path))!==manifest.files[key])throw new Error('Reviewed browser input changed; explicit manifest review required');
  return;
 }
 // Installed, lockfile-pinned executable dependencies are not a public asset root.
 if(within(deps,actual)&&/\.(?:[cm]?js|json|map|css|wasm)$/.test(actual))return;
 throw new Error('File is outside the reviewed browser manifest');
}
function projectInput(id:string){
 const file=clean(id);
 if(isAbsolute(file)&&!id.startsWith('\0')){
  try{lstatSync(file);}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return;throw error;}
  inspect(file);
 }
}
export function reviewedAssets():Plugin{return {
 name:'jianwei-reviewed-browser-inputs',enforce:'pre',
 buildStart(){for(const file of Object.keys(manifest.files))inspect(resolve(repo,file));},
 resolveId(source,importer){
  if(source.startsWith('\0'))return;
  const file=clean(source);
  if(isAbsolute(file)){try{if(lstatSync(file).isDirectory())return;}catch{return;}inspect(file);}
  else if(file.startsWith('.')&&importer&&!importer.startsWith('\0')){
   const candidate=resolve(clean(importer),'..',file);
   try{if(lstatSync(candidate).isDirectory())return;}catch{return;}inspect(candidate);
  }
 },
 load(id){projectInput(id);return null;},
 transformIndexHtml:{order:'pre',handler(html,ctx){inspect(ctx.filename);return html;}},
 configureServer(server){server.middlewares.use((req,res,next)=>{
  let path:string;
  try{path=decodeURIComponent(new URL(req.url??'/','http://localhost').pathname);}catch{res.statusCode=400;res.end('Invalid path');return;}
  if(path.startsWith('/api/')||path.startsWith('/@vite/')||path.startsWith('/@id/')||path==='/@react-refresh')return next();
  const file=path.startsWith('/@fs/')?path.slice(5):resolve(web,'.'+(path==='/'?'/index.html':path));
  try{inspect(isAbsolute(file)?file:'/'+file);next();}catch{res.statusCode=403;res.end('Resource is not in the reviewed browser manifest');}
 });},
 generateBundle(){
  for(const [name,file] of Object.entries(manifest.assets)){
   inspect(resolve(repo,file));this.emitFile({type:'asset',fileName:name,source:readFileSync(resolve(repo,file))});
  }
 },
 writeBundle(options){
  const out=resolve(options.dir??resolve(web,'dist'));
  const members:Record<string,string>={};
  const scan=(dir:string)=>{for(const entry of readdirSync(dir,{withFileTypes:true})){
   const path=resolve(dir,entry.name);if(entry.isDirectory()){scan(path);continue;}
   const name=relative(out,path).replaceAll('\\','/');const stat=lstatSync(path);
   if(stat.isSymbolicLink()||stat.nlink!==1)throw new Error('Forbidden build member link');
   if(name==='build-members.json')continue;
   members[name]=digest(readFileSync(path));
   if(name==='index.html')continue;
   const source=manifest.assets[name];
   if(!source||digest(readFileSync(path))!==manifest.files[source])throw new Error('Unknown build member: '+name);
  }};scan(out);
  writeFileSync(resolve(out,'build-members.json'),JSON.stringify({version:'reviewed-web-build-v1',files:members},null,2)+'\n');
 }
};}
