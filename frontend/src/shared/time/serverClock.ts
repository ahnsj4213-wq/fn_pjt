/**
 * Server-time estimate for countdowns (INT-06 answer window, COD-05 coding limit).
 * The server owns every deadline; the client only displays remaining time, so its own wall clock
 * is never trusted (design D 3.7). Offsets come from round-trip samples (data-channel ping/pong)
 * or, until one exists, from a one-way `server_now` in a REST response or event.
 */
export interface RoundTripSample {
  /** Server epoch ms reported in the reply. */
  serverNowMs: number;
  /** Local epoch ms when the request left. */
  sentAtMs: number;
  /** Local epoch ms when the reply arrived. */
  receivedAtMs: number;
}

export interface ServerClock {
  addRoundTrip(sample: RoundTripSample): number;
  addOneWay(serverNowMs: number, receivedAtMs?: number): number;
  offsetMs(): number;
  hasSample(): boolean;
  now(): number;
  remainingMs(deadlineMs: number): number;
  reset(): void;
}

const WINDOW = 8;

export function localNow(): number {
  return performance.timeOrigin + performance.now();
}

export function createServerClock(clock: () => number = localNow): ServerClock {
  let samples: { offset: number; rtt: number }[] = [];
  let oneWayOffset: number | null = null;

  const best = () => samples.reduce<{ offset: number; rtt: number } | null>(
    (acc, s) => (acc === null || s.rtt < acc.rtt ? s : acc), null);

  return {
    addRoundTrip({ serverNowMs, sentAtMs, receivedAtMs }) {
      const rtt = Math.max(0, receivedAtMs - sentAtMs);
      // The server stamped its time roughly half-way through the round trip.
      const offset = serverNowMs + rtt / 2 - receivedAtMs;
      samples = [...samples, { offset, rtt }].slice(-WINDOW);
      return this.offsetMs();
    },
    addOneWay(serverNowMs, receivedAtMs = clock()) {
      oneWayOffset = serverNowMs - receivedAtMs;
      return this.offsetMs();
    },
    offsetMs() {
      // The lowest-RTT sample has the smallest error bound (RTT / 2).
      return best()?.offset ?? oneWayOffset ?? 0;
    },
    hasSample() {
      return samples.length > 0 || oneWayOffset !== null;
    },
    now() {
      return clock() + this.offsetMs();
    },
    remainingMs(deadlineMs) {
      return Math.max(0, deadlineMs - this.now());
    },
    reset() {
      samples = [];
      oneWayOffset = null;
    },
  };
}

/** Whole seconds to show; rounds up so "0" appears only when the deadline has passed. */
export function displaySeconds(remainingMs: number): number {
  return Math.ceil(Math.max(0, remainingMs) / 1000);
}
