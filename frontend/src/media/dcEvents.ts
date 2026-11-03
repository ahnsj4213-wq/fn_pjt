/**
 * Data-channel protocol v1, server -> client (TRD 06 §8). Mirrors backend app/voice/dc_protocol.py, the single source.
 * Envelope: {v, type, seq, turn_id, server_now, ...fields}. Times are ISO 8601 UTC with milliseconds.
 */
interface Envelope<T extends string> {
  v: 1;
  type: T;
  seq: number;
  turn_id: string | null;
  server_now: string;
}

export type ServerEvent =
  | (Envelope<'hello'> & { session_id: string; purpose: 'precheck' | 'interview'; resume: { main_no: number; is_followup: boolean } | null })
  | (Envelope<'question'> & {
    main_no: number; main_count: number; is_followup: boolean; area: string; text: string; audio: 'track' | 'none';
    limit_sec: number; think_sec: number; answer_mode: 'SPEECH' | 'CODE';
    presentation_no: number;
  })
  | (Envelope<'thinking'> & { think_until: string; replay_left: number })
  | Envelope<'question_replay'>
  | (Envelope<'interjected'> & { text: string; audio: 'track' | 'none' })
  | (Envelope<'deadline_moved'> & { deadline_at: string; warn_at: string })
  | (Envelope<'tts_end'> & { played: boolean })
  | (Envelope<'answer_opened'> & { opened_at: string; deadline_at: string; warn_at: string; limit_sec: number })
  | (Envelope<'answer_warn'> & { remaining_sec: number })
  | (Envelope<'caption_segment'> & { segment_no: number; text: string })
  | (Envelope<'answer_finalized'> & { reason: 'button' | 'timeout'; text: string; no_answer: boolean })
  | Envelope<'processing'>
  | (Envelope<'interview_completed'> & { next: 'CODING' | 'SUBMITTED' })
  | (Envelope<'status_changed'> & { attempt_status: string; mode: 'VOICE' | 'TEXT' })
  | Envelope<'session_superseded'>
  | (Envelope<'precheck_listen'> & { opened_at: string; deadline_at: string })
  | (Envelope<'precheck_result'> & { ok: boolean; text: string; reason: 'OK' | 'NO_SPEECH' | 'NOT_RECOGNIZED' | 'STT_UNAVAILABLE' })
  | (Envelope<'error'> & { code: string })
  | (Envelope<'pong'> & { client_seq: number; client_now: string | number | null });

export type ServerEventType = ServerEvent['type'];

const KNOWN = new Set<string>([
  'hello', 'question', 'thinking', 'question_replay', 'interjected', 'deadline_moved', 'tts_end', 'answer_opened', 'answer_warn', 'caption_segment', 'answer_finalized', 'processing',
  'interview_completed', 'status_changed', 'session_superseded', 'precheck_listen', 'precheck_result', 'error', 'pong',
]);

/** Parses one data-channel message; anything that is not a known v1 event is ignored (returns null). */
export function parseServerEvent(raw: unknown): ServerEvent | null {
  if (typeof raw !== 'string') return null;
  let msg: unknown;
  try {
    msg = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!msg || typeof msg !== 'object') return null;
  const m = msg as { v?: unknown; type?: unknown; seq?: unknown };
  if (m.v !== 1 || typeof m.type !== 'string' || !KNOWN.has(m.type) || typeof m.seq !== 'number') return null;
  return msg as ServerEvent;
}
