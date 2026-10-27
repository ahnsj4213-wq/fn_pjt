import { describe, expect, it } from 'vitest';
import { createServerClock, displaySeconds } from './serverClock';

describe('serverClock', () => {
  it('estimates the offset from a round trip, crediting half the RTT', () => {
    let local = 1_000_000;
    const clock = createServerClock(() => local);
    // request left at 1_000_000, server stamped 1_005_100, reply arrived at 1_000_200 (RTT 200)
    expect(clock.addRoundTrip({ sentAtMs: 1_000_000, serverNowMs: 1_005_100, receivedAtMs: 1_000_200 })).toBe(5_000);
    local = 1_000_300;
    expect(clock.now()).toBe(1_005_300);
  });

  it('prefers the lowest-RTT sample', () => {
    const clock = createServerClock(() => 0);
    clock.addRoundTrip({ sentAtMs: 0, serverNowMs: 3_000, receivedAtMs: 2_000 }); // RTT 2000, offset 2000
    clock.addRoundTrip({ sentAtMs: 10_000, serverNowMs: 11_010, receivedAtMs: 10_020 }); // RTT 20, offset 1000
    clock.addRoundTrip({ sentAtMs: 20_000, serverNowMs: 21_600, receivedAtMs: 20_400 }); // RTT 400, offset 1400
    expect(clock.offsetMs()).toBe(1_000);
  });

  it('uses a one-way server_now only until a round-trip sample exists', () => {
    const clock = createServerClock(() => 0);
    expect(clock.hasSample()).toBe(false);
    clock.addOneWay(7_000, 5_000);
    expect(clock.offsetMs()).toBe(2_000);
    clock.addRoundTrip({ sentAtMs: 0, serverNowMs: 510, receivedAtMs: 20 });
    expect(clock.offsetMs()).toBe(500);
  });

  it('counts down to the server deadline and never below zero', () => {
    let local = 10_000;
    const clock = createServerClock(() => local);
    clock.addOneWay(15_000, 10_000); // server is 5 s ahead
    const deadline = 15_000 + 60_000; // INT-06 main question: 60 s from mic open
    expect(clock.remainingMs(deadline)).toBe(60_000);
    local += 50_500;
    expect(displaySeconds(clock.remainingMs(deadline))).toBe(10); // 9.5 s left shows "10"
    local += 20_000;
    expect(clock.remainingMs(deadline)).toBe(0);
  });
});
