import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';

export default defineConfig(({ mode }) => ({
  plugins: [react(), viteSingleFile()],
  base: './',
  define: { __DEMO_BUILD__: JSON.stringify(mode === 'demo') },
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' }, fs: { allow: ['../..'] } },
  build: { outDir: mode === 'demo' ? 'dist-demo' : 'dist', target: 'es2022' },
}));
