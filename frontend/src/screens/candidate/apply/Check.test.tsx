import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { candidateSession, renderRoutes } from '@/test/render';
import { Check } from './Check';

const server = setupServer(http.get('*/api/postings/p1', () => HttpResponse.json({ id: 'p1', title: '백엔드', company_name: '회사' })));
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); vi.unstubAllGlobals(); });
afterAll(() => server.close());

const fakeTrack = (kind: string) => ({ kind, stop: () => undefined, getSettings: () => ({ deviceId: `${kind}-1` }) });
const fail = (name: string) => Object.assign(new Error(name), { name });

function stubMedia(getUserMedia: (c: MediaStreamConstraints) => Promise<unknown>) {
  class FakeStream { constructor(public t: unknown[] = []) {}
    getTracks() { return this.t; } getVideoTracks() { return this.t.filter((x) => (x as { kind: string }).kind === 'video'); }
    getAudioTracks() { return this.t.filter((x) => (x as { kind: string }).kind === 'audio'); } }
  vi.stubGlobal('MediaStream', FakeStream);
  vi.stubGlobal('AudioContext', class {
    createAnalyser() { return { fftSize: 0, getFloatTimeDomainData: () => undefined }; }
    createMediaStreamSource() { return { connect: () => undefined }; }
    close() { return Promise.resolve(); }
  });
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
    getUserMedia, enumerateDevices: async () => [{ kind: 'audioinput', deviceId: 'audio-1', label: '이어폰 마이크' }],
    addEventListener: () => undefined, removeEventListener: () => undefined } });
  return FakeStream;
}

describe('환경 점검 (SC-A04) device problems', () => {
  beforeEach(() => { sessionStorage.clear(); });

  it('names the camera as the problem and keeps the earphone microphone working when there is no webcam', async () => {
    const Stream = stubMedia(async (c) => {
      if (c.video && c.audio) throw fail('NotFoundError');
      if (c.video) throw fail('NotFoundError');
      return new Stream([fakeTrack('audio')]);
    });
    renderRoutes([{ path: '/postings/:pid/apply', element: <Check /> }], { path: '/postings/p1/apply', session: candidateSession });
    expect(await screen.findByText(/카메라를 찾지 못했습니다/)).toBeTruthy();
    expect(screen.queryByText(/마이크를 찾지 못했습니다/)).toBeNull();
    await waitFor(() => expect((screen.getByLabelText('마이크 장치') as HTMLSelectElement).textContent).toContain('이어폰 마이크'));
    expect((screen.getByRole('button', { name: '마이크 테스트' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('tells a blocked permission from a missing microphone', async () => {
    stubMedia(async () => { throw fail('NotAllowedError'); });
    renderRoutes([{ path: '/postings/:pid/apply', element: <Check /> }], { path: '/postings/p1/apply', session: candidateSession });
    expect((await screen.findAllByText(/사용이 차단되어 있습니다/)).length).toBeGreaterThan(0);
    expect((screen.getByRole('button', { name: '마이크 테스트' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
