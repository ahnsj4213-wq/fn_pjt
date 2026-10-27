import { defineConfig, devices } from '@playwright/test';

// E2E skeleton (design D 3.13). Browser binaries are installed separately and carried into the offline
// network beforehand (AC-22); `npm install` does not download them. Voice scenarios add
// --use-file-for-fake-audio-capture=<wav> per test file through test.use({ launchOptions }).
const fakeMedia = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'];

export default defineConfig({
  testDir: 'e2e',
  fullyParallel: false,
  use: {
    baseURL: 'http://localhost:5173',
    testIdAttribute: 'data-testid',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], launchOptions: { args: fakeMedia } } },
    { name: 'msedge', use: { ...devices['Desktop Edge'], channel: 'msedge', launchOptions: { args: fakeMedia } } },
  ],
});
