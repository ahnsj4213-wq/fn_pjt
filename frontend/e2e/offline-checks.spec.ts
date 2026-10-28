import { expect, test } from '@playwright/test';

/**
 * AC-22 / TRD 07 §12: with iceServers=[] the browser gathers host candidates only, without waiting on a STUN
 * server, and quickly (< 1 s). Runs in every E2E run, offline or not.
 */
test('ICE gathering with no ICE servers is host-only and under a second', async ({ page }) => {
  await page.goto('/login');
  const r = await page.evaluate(async () => {
    const pc = new RTCPeerConnection({ iceServers: [] });
    pc.createDataChannel('control');
    const t0 = performance.now();
    const types: string[] = [];
    pc.onicecandidate = (e) => { if (e.candidate) types.push(e.candidate.type ?? 'unknown'); };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise<void>((resolve) => {
      if (pc.iceGatheringState === 'complete') resolve();
      pc.onicegatheringstatechange = () => { if (pc.iceGatheringState === 'complete') resolve(); };
      setTimeout(resolve, 5_000);
    });
    const ms = performance.now() - t0;
    pc.close();
    return { ms, types };
  });
  expect(r.ms).toBeLessThan(1_000);
  expect(r.types.every((t) => t === 'host')).toBeTruthy();
});
