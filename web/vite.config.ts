import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const agentSrc = path.resolve(__dirname, '../../surf-kit/packages/agent/src')
const coreSrc = path.resolve(__dirname, '../../surf-kit/packages/core/src')
const themeSrc = path.resolve(__dirname, '../../surf-kit/packages/theme/src')

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

      // Map @surf-kit/theme to source for dev HMR
      '@surf-kit/theme': path.join(themeSrc, 'index.ts'),
    },
  },
  server: {
    port: 3020,
    strictPort: true,
    host: host || false,
    open: !process.env.TAURI_ENV_PLATFORM,
    proxy: {
      '/api': { target: process.env.API_PROXY_TARGET || 'http://localhost:8090', changeOrigin: true },
    },
  },
  optimizeDeps: {
    // Tauri plugin-store uses IPC internals that fail when pre-bundled by Vite
    // in a browser context. Exclude it so the dynamic import in tauriTokenCache
    // only resolves at runtime inside the Tauri WebView.
    exclude: ['@tauri-apps/plugin-store'],
  },
  build: { outDir: 'dist', sourcemap: process.env.NODE_ENV !== 'production', target: 'es2022' },
})
