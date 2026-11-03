import { describe, expect, it } from 'vitest';
import type { ServerEvent } from '@/media/dcEvents';
import { initialUi, micOn, progressOf, reduce, type InterviewUi } from './machine';

const env = { v: 1 as const, seq: 1, server_now: '2026-10-28T00:00:00.000Z' };
const q = (turn: string, extra: Partial<Extract<ServerEvent, { type: 'question' }>> = {}): ServerEvent =>
  ({ ...env, type: 'question', turn_id: turn, main_no: 3, main_count: 5, is_followup: false, area: 'JOB_SKILL', text: '질문',
    audio: 'track', limit_sec: 60, think_sec: 10, answer_mode: 'SPEECH', presentation_no: 1,
    ...extra });
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

  it('think time, one replay, then the answer opens', () => {
    let s = run({ type: 'event', ev: q('t4') }, ev({ type: 'tts_end', turn_id: 't4', played: true }));
    s = reduce(s, ev({ type: 'thinking', turn_id: 't4', think_until: '2026-10-28T00:00:10.000Z', replay_left: 1 }));
    expect(s).toMatchObject({ phase: 'THINKING', replayLeft: 1, replaying: false });
    expect(micOn(s)).toBe(false);
    s = reduce(s, ev({ type: 'question_replay', turn_id: 't4' }));
    expect(s).toMatchObject({ phase: 'THINKING', replaying: true, replayLeft: 0 });
    s = reduce(s, ev({ type: 'thinking', turn_id: 't4', think_until: '2026-10-28T00:00:12.000Z', replay_left: 0 }));
    expect(s).toMatchObject({ replaying: false, thinkUntil: Date.parse('2026-10-28T00:00:12.000Z') });
    s = reduce(s, ev({ type: 'answer_opened', turn_id: 't4', opened_at: env.server_now, deadline_at: env.server_now,
      warn_at: env.server_now, limit_sec: 60 }));
    expect(s).toMatchObject({ phase: 'ANSWERING', thinkUntil: null });
  });

  it('interjection moves the deadline and is counted; follow-ups are counted once per turn', () => {
    let s = run({ type: 'event', ev: q('t5') }, ev({ type: 'answer_opened', turn_id: 't5', opened_at: env.server_now,
      deadline_at: '2026-10-28T00:01:00.000Z', warn_at: env.server_now, limit_sec: 60 }));
    s = reduce(s, ev({ type: 'interjected', turn_id: 't5', text: '핵심만', audio: 'track' }));
    expect(s).toMatchObject({ interjecting: true, interjections: 1 });
    s = reduce(s, ev({ type: 'deadline_moved', turn_id: 't5', deadline_at: '2026-10-28T00:00:40.000Z', warn_at: env.server_now }));
    expect(s).toMatchObject({ interjecting: false, deadlineAt: Date.parse('2026-10-28T00:00:40.000Z') });
    s = reduce(s, { type: 'event', ev: q('t6', { is_followup: true }) });
    s = reduce(s, { type: 'event', ev: q('t6', { is_followup: true, presentation_no: 2 }) }); // presented again
    expect(s.followups).toBe(1);
  });

  it('progress follows the wireframe split', () => {
    const s = run({ type: 'event', ev: q('t7', { main_no: 2 }) });
    expect(progressOf(s, 0)).toBeCloseTo(0.2);
    expect(progressOf({ ...s, phase: 'ANSWERING' }, 1)).toBeCloseTo((1 + 0.8) / 5);
  });
});
