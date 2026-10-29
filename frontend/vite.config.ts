import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Used by Vitest only (unit tests). The app itself is built and served by Next.js (next.config.ts).
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': '/src' },
  },
});
