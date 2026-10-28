import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// E2E (design D 3.13). Browser binaries are installed separately and carried into the offline network beforehand
// (AC-22); `npm install` does not download them. PW_CHROMIUM_PATH points at a pre-installed Chromium if needed.
// The microphone plays e2e/.fixtures/voice.wav (written by global-setup) so the server VAD hears "speech".
const voiceWav = path.resolve(import.meta.dirname, 'e2e/.fixtures/voice.wav');
const fakeMedia = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
  `--use-file-for-fake-audio-capture=${voiceWav}`, '--autoplay-policy=no-user-gesture-required'];
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;
// Backend with fake AI and runner (backend/scripts/e2e_server.py). E2E_PYTHON = the backend venv's python.
const python = process.env.E2E_PYTHON ?? 'python';

export default defineConfig({
  testDir: 'e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  use: {
    baseURL: 'http://localhost:5173',
    testIdAttribute: 'data-testid',
    trace: 'retain-on-failure',
  },
  webServer: [
    { command: `${python} ../backend/scripts/e2e_server.py --port 8000`, url: 'http://127.0.0.1:8000/api/job-families',
      reuseExistingServer: !process.env.CI, timeout: 120_000, stdout: 'ignore' },
    { command: 'npm run dev -- --strictPort', url: 'http://localhost:5173', reuseExistingServer: !process.env.CI },
  ],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], launchOptions: { args: fakeMedia, executablePath } } },
    { name: 'msedge', use: { ...devices['Desktop Edge'], channel: 'msedge', launchOptions: { args: fakeMedia } } },
  ],
});
