import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const API_TARGET = 'http://127.0.0.1:8000';
const API_PROXY_PREFIXES = [
  '/reports',
  '/backtests',
  '/health',
  '/version',
  '/runtime',
  '/datasources',
  '/skill-packs',
  '/factors',
  '/local-data',
  '/strategies',
  '/tickers',
  '/watchlist',
  '/graph',
  '/memory'
];

const proxy = Object.fromEntries(API_PROXY_PREFIXES.map((prefix) => [prefix, API_TARGET]));

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy
  }
});
