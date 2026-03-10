import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const agentSrc = path.resolve(__dirname, '../../surf-kit/packages/agent/src')
const coreSrc = path.resolve(__dirname, '../../surf-kit/packages/core/src')

const host = process.env.TAURI_DEV_HOST

export default defineConfig({
  plugins: [
    tailwindcss(),
    react({
      babel: { plugins: ['babel-plugin-react-compiler'] },
    }),
  ],
  resolve: {
    alias: {
      // Deduplicate React — ensure all packages use the same copy
      'react': path.resolve(__dirname, 'node_modules/react'),
      'react-dom': path.resolve(__dirname, 'node_modules/react-dom'),

      // Map @surf-kit/agent imports directly to source during development.
      // No build step needed — Vite compiles the TS on the fly and HMR works.
      '@surf-kit/agent/hooks': path.join(agentSrc, 'hooks.ts'),
      '@surf-kit/agent/chat': path.join(agentSrc, 'chat/index.ts'),
      '@surf-kit/agent/response': path.join(agentSrc, 'response/index.ts'),
      '@surf-kit/agent/streaming': path.join(agentSrc, 'streaming/index.ts'),
      '@surf-kit/agent/sources': path.join(agentSrc, 'sources/index.ts'),
      '@surf-kit/agent/confidence': path.join(agentSrc, 'confidence/index.ts'),
      '@surf-kit/agent/agent-identity': path.join(agentSrc, 'agent-identity/index.ts'),
      '@surf-kit/agent/layouts': path.join(agentSrc, 'layouts/index.ts'),
      '@surf-kit/agent/feedback': path.join(agentSrc, 'feedback/index.ts'),
      '@surf-kit/agent': path.join(agentSrc, 'index.ts'),

      // Map @surf-kit/core to source for dev
      '@surf-kit/core': path.join(coreSrc, 'index.ts'),
    },
  },
  server: {
    port: 3000,
    strictPort: true,
    host: host || false,
    proxy: {
      '/api': { target: 'http://localhost:8090', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true, target: 'es2022' },
  ...(process.env.TAURI_ENV_PLATFORM ? { server: { open: false } } : {}),
})
