/** 결과 리포트 for the applicant (backend app/applications/report.py, screen spec SC-A08). */
import { apiFetch } from '@/shared/http/client';

export interface Highlight { start: number; end: number; kind: 'good' | 'bad'; criterion: string }
export interface Evidence { criterion: string; label: string; kind: 'good' | 'bad'; quote: string; comment: string | null }
export interface ReportAnswer {
  text: string | null;
  seconds: number | null;
  input_mode: string;
  no_answer: boolean;
  highlights: Highlight[];
  negative: string[];
  evidence: Evidence[];
  criteria: { key: string; label: string; score: number }[];
  score: number | null;
  good: string | null;
  fix: string | null;
  code: string | null;
  code_language: string | null;
}
export interface ReportFollowup { label: string; text: string; why: string | null; answer: ReportAnswer }
export interface ReportQuestion {
  no: number;
  label: string;
  type_label: string;
  text: string;
  answer: ReportAnswer | null;
  followup: ReportFollowup | null;
  limit_sec: number | null;
}
export interface ReportPoint { rank: number; text: string; refs: number[] }
export interface SubItem { key: string; name: string; score: number | null }
/** Sub-items, STAR and the values the server measures itself, stored with the report (backend evaluation/details.py). {} for older reports. */
export interface ReportDetail {
  version?: string;
  sub?: { expertise: SubItem[]; logic: SubItem[]; star: { items: { key: string; name: string; rate: number | null }[]; score: number | null } };
  rules?: {
    negative?: { count: number; score: number; per_question: { seq: number; count: number; phrases: string[] }[] };
    answer_time?: { score: number; per_question: { seq: number; sec: number; limit: number; score: number }[] };
    speed?: { avg_cpm: number; ideal: [number, number]; score: number; per_question: { seq: number; cpm: number; score: number }[] };
    clarity?: { score: number };
    pitch?: { score: number; avg_std_semitones: number };
  };
  custom?: { id: string; name: string; description: string; weight: number; grade: '상' | '중' | '하'; score: number;
    evidence_status: string; quote: string | null; quote_no: number | null; comment: string }[];
  blend?: { scoring: string; rule_weight: number; rule_scores: Record<string, number> };
}
export interface MyReport {
  application_id: string;
  posting_id: string;
  posting_title: string;
  company_name: string;
  candidate_name: string;
  interviewed_at: string | null;
  main_count: number;
  followup_count: number;
  avg_answer_sec: number | null;
  total: number;
  grade: string;
  grade_kind: 'good' | 'hold' | 'bad';
  criteria: { key: string; label: string; desc: string; score: number; weight: number; custom?: boolean }[];
  coding: {
    score: number;
    weight: number | null;
    items: { key: string; label: string; score: number; weight: number; comment: string | null; quote: string | null; evidence_status: string }[];
    time: string | null;
    improvements: string[];
  } | null;
  summary: string;
  strengths: ReportPoint[];
  improvements: ReportPoint[];
  chips: { label: string; no: number; score: number | null; followup: boolean }[];
  questions: ReportQuestion[];
  areas: { key: string; label: string; desc: string; score: number; custom?: boolean; items: { label: string; score: number; comment: string | null }[] }[];
  attitude: { negative: string[]; times: { label: string; sec: number; limit: number | null }[]; limit_sec: number | null };
  proctor: { leave_count: number; away_ms: number };
  media: { audio_deleted: boolean; audio_auto_delete_days: number };
  share_result: boolean;
  notice: string;
  detail: ReportDetail;
}

const base = (aid: string) => `/api/candidate/applications/${encodeURIComponent(aid)}`;
export const fetchMyReport = (aid: string) => apiFetch<MyReport>(`${base(aid)}/report`);
export const deleteMyMedia = (aid: string) => apiFetch<void>(`${base(aid)}/media/delete`, { method: 'POST' });

/** SC-R10: the same report for the company of the application, with the AI reference badge (never in the applicant's copy). */
export type RecruiterReportView = MyReport & { ai_reference: 'PASS' | 'HOLD' | 'FAIL' | null };
export const fetchRecruiterReportView = (aid: string) =>
  apiFetch<RecruiterReportView>(`/api/recruiter/applications/${encodeURIComponent(aid)}/report-view`);
