/**
 * Recruiter API (tag "recruiter", company-scoped). Hand-written from backend app/postings/router.py and
 * app/recruiter/router.py (source of truth, TRD 04 4.1~4.3). Loaded only inside lazy recruiter chunks.
 * TODO(OpenAPI): generate from openapi.recruiter.json once responses are typed models, not dict.
 */
import type { PostingPublic } from '@/api/common/postings';
import type { ResumeContent } from '@/api/common/resume';
import { apiFetch } from '@/shared/http/client';

const enc = encodeURIComponent;

// ---- postings & evaluation settings (JOB-03~07, ADM-05) ----
export type PostingStatus = 'DRAFT' | 'OPEN' | 'CLOSED';

export interface Weights {
  expertise: number;
  logic: number;
  specificity: number;
  delivery: number;
  coding?: number | null;
}

export interface EvalSettings {
  version: number;
  weights: Weights;
  custom_question: string | null;
  rubric_version: string;
  locked: boolean;
  coding_enabled: boolean;
}

export interface PostingSummary {
  id: string;
  title: string | null;
  job_family: string | null;
  status: PostingStatus;
  effective_status: PostingStatus;
  deadline_date: string | null;
  headcount: number | null;
  coding_enabled: boolean;
  applicant_count: number;
  completed_count: number;
  updated_at: string;
}

export interface PostingDetail extends PostingSummary {
  requirements: string | null;
  preferred: string | null;
  duties: string | null;
  coding_problem_id: string | null;
  coding_language: string | null;
  coding_time_limit_min: number | null;
  settings_locked: boolean;
  row_version: number;
  evaluation_settings: EvalSettings;
  weights_reset: boolean;
}

export interface PostingIn {
  title?: string | null;
  job_family?: string | null;
  requirements?: string | null;
  preferred?: string | null;
  duties?: string | null;
  headcount?: number | null;
  deadline_date?: string | null;
  coding_enabled: boolean;
  coding_problem_id?: string | null;
  coding_time_limit_min?: number | null;
  row_version?: number;
}

export interface CodingProblem {
  id: string;
  title: string;
  statement_md: string;
  language: string;
  public_examples: { input: string; output: string }[];
}

export const fetchDashboard = () => apiFetch<{ open_postings: number; awaiting_review: number }>('/api/recruiter/dashboard');
export const fetchRecruiterPostings = (status?: PostingStatus) =>
  apiFetch<PostingSummary[]>(`/api/recruiter/postings${status ? `?status=${status}` : ''}`);
export const fetchRecruiterPosting = (id: string) => apiFetch<PostingDetail>(`/api/recruiter/postings/${enc(id)}`);
export const createPosting = (body: PostingIn) => apiFetch<PostingDetail>('/api/recruiter/postings', { method: 'POST', body });
export const updatePosting = (id: string, body: PostingIn) =>
  apiFetch<PostingDetail>(`/api/recruiter/postings/${enc(id)}`, { method: 'PUT', body });
export const publishPosting = (id: string) => apiFetch<PostingDetail>(`/api/recruiter/postings/${enc(id)}/publish`, { method: 'POST' });
export const closePosting = (id: string) => apiFetch<PostingDetail>(`/api/recruiter/postings/${enc(id)}/close`, { method: 'POST' });
export const deletePosting = (id: string) => apiFetch<void>(`/api/recruiter/postings/${enc(id)}`, { method: 'DELETE' });
export const fetchPostingPreview = (id: string) => apiFetch<PostingPublic>(`/api/recruiter/postings/${enc(id)}/preview`);
export const fetchEvalSettings = (id: string) => apiFetch<EvalSettings>(`/api/recruiter/postings/${enc(id)}/evaluation-settings`);
export const saveEvalSettings = (id: string, body: { weights: Weights; custom_question: string | null }) =>
  apiFetch<EvalSettings>(`/api/recruiter/postings/${enc(id)}/evaluation-settings`, { method: 'PUT', body });
export const resetEvalSettings = (id: string) =>
  apiFetch<EvalSettings>(`/api/recruiter/postings/${enc(id)}/evaluation-settings/reset`, { method: 'POST' });
export const fetchCodingProblems = () => apiFetch<CodingProblem[]>('/api/recruiter/coding-problems');

// ---- applications (ADM-02~08, RES-08, RPT-02) ----
export type EvalStatus = 'PENDING' | 'RUNNING' | 'DONE' | 'ON_HOLD';
export type Decision = 'UNDECIDED' | 'PASS' | 'FAIL';

export interface ApplicationRow {
  id: string;
  candidate_name: string;
  posting: { id: string; title: string | null };
  applied_at: string;
  attempt_status: string;
  answered_main_count: number;
  eval_status: EvalStatus | null;
  total_score: string | null;
  decision: Decision;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

export interface ApplicationFilters {
  posting_id?: string;
  decision?: string;
  eval_status?: string;
  page?: number;
  size?: number;
}

export interface ApplicationDetail {
  id: string;
  candidate: { name: string; email: string; phone: string };
  posting: { id: string; title: string | null };
  attempt_status: string;
  interrupted_from: string | null;
  eval_status: EvalStatus | null;
  decision: Decision;
  decision_version: number;
  answered_main_count: number;
  text_mode_used: boolean;
  can_decide: boolean;
  can_re_evaluate: boolean;
  applied_at: string;
  submitted_at: string | null;
}

export type CriterionKey = 'EXPERTISE' | 'LOGIC' | 'SPECIFICITY' | 'DELIVERY';

export interface ReportItem {
  key: CriterionKey;
  score: number;
  evidence: 'OK' | 'INSUFFICIENT' | 'NO_ANSWER';
  insufficient_reason?: string | null;
  /** start/end index display_text; text is the original substring (ADR-0008). */
  quote: { text: string; start: number | null; end: number | null } | null;
  comment: string | null;
}

export interface ReportAnswer {
  role: 'MAIN' | 'FOLLOWUP';
  input_mode: 'VOICE' | 'TEXT';
  end_reason: string;
  no_answer: boolean;
  stt_failed: boolean;
  display_text: string | null;
  transcript: string | null;
  items: ReportItem[];
}

export interface ReportQuestion {
  seq: number;
  slot_type: string;
  question_text: string;
  reasked: number;
  question_scores: Record<string, string>;
  followup: { asked: boolean; skip_cause: string | null; question_text?: string };
  answers: ReportAnswer[];
}

export interface RecruiterReport {
  application_id: string;
  total_score: string;
  criteria: { key: CriterionKey; score: string; weight: number; insufficient: number }[];
  coding: { score: string; weight: number | null } | null;
  insufficient_count: number;
  no_answer_count: number;
  answered_main_count: number;
  summary: string;
  strengths: string[];
  improvements: string[];
  questions: ReportQuestion[];
  code_review: string | null;
  proctor: { leave_count: number; away_ms: number };
  versions: { model: string; model_digest: string | null; prompt: string; rubric: string; scoring: string; weights_version: number };
}

export interface CodingResult {
  enabled: boolean;
  submission?: {
    kind: string;
    submitted_at: string;
    code: string;
    compile_ok: boolean | null;
    grading_status: string;
    passed: number | null;
    total: number | null;
    score: string | null;
  } | null;
  tests?: { ordinal: number; visibility: 'PUBLIC' | 'HIDDEN'; passed: boolean; outcome: string; time_ms: number | null; input: string; expected_output: string }[];
  code_review?: string | null;
}

export interface DecisionHistoryRow {
  from: Decision;
  to: Decision;
  decided_by: { id: string; name: string };
  decided_at: string;
}

export interface Note {
  id: string;
  author: { id: string; name: string };
  body: string;
  created_at: string;
  updated_at: string | null;
}

export interface Stats {
  monthly_applicants: { month: string; count: number }[];
  avg_score_by_job_family: { job_family: string; avg: string; n: number }[];
  pass_rate_by_posting: { posting_id: string; title: string | null; decided: number; passed: number }[];
  pass_rate_by_job_family: { job_family: string; decided: number; passed: number }[];
}

export function fetchApplications(f: ApplicationFilters): Promise<Page<ApplicationRow>> {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== '') sp.set(k, String(v));
  const qs = sp.toString();
  return apiFetch<Page<ApplicationRow>>(`/api/recruiter/applications${qs ? `?${qs}` : ''}`);
}

const app = (id: string) => `/api/recruiter/applications/${enc(id)}`;
export const fetchApplication = (id: string) => apiFetch<ApplicationDetail>(app(id));
export const fetchApplicationResume = (id: string) => apiFetch<{ snapshot: ResumeContent; has_file: boolean }>(`${app(id)}/resume`);
export const resumeFileUrl = (id: string) => `${app(id)}/resume-file`;
export const fetchReport = (id: string) => apiFetch<RecruiterReport>(`${app(id)}/report`);
export const fetchCodingResult = (id: string) => apiFetch<CodingResult>(`${app(id)}/coding-result`);
export const saveDecision = (id: string, body: { decision: 'PASS' | 'FAIL'; expected_version: number }) =>
  apiFetch<{ decision: Decision; version: number; decided_at: string }>(`${app(id)}/decision`, { method: 'POST', body });
export const fetchDecisionHistory = (id: string) => apiFetch<DecisionHistoryRow[]>(`${app(id)}/decision-history`);
export const reEvaluate = (id: string) => apiFetch<{ eval_status: 'PENDING' }>(`${app(id)}/re-evaluate`, { method: 'POST' });
export const fetchNotes = (id: string) => apiFetch<Note[]>(`${app(id)}/notes`);
export const createNote = (id: string, body: string) => apiFetch<Note>(`${app(id)}/notes`, { method: 'POST', body: { body } });
export const updateNote = (noteId: string, body: string) =>
  apiFetch<Note>(`/api/recruiter/notes/${enc(noteId)}`, { method: 'PATCH', body: { body } });
export const deleteNote = (noteId: string) => apiFetch<void>(`/api/recruiter/notes/${enc(noteId)}`, { method: 'DELETE' });

export function fetchStats(from?: string, to?: string): Promise<Stats> {
  const sp = new URLSearchParams();
  if (from) sp.set('from', from);
  if (to) sp.set('to', to);
  const qs = sp.toString();
  return apiFetch<Stats>(`/api/recruiter/stats${qs ? `?${qs}` : ''}`);
}
