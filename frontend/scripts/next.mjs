// Runs the Next.js CLI with telemetry off (AC-22: no call outside the internal network), on every OS.
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const bin = require.resolve('next/dist/bin/next');
const child = spawn(process.execPath, [bin, ...process.argv.slice(2)], {
  stdio: 'inherit',
  env: { ...process.env, NEXT_TELEMETRY_DISABLED: '1' },
});
child.on('exit', (code, signal) => process.exit(signal ? 1 : (code ?? 1)));
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => child.kill(sig));
