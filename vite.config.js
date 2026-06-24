import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const backendTarget = process.env.HERMES_BACKEND_URL || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': backendTarget,
      '/ws': {
        target: backendTarget.replace(/^http/, 'ws'),
        ws: true,
      },
    },
  },
});
