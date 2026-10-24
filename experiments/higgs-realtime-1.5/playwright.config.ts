import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/browser', fullyParallel: true, timeout: 20000,
  use: { baseURL: 'http://127.0.0.1:3105', headless: true,
    permissions: ['microphone'], launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'] } },
  webServer: { command: 'npm run dev', url: 'http://127.0.0.1:3105/api/health',
    env: { PORT: '3105', BOSON_API_KEY: '', BOSON_REALTIME_MODEL: 'higgs-realtime-1.5-preview' }, reuseExistingServer: false },
  reporter: 'list',
});
