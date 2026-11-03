import {defineConfig} from 'vite';
import {fileURLToPath} from 'node:url';
import react from '@vitejs/plugin-react';
import {viteSingleFile} from 'vite-plugin-singlefile';
import {reviewedAssets} from './reviewedAssets';
const local=(path:string)=>fileURLToPath(new URL(path,import.meta.url));
export default defineConfig(({mode})=>({
 plugins:[reviewedAssets(),react(),viteSingleFile()],base:'./',publicDir:false,
 define:{__DEMO_BUILD__:JSON.stringify(mode==='demo')},
 server:{host:'127.0.0.1',port:5173,proxy:{'/api':'http://127.0.0.1:8000'},
  fs:{strict:true,allow:[local('./'),local('../../knowledge'),local('../../schemas'),local('../../config/system.yaml'),local('../../tests/fixtures')] }},
 preview:{host:'127.0.0.1'},
 build:{outDir:mode==='demo'?'dist-demo':'dist',target:'es2022'},
}));
