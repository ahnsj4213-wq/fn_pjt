import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Same-origin development: the browser only talks to :5173 and Vite forwards /api to FastAPI.
// No CORS, the session cookie stays SameSite=Strict, and localhost is a secure context for the mic (8.4).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': '/src' },
  },
  server: {
    host: 'localhost',
    port: 5173,
    strictPort: true,
    proxy: {
      // Origin stays http://localhost:5173, which is in the backend's trusted_origins (dev).
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      // TODO(06-webrtc-interview): add the signaling path once the realtime protocol doc fixes it.
    },
  },
  preview: { port: 4173, strictPort: true },
  build: {
    target: 'es2022',
    sourcemap: false,
    rolldownOptions: {
      output: {
        // Recruiter pages get an "rc-" prefix so E2E can assert a candidate session never loads them (D 3.2).
        chunkFileNames: (chunk) =>
          chunk.moduleIds.some((id) => id.replace(/\\/g, '/').includes('/src/pages/recruiter/'))
            ? 'assets/rc-[name]-[hash].js'
            : 'assets/[name]-[hash].js',
      },
    },
  },
});
