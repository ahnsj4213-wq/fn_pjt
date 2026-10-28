/**
 * Browser side of the voice connection (TRD 06 §2~4, ADR-0009). Non-trickle offer/answer over HTTPS, iceServers is
 * always empty (internal network, AC-22), audio only (the camera stays a local preview, INT-08), one data channel
 * `control` for server events. The server owns the gate: muting the mic here is only a courtesy (AC-09).
 */
import { sendOffer } from '@/api/candidate/interview';
import { createServerClock, localNow, type ServerClock } from '@/shared/time/serverClock';
import { parseServerEvent, type ServerEvent } from './dcEvents';

export type LinkState = 'connecting' | 'connected' | 'lost' | 'closed';

export interface VoiceLinkOptions {
  aid: string;
  purpose: 'PRECHECK' | 'INTERVIEW';
  stream: MediaStream; // must contain one audio track
  onEvent: (ev: ServerEvent) => void;
  onState: (s: LinkState) => void;
  /** Injected in tests. */
  offer?: typeof sendOffer;
  connectTimeoutMs?: number;
}

const ICE_GATHER_CAP_MS = 3_000;
const DISCONNECTED_GRACE_MS = 5_000;
const PING_EVERY_MS = 2_000;

/** Audio constraints for answers (TRD 06 §2): echo cancellation, noise suppression, AGC, mono. */
export const ANSWER_AUDIO: MediaTrackConstraints = {
  echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
};

function waitIceComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      pc.removeEventListener('icegatheringstatechange', check);
      clearTimeout(t);
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === 'complete') done(); };
    const t = setTimeout(done, ICE_GATHER_CAP_MS); // host candidates arrive quickly; never wait forever
    pc.addEventListener('icegatheringstatechange', check);
  });
}

export class VoiceLink {
  readonly clock: ServerClock = createServerClock();
  readonly audio: HTMLAudioElement;
  audioBlocked = false;
  private pc: RTCPeerConnection;
  private dc: RTCDataChannel;
  private state: LinkState = 'connecting';
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lostTimer: ReturnType<typeof setTimeout> | null = null;
  private seq = 0;
  private pending = new Map<number, number>();

  private constructor(private opts: VoiceLinkOptions) {
    this.pc = new RTCPeerConnection({ iceServers: [] });
    this.audio = new Audio();
    this.audio.autoplay = true;
    for (const t of opts.stream.getAudioTracks()) this.pc.addTrack(t, opts.stream);
    this.dc = this.pc.createDataChannel('control', { ordered: true });
    this.dc.onopen = () => this.onOpen();
    this.dc.onmessage = (e) => this.onMessage(e.data);
    this.dc.onclose = () => this.setState(this.state === 'closed' ? 'closed' : 'lost');
    this.pc.ontrack = (e) => {
      this.audio.srcObject = e.streams[0] ?? new MediaStream([e.track]);
      this.audio.play().catch(() => { this.audioBlocked = true; });
    };
    this.pc.onconnectionstatechange = () => this.onPcState();
  }

  /** Connects and resolves once the data channel is open; rejects on signaling errors or timeout. */
  static async open(opts: VoiceLinkOptions): Promise<VoiceLink> {
    const link = new VoiceLink(opts);
    try {
      await link.pc.setLocalDescription(await link.pc.createOffer());
      await waitIceComplete(link.pc);
      const res = await (opts.offer ?? sendOffer)(opts.aid, opts.purpose, link.pc.localDescription!.sdp);
      await link.pc.setRemoteDescription({ type: 'answer', sdp: res.sdp });
      await link.waitOpen(opts.connectTimeoutMs ?? 10_000);
      return link;
    } catch (e) {
      link.close();
      throw e;
    }
  }

  private waitOpen(ms: number): Promise<void> {
    if (this.dc.readyState === 'open') return Promise.resolve();
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('RTC_TIMEOUT')), ms);
      this.dc.addEventListener('open', () => { clearTimeout(t); resolve(); }, { once: true });
    });
  }

  private onOpen(): void {
    this.setState('connected');
    this.send({ type: 'client_ready', audio: this.audioBlocked ? 'blocked' : 'playing' });
    this.pingTimer = setInterval(() => this.ping(), PING_EVERY_MS);
    this.ping();
  }

  private ping(): void {
    this.seq += 1;
    const sentAt = localNow();
    this.pending.set(this.seq, sentAt);
    this.send({ type: 'ping', client_seq: this.seq, client_now: sentAt });
  }

  private onMessage(raw: unknown): void {
    const ev = parseServerEvent(raw);
    if (!ev) return;
    const receivedAt = localNow();
    if (ev.type === 'pong') {
      const sentAt = this.pending.get(ev.client_seq);
      this.pending.delete(ev.client_seq);
      if (sentAt !== undefined) {
        this.clock.addRoundTrip({ serverNowMs: Date.parse(ev.server_now), sentAtMs: sentAt, receivedAtMs: receivedAt });
      }
      return;
    }
    if (!this.clock.hasSample()) this.clock.addOneWay(Date.parse(ev.server_now), receivedAt);
    this.opts.onEvent(ev);
  }

  private onPcState(): void {
    const s = this.pc.connectionState;
    if (s === 'failed' || s === 'closed') this.setState(this.state === 'closed' ? 'closed' : 'lost');
    else if (s === 'disconnected') {
      this.lostTimer ??= setTimeout(() => {
        this.lostTimer = null;
        if (this.pc.connectionState === 'disconnected') this.setState('lost');
      }, DISCONNECTED_GRACE_MS);
    } else if (s === 'connected' && this.lostTimer) {
      clearTimeout(this.lostTimer);
      this.lostTimer = null;
    }
  }

  private setState(s: LinkState): void {
    if (this.state === s || this.state === 'closed') return;
    this.state = s;
    if (s !== 'connected' && this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
    this.opts.onState(s);
  }

  private send(obj: unknown): void {
    if (this.dc.readyState === 'open') this.dc.send(JSON.stringify(obj));
  }

  /** Mic courtesy toggle (the server gate is authoritative). */
  setMic(enabled: boolean): void {
    for (const t of this.opts.stream.getAudioTracks()) t.enabled = enabled;
  }

  /** Retries remote audio playback after a user gesture (autoplay policy). */
  async unblockAudio(): Promise<boolean> {
    try {
      await this.audio.play();
      this.audioBlocked = false;
    } catch {
      this.audioBlocked = true;
    }
    return !this.audioBlocked;
  }

  close(): void {
    this.state = 'closed';
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.lostTimer) clearTimeout(this.lostTimer);
    this.audio.srcObject = null;
    try { this.dc.close(); } catch { /* already closed */ }
    try { this.pc.close(); } catch { /* already closed */ }
  }
}

export function stopStream(stream: MediaStream | null | undefined): void {
  stream?.getTracks().forEach((t) => t.stop());
}
