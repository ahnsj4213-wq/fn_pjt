/**
 * Client interview state machine (TRD 06 §9, C1~C28) as a pure reducer. The server decides every transition that
 * matters (mic gate, deadlines, finalisation); the client only mirrors data-channel events and its own link state.
 * Events for a turn other than the current one are dropped (TRD 06 §8), so a late event can never touch a new turn.
 */
import type { ServerEvent } from '@/media/dcEvents';

export type Phase =
  | 'BOOT' | 'RESUME_GATE' | 'CONNECTING' | 'WAITING' | 'PRESENTING' | 'ANSWERING' | 'CLOSING'
  | 'RECONNECTING' | 'TEXT' | 'SUPERSEDED' | 'MEDIA_ERROR' | 'COMPLETED' | 'ERROR';

export interface Turn {
  id: string;
  mainNo: number;
  isFollowup: boolean;
  text: string;
  limitSec: number;
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
  openedAt: number | null; // server epoch ms
  deadlineAt: number | null;
  warned: boolean;
  captions: string[];
  last: Final | null; // the previous answer's confirmed text (INT-15)
  next: 'CODING' | 'SUBMITTED' | null;
  error: string | null;
  attempts: number; // reconnect attempts in a row
}

export const initialUi: InterviewUi = {
  phase: 'BOOT', turn: null, ttsPlayed: null, openedAt: null, deadlineAt: null, warned: false, captions: [],
  last: null, next: null, error: null, attempts: 0,
};

export type Action =
  | { type: 'event'; ev: ServerEvent }
  | { type: 'gate' } // needs a click to (re)connect (C2/C4)
  | { type: 'connecting' }
  | { type: 'connected' }
  | { type: 'lost' } // C21
  | { type: 'reconnect_failed' }
  | { type: 'text_mode' }
  | { type: 'done_clicked' } // C15
  | { type: 'media_error'; message: string }
  | { type: 'error'; message: string };

const ms = (iso: string) => Date.parse(iso);

export function reduce(s: InterviewUi, a: Action): InterviewUi {
  switch (a.type) {
    case 'gate':
      return { ...s, phase: 'RESUME_GATE' };
    case 'connecting':
      return { ...s, phase: 'CONNECTING', error: null };
    case 'connected':
      return { ...s, phase: s.phase === 'CONNECTING' ? 'WAITING' : s.phase, attempts: 0 };
    case 'lost':
      if (['COMPLETED', 'SUPERSEDED', 'TEXT', 'ERROR'].includes(s.phase)) return s;
      // The turn in progress will be presented again from the start (OQ-21): forget its partial captions.
      return { ...s, phase: 'RECONNECTING', turn: null, captions: [], openedAt: null, deadlineAt: null,
        attempts: s.attempts + 1 };
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
    return {
      ...s, phase: 'PRESENTING', warned: false, captions: [], openedAt: null, deadlineAt: null, ttsPlayed: null,
      turn: { id: ev.turn_id!, mainNo: ev.main_no, isFollowup: ev.is_followup, text: ev.text, limitSec: ev.limit_sec,
        presentationNo: ev.presentation_no, audio: ev.audio },
    };
  }
  if (!s.turn || ev.turn_id !== s.turn.id) return s; // stale or unrelated event
  switch (ev.type) {
    case 'tts_end':
      return { ...s, ttsPlayed: ev.played };
    case 'answer_opened':
      return { ...s, phase: 'ANSWERING', openedAt: ms(ev.opened_at), deadlineAt: ms(ev.deadline_at) };
    case 'answer_warn':
      return { ...s, warned: true };
    case 'caption_segment':
      return s.phase === 'ANSWERING' || s.phase === 'CLOSING' ? { ...s, captions: [...s.captions, ev.text] } : s;
    case 'answer_finalized':
      return { ...s, phase: 'WAITING', openedAt: null, deadlineAt: null,
        last: { turnId: ev.turn_id!, text: ev.text, noAnswer: ev.no_answer, reason: ev.reason } };
    default:
      return s;
  }
}

/** The mic is on only while an answer is open or closing (C11, C16: kept until answer_finalized). */
export function micOn(s: InterviewUi): boolean {
  return s.phase === 'ANSWERING' || s.phase === 'CLOSING';
}
