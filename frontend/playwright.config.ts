import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// E2E (design D 3.13). Browser binaries are installed separately and carried into the offline network beforehand
// (AC-22); `npm install` does not download them. PW_CHROMIUM_PATH points at a pre-installed Chromium if needed.
// The microphone plays e2e/.fixtures/voice.wav (written by global-setup) so the server VAD hears "speech".
const voiceWav = path.resolve(import.meta.dirname, 'e2e/.fixtures/voice.wav');
const fakeMedia = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
  `--use-file-for-fake-audio-capture=${voiceWav}`, '--autoplay-policy=no-user-gesture-required'];
// AC-22 run (tools/offline/run.sh): Chromium records every request, lookup and socket to a net-log.
// Chromium's own vendor services (updates, autofill, variations) are switched off there so the log shows only what
// the platform asks for; the manual rehearsal on the demo machine covers a stock browser.
if (process.env.E2E_NETLOG) {
  fakeMedia.push(`--log-net-log=${process.env.E2E_NETLOG}`, '--net-log-capture-mode=Default',
    '--disable-background-networking', '--disable-component-update', '--disable-domain-reliability', '--no-pings',
    '--disable-sync', '--disable-client-side-phishing-detection', '--disable-default-apps', '--no-first-run',
    '--disable-features=AutofillServerCommunication,OptimizationHints,Translate,MediaRouter,DialMediaRouteProvider');
}
const executablePath = process.env.PW_CHROMIUM_PATH || undefined;
// Backend with fake AI and runner (backend/scripts/e2e_server.py). E2E_PYTHON = the backend venv's python.
const python = process.env.E2E_PYTHON ?? 'python';
// E2E_BASE_URL = an already running stack (e.g. deploy/docker-compose.yml on http://localhost:8080): no local servers.
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';

export default defineConfig({
  testDir: 'e2e',
  testMatch: '*.spec.ts', // e2e/*.test.ts are vitest unit tests of the E2E tooling
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  use: {
    baseURL,
    testIdAttribute: 'data-testid',
    trace: 'retain-on-failure',
  },
  webServer: process.env.E2E_BASE_URL ? undefined : [
    { command: `${python} ../backend/scripts/e2e_server.py --port 8000`, url: 'http://127.0.0.1:8000/api/job-families',
      reuseExistingServer: !process.env.CI, timeout: 120_000, stdout: 'ignore' },
    { command: 'npm run dev -- --strictPort', url: 'http://localhost:5173', reuseExistingServer: !process.env.CI },
  ],
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'], launchOptions: { args: fakeMedia, executablePath } } },
    { name: 'msedge', use: { ...devices['Desktop Edge'], channel: 'msedge', launchOptions: { args: fakeMedia } } },
  ],
});
