/**
 * Client interview state machine (TRD 06 §9, C1~C28; screen spec SC-A06) as a pure reducer. The server decides every
 * transition that matters (mic gate, think time, deadlines, interjection, finalisation); the client only mirrors
 * data-channel events and its own link state. Events for a turn other than the current one are dropped (TRD 06 §8),
 * so a late event can never touch a new turn.
 *
 * One question: PRESENTING (the interviewer reads) -> THINKING (countdown, mic closed) -> ANSWERING -> CLOSING ->
 * WAITING (analysis: "답변 잘 들었습니다.") -> the next question or a follow-up.
 */
import type { ServerEvent } from '@/media/dcEvents';

export type Phase =
  | 'BOOT' | 'PREPARING' | 'RESUME_GATE' | 'CONNECTING' | 'WAITING' | 'PRESENTING' | 'THINKING' | 'ANSWERING'
  | 'CLOSING' | 'RECONNECTING' | 'TEXT' | 'SUPERSEDED' | 'MEDIA_ERROR' | 'COMPLETED' | 'ERROR';

export type AnswerMode = 'SPEECH' | 'CODE';

export interface Turn {
  id: string;
  mainNo: number;
  mainCount: number;
  isFollowup: boolean;
  area: string;
  text: string;
  limitSec: number;
  thinkSec: number;
  answerMode: AnswerMode;
  presentationNo: number;
  audio: 'track' | 'none';
}

export interface Final {
  turnId: string;
  text: string;
  noAnswer: boolean;
  reason: 'button' | 'timeout';
}

export interface InterviewUi {
  phase: Phase;
  turn: Turn | null;
  ttsPlayed: boolean | null;
  thinkUntil: number | null; // server epoch ms
  replayLeft: number;
  replaying: boolean; // [질문 다시 듣기]: the question is being read again inside the think time
  interjecting: boolean; // the interviewer is cutting in (the answer window waits for the new deadline)
  interjectText: string | null;
  interjections: number;
  openedAt: number | null;
  deadlineAt: number | null;
  warned: boolean;
  captions: string[];
  last: Final | null;
  followups: number; // follow-up questions asked in this attempt (seen by this client)
  mainCount: number | null;
  next: 'CODING' | 'SUBMITTED' | null;
  error: string | null;
  attempts: number; // reconnect attempts in a row
}

export const initialUi: InterviewUi = {
  phase: 'BOOT', turn: null, ttsPlayed: null, thinkUntil: null, replayLeft: 0, replaying: false, interjecting: false,
  interjectText: null, interjections: 0, openedAt: null, deadlineAt: null, warned: false, captions: [], last: null, followups: 0,
  mainCount: null, next: null, error: null, attempts: 0,
};

export type Action =
  | { type: 'event'; ev: ServerEvent }
  | { type: 'preparing' } // questions are still being composed
  | { type: 'gate' } // needs a click to (re)connect (C2/C4)
  | { type: 'connecting' }
  | { type: 'connected'; followups?: number; mainCount?: number | null }
  | { type: 'lost' } // C21
  | { type: 'reconnect_failed' }
  | { type: 'text_mode' }
  | { type: 'done_clicked' } // C15
  | { type: 'media_error'; message: string }
  | { type: 'error'; message: string };

const ms = (iso: string) => Date.parse(iso);

export function reduce(s: InterviewUi, a: Action): InterviewUi {
  switch (a.type) {
    case 'preparing':
      return { ...s, phase: 'PREPARING' };
    case 'gate':
      return { ...s, phase: 'RESUME_GATE' };
    case 'connecting':
      return { ...s, phase: 'CONNECTING', error: null };
    case 'connected':
      return { ...s, phase: s.phase === 'CONNECTING' ? 'WAITING' : s.phase, attempts: 0,
        followups: a.followups ?? s.followups, mainCount: a.mainCount ?? s.mainCount };
    case 'lost':
      if (['COMPLETED', 'SUPERSEDED', 'TEXT', 'ERROR'].includes(s.phase)) return s;
      // The turn in progress will be presented again from the start (OQ-21): forget its partial captions.
      return { ...s, phase: 'RECONNECTING', turn: null, captions: [], openedAt: null, deadlineAt: null,
        thinkUntil: null, replaying: false, interjecting: false, attempts: s.attempts + 1 };
    case 'reconnect_failed':
      return { ...s, phase: 'RESUME_GATE' };
    case 'text_mode':
      return { ...s, phase: 'TEXT', turn: null, captions: [] };
    case 'done_clicked':
      return s.phase === 'ANSWERING' ? { ...s, phase: 'CLOSING' } : s;
    case 'media_error':
      return { ...s, phase: 'MEDIA_ERROR', error: a.message };
    case 'error':
      return { ...s, phase: 'ERROR', error: a.message };
    case 'event':
      return onEvent(s, a.ev);
  }
}

function onEvent(s: InterviewUi, ev: ServerEvent): InterviewUi {
  if (ev.type === 'session_superseded') return { ...s, phase: 'SUPERSEDED' };
  if (ev.type === 'interview_completed') return { ...s, phase: 'COMPLETED', next: ev.next };
  if (ev.type === 'error') {
    return ev.code === 'TEXT_MODE_REQUIRED' ? { ...s, phase: 'RECONNECTING', attempts: 99 } : s;
  }
  if (ev.type === 'question') {
    const again = s.turn?.id === ev.turn_id; // the same turn presented again after a reconnect
    return {
      ...s, phase: 'PRESENTING', warned: false, captions: [], openedAt: null, deadlineAt: null, ttsPlayed: null,
      thinkUntil: null, replayLeft: 0, replaying: false, interjecting: false, mainCount: ev.main_count,
      followups: s.followups + (ev.is_followup && !again ? 1 : 0),
      turn: { id: ev.turn_id!, mainNo: ev.main_no, mainCount: ev.main_count, isFollowup: ev.is_followup, area: ev.area,
        text: ev.text, limitSec: ev.limit_sec, thinkSec: ev.think_sec, answerMode: ev.answer_mode,
        presentationNo: ev.presentation_no, audio: ev.audio },
    };
  }
  if (!s.turn || ev.turn_id !== s.turn.id) return s; // stale or unrelated event
  switch (ev.type) {
    case 'tts_end':
      return { ...s, ttsPlayed: ev.played };
    case 'thinking':
      return { ...s, phase: 'THINKING', thinkUntil: ms(ev.think_until), replayLeft: ev.replay_left, replaying: false };
    case 'question_replay':
      return { ...s, replaying: true, replayLeft: 0 };
    case 'answer_opened':
      return { ...s, phase: 'ANSWERING', thinkUntil: null, openedAt: ms(ev.opened_at), deadlineAt: ms(ev.deadline_at) };
    case 'interjected':
      return { ...s, interjecting: true, interjectText: ev.text, interjections: s.interjections + 1 };
    case 'deadline_moved':
      return { ...s, interjecting: false, warned: false, deadlineAt: ms(ev.deadline_at) };
    case 'answer_warn':
      return { ...s, warned: true };
    case 'caption_segment':
      return s.phase === 'ANSWERING' || s.phase === 'CLOSING' ? { ...s, captions: [...s.captions, ev.text] } : s;
    case 'answer_finalized':
      return { ...s, phase: 'WAITING', openedAt: null, deadlineAt: null, interjecting: false,
        last: { turnId: ev.turn_id!, text: ev.text, noAnswer: ev.no_answer, reason: ev.reason } };
    default:
      return s;
  }
}

/** The mic is on only while an answer is open or closing (C11, C16: kept until answer_finalized). */
export function micOn(s: InterviewUi): boolean {
  return s.phase === 'ANSWERING' || s.phase === 'CLOSING';
}

/**
 * Overall progress 0..1 like the wireframe: inside one main question 질문 10% · 생각 15% · 답변 55% · 분석 5%,
 * and a follow-up uses the last 15%. `fraction` is how far the current step is (0..1).
 */
export function progressOf(s: InterviewUi, fraction: number): number {
  if (!s.turn || !s.mainCount) return 0;
  const step = ({ PRESENTING: [0, 0.1], THINKING: [0.1, 0.25], ANSWERING: [0.25, 0.8], CLOSING: [0.8, 0.85],
    WAITING: [0.8, 0.85] } as Record<string, [number, number]>)[s.phase] ?? [0, 0];
  let inQ = step[0] + (step[1] - step[0]) * Math.min(1, Math.max(0, fraction));
  if (s.turn.isFollowup) inQ = 0.85 + 0.15 * (inQ / 0.85);
  return Math.min(1, (s.turn.mainNo - 1 + inQ) / s.mainCount);
}
