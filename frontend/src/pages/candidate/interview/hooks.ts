import { useEffect, useState } from 'react';
import { heartbeat } from '@/api/candidate/interview';

const HEARTBEAT_MS = 30_000;

/** Liveness while preparing, in text mode and while coding (C7). Failures are ignored; the next beat retries. */
export function useHeartbeat(aid: string, enabled = true): void {
  useEffect(() => {
    if (!enabled) return;
    const beat = () => { heartbeat(aid).catch(() => undefined); };
    beat();
    const t = setInterval(beat, HEARTBEAT_MS);
    return () => clearInterval(t);
  }, [aid, enabled]);
}

/** 0..1 microphone level for the precheck meter (INT-01). Local only; nothing is sent. */
export function useMicLevel(stream: MediaStream | null): number {
  const [level, setLevel] = useState(0);
  useEffect(() => {
    if (!stream || stream.getAudioTracks().length === 0) return;
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    let raf = 0;
    const tick = () => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 8));
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => {
      cancelAnimationFrame(raf);
      void ctx.close();
    };
  }, [stream]);
  return level;
}

/** Ticks every 250 ms while `active`, so countdowns re-render from the server clock. */
export function useTicker(active: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setN((x) => x + 1), 250);
    return () => clearInterval(t);
  }, [active]);
  return n;
}
