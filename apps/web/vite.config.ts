import { defineConfig, isFileServingAllowed, type Plugin } from 'vite';
import { fileURLToPath } from 'node:url';
import { realpathSync } from 'node:fs';
import { isAbsolute, relative, resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';
const localPath=(path:string)=>fileURLToPath(new URL(path,import.meta.url));
const allowed=[localPath('./'),localPath('../../knowledge'),localPath('../../schemas'),localPath('../../config/system.yaml'),localPath('../../tests/fixtures/v30/config/system.yaml'),localPath('../../tests/fixtures/v31-parity.json')];

// Enforce before asset/import transforms as well as Vite's raw-file middleware.
// These development routes never provide media; use the gated API for playback.
function restrictedFiles():Plugin{return {name:'jianwei-explicit-development-files',configureServer(server){
 server.middlewares.use((req,res,next)=>{
  let path:string;
  try{path=decodeURIComponent(new URL(req.url??'/', 'http://localhost').pathname);}catch{res.statusCode=400;res.end('Invalid path');return;}
  if(path.startsWith('/api/'))return next();
  const raw=path.startsWith('/@fs/')?path.slice(5):null;
  const files=raw!==null?[(isAbsolute(raw)?raw:'/'+raw)]:[resolve(server.config.root,'.'+path),...(server.config.publicDir?[resolve(server.config.publicDir,'.'+path)]:[])];
  for(const file of files)try{
   const actual=realpathSync(file);
   const inside=allowed.some(root=>{let real:string;try{real=realpathSync(root);}catch{return false;}const rel=relative(real,actual);return !rel||(!isAbsolute(rel)&&rel!=='..'&&!rel.startsWith('../')&&!rel.startsWith('..\\'));});
   const media=/\.(mp4|mov|avi|mkv|webm|pdf|npy|npz|onnx|pth|pt|db|sqlite)(-(wal|shm|journal))?$/i.test(actual);
   if(!inside||media||(raw!==null&&!isFileServingAllowed(server.config,path))){res.statusCode=403;res.end('Use the authorized API for this resource');return;}
  }catch(error){if((error as NodeJS.ErrnoException).code!=='ENOENT'){res.statusCode=403;res.end('Resource unavailable');return;}}
  next();
 });
}};}

export default defineConfig(({ mode }) => ({
  plugins: [restrictedFiles(),react(), viteSingleFile()],
  base: './',
  define: { __DEMO_BUILD__: JSON.stringify(mode === 'demo') },
  server: { host:'127.0.0.1',port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' }, fs: { strict:true,allow:allowed } },
  build: { outDir: mode === 'demo' ? 'dist-demo' : 'dist', target: 'es2022' },
}));
