import { describe, expect, it } from 'vitest';
import type { ServerEvent } from '@/media/dcEvents';
import { initialUi, micOn, reduce, type InterviewUi } from './machine';

const env = { v: 1 as const, seq: 1, server_now: '2026-10-28T00:00:00.000Z' };
const q = (turn: string, extra: Partial<Extract<ServerEvent, { type: 'question' }>> = {}): ServerEvent =>
  ({ ...env, type: 'question', turn_id: turn, main_no: 3, is_followup: false, text: '질문', audio: 'track', limit_sec: 60,
    presentation_no: 1, ...extra });
const ev = (e: Record<string, unknown>) => ({ type: 'event' as const, ev: { ...env, ...e } as ServerEvent });

function run(...actions: Parameters<typeof reduce>[1][]): InterviewUi {
  return actions.reduce(reduce, { ...initialUi, phase: 'WAITING' });
}

describe('interview machine', () => {
  it('follows question -> tts_end -> answer_opened -> captions -> finalized', () => {
    let s = run({ type: 'event', ev: q('t1') });
    expect(s.phase).toBe('PRESENTING');
    expect(micOn(s)).toBe(false);
    s = reduce(s, ev({ type: 'tts_end', turn_id: 't1', played: true }));
    s = reduce(s, ev({ type: 'answer_opened', turn_id: 't1', opened_at: '2026-10-28T00:00:01.000Z',
      deadline_at: '2026-10-28T00:01:01.000Z', warn_at: '2026-10-28T00:00:51.000Z', limit_sec: 60 }));
    expect(s.phase).toBe('ANSWERING');
    expect(s.deadlineAt! - s.openedAt!).toBe(60_000);
    expect(micOn(s)).toBe(true);
    s = reduce(s, ev({ type: 'caption_segment', turn_id: 't1', segment_no: 1, text: '첫 구간' }));
    s = reduce(s, { type: 'done_clicked' });
    expect(s.phase).toBe('CLOSING');
    expect(micOn(s)).toBe(true); // kept until answer_finalized (C16)
    s = reduce(s, ev({ type: 'caption_segment', turn_id: 't1', segment_no: 2, text: '마지막 말' }));
    expect(s.captions).toEqual(['첫 구간', '마지막 말']);
    s = reduce(s, ev({ type: 'answer_finalized', turn_id: 't1', reason: 'button', text: '첫 구간 마지막 말', no_answer: false }));
    expect(s.phase).toBe('WAITING');
    expect(s.last).toEqual({ turnId: 't1', text: '첫 구간 마지막 말', noAnswer: false, reason: 'button' });
    expect(micOn(s)).toBe(false);
  });

  it('drops events for another turn', () => {
    let s = run({ type: 'event', ev: q('t2') });
    s = reduce(s, ev({ type: 'answer_opened', turn_id: 'old', opened_at: env.server_now, deadline_at: env.server_now,
      warn_at: env.server_now, limit_sec: 60 }));
    expect(s.phase).toBe('PRESENTING');
    s = reduce(s, ev({ type: 'answer_finalized', turn_id: 'old', reason: 'button', text: 'x', no_answer: false }));
    expect(s.last).toBeNull();
  });

  it('a lost link forgets the partial answer and counts attempts', () => {
    let s = run({ type: 'event', ev: q('t3') }, ev({ type: 'answer_opened', turn_id: 't3', opened_at: env.server_now,
      deadline_at: env.server_now, warn_at: env.server_now, limit_sec: 60 }), ev({ type: 'caption_segment', turn_id: 't3',
      segment_no: 1, text: '중간' }));
    s = reduce(s, { type: 'lost' });
    expect(s).toMatchObject({ phase: 'RECONNECTING', turn: null, captions: [], attempts: 1 });
    s = reduce(reduce(s, { type: 'connecting' }), { type: 'connected' });
    expect(s).toMatchObject({ phase: 'WAITING', attempts: 0 });
  });

  it('completion, supersede and text-mode requests', () => {
    expect(run(ev({ type: 'interview_completed', turn_id: null, next: 'CODING' })))
      .toMatchObject({ phase: 'COMPLETED', next: 'CODING' });
    expect(run(ev({ type: 'session_superseded', turn_id: null })).phase).toBe('SUPERSEDED');
    expect(run(ev({ type: 'error', turn_id: null, code: 'TEXT_MODE_REQUIRED' })).attempts).toBe(99);
    expect(reduce(run(ev({ type: 'session_superseded', turn_id: null })), { type: 'lost' }).phase).toBe('SUPERSEDED');
  });
});
