/**
 * Interview, precheck and coding commands (TRD 04 §5.1~5.3, TRD 06 §7). Hand-written from backend
 * app/interview/router.py, app/voice/router.py and app/coding/router.py (the source of truth).
 * Commands go over HTTPS with the session cookie + CSRF; server events arrive on the data channel (dcEvents.ts).
 */
import { apiFetch } from '@/shared/http/client';
import type { AttemptStatus } from './index';

const enc = encodeURIComponent;
const base = (aid: string) => `/api/candidate/applications/${enc(aid)}`;

export interface InterviewGuide {
  main_count: number;
  main_answer_sec: number;
  followup_answer_sec: number;
  followup_max_total: number;
  warning_sec: number;
  coding: { enabled: boolean; time_limit_min: number | null; language: string | null };
}

export type TurnPhase = 'PRESENTING' | 'ANSWERING' | 'FINALIZING';

export interface TurnView {
  turn_id: string;
  main_no: number;
  is_followup: boolean;
  presentation_no: number;
  phase: TurnPhase;
  question_text: string;
  limit_sec: number;
  opened_at: string | null;
  deadline_at: string | null;
}

/** TRD 04 §5.2: the current turn only, never the next question, sources or follow-up reasons. */
export interface InterviewState {
  attempt_status: AttemptStatus;
  interrupted_from: AttemptStatus | null;
  mode: 'VOICE' | 'TEXT';
  server_now: string;
  total_main: number;
  answered_main: number;
  next_after_interview: 'CODING' | 'DONE';
  warnings: string[];
  turn: TurnView | null;
  slot_grace_until: string | null;
  coding_deadline_at: string | null;
}

export interface AiHealth {
  llm: boolean;
  stt: boolean;
  tts: boolean;
  can_start: boolean;
}

export const fetchGuide = (aid: string) => apiFetch<InterviewGuide>(`${base(aid)}/interview/guide`);
export const prepareInterview = (aid: string) =>
  apiFetch<{ attempt_status: AttemptStatus; questions_ready: boolean }>(`${base(aid)}/interview/prepare`, { method: 'POST' });
export const leavePrepare = (aid: string) => apiFetch<{ attempt_status: AttemptStatus }>(`${base(aid)}/interview/leave-prepare`, { method: 'POST' });
export const fetchInterviewState = (aid: string) => apiFetch<InterviewState>(`${base(aid)}/interview/state`);
export const startInterview = (aid: string) => apiFetch<InterviewState>(`${base(aid)}/interview/start`, { method: 'POST' });
export const resumeInterview = (aid: string) => apiFetch<InterviewState>(`${base(aid)}/interview/resume`, { method: 'POST' });
export const enterTextMode = (aid: string) => apiFetch<InterviewState>(`${base(aid)}/interview/text-mode`, { method: 'POST' });
export const nextTextQuestion = (aid: string) => apiFetch<InterviewState>(`${base(aid)}/interview/next`, { method: 'POST' });
export const finishTurn = (aid: string, turnId: string) =>
  apiFetch<{ turn_id: string; state: string }>(`${base(aid)}/interview/turns/${enc(turnId)}/done`, { method: 'POST' });
export const sendTextAnswer = (aid: string, turnId: string, text: string) =>
  apiFetch<{ turn_id: string; state: string }>(`${base(aid)}/interview/turns/${enc(turnId)}/text-answer`,
    { method: 'POST', body: { text } });
export const heartbeat = (aid: string) => apiFetch<void>(`${base(aid)}/heartbeat`, { method: 'POST' });
export const reportProctor = (aid: string, kind: 'LEAVE' | 'RETURN', awayMs?: number) =>
  apiFetch<void>(`${base(aid)}/interview/proctor-events`, { method: 'POST', body: { kind, away_ms: awayMs ?? null } });
export const fetchAiHealth = () => apiFetch<AiHealth>('/api/system/ai-health');

// ---- precheck (INT-01, INT-17) ----
export const reportMedia = (aid: string, body: { mic_ok: boolean; camera_ok: boolean; speaker_ok?: boolean | null }) =>
  apiFetch<void>(`${base(aid)}/precheck/media`, { method: 'POST', body });
export const requestUtterance = (aid: string) =>
  apiFetch<{ window_sec: number }>(`${base(aid)}/precheck/utterance`, { method: 'POST' });

// ---- signaling (IV-3) ----
export const sendOffer = (aid: string, purpose: 'PRECHECK' | 'INTERVIEW', sdp: string) =>
  apiFetch<{ sdp: string; type: 'answer'; rtc_session_id: string }>(`${base(aid)}/rtc/offer`,
    { method: 'POST', body: { purpose, sdp, type: 'offer' }, timeoutMs: 20_000 });

// ---- coding (TRD 04 §5.3) ----
export interface CodingView {
  problem: { title: string; statement_md: string; examples: { input: string; output: string }[] };
  language: string;
  languages: string[];
  deadline_at: string;
  server_now: string;
  draft: { code: string; revision: number; saved_at: string | null };
  run_available: boolean;
}

/** [실행] result: normal / abnormal and what the program printed — never whether it was correct (AC-15). */
export interface RunResult {
  run_id: string;
  verdict: 'NORMAL' | 'ABNORMAL' | 'ENV_ERROR';
  outcome: string | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
  duration_ms: number | null;
  examples: { input: string; stdout: string; stderr: string; outcome: string }[];
}

export const fetchCoding = (aid: string) => apiFetch<CodingView>(`${base(aid)}/coding`);
export const saveDraft = (aid: string, code: string, revision: number) =>
  apiFetch<{ revision: number; saved_at: string; applied: boolean }>(`${base(aid)}/coding/draft`,
    { method: 'PUT', body: { code, revision } });
export const runCode = (aid: string, code: string) =>
  apiFetch<RunResult>(`${base(aid)}/coding/runs`, { method: 'POST', body: { code }, timeoutMs: 30_000 });
export const submitCode = (aid: string, code: string, revision: number) =>
  apiFetch<{ attempt_status: AttemptStatus }>(`${base(aid)}/coding/submit`,
    { method: 'POST', body: { code, revision, confirmed: true } });
