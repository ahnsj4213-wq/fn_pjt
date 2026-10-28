import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

/**
 * Writes the fake microphone input: 3 s of a voiced-like signal (a 180 Hz tone with a 4 Hz syllable envelope),
 * 48 kHz mono 16-bit PCM. Chrome loops it. The E2E backend's fake STT turns any audio into a fixed sentence,
 * so this only has to be loud enough for the energy VAD.
 */
export default function globalSetup(): void {
  const rate = 48_000;
  const n = rate * 3;
  const data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) {
    const t = i / rate;
    const env = 0.55 + 0.45 * Math.sin(2 * Math.PI * 4 * t);
    const v = env * (0.5 * Math.sin(2 * Math.PI * 180 * t) + 0.2 * Math.sin(2 * Math.PI * 360 * t));
    data.writeInt16LE(Math.round(v * 0.6 * 32767), i * 2);
  }
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(1, 22); // mono
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  const dir = path.resolve(import.meta.dirname, '.fixtures');
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, 'voice.wav'), Buffer.concat([header, data]));
}
