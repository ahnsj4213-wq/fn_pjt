/**
 * Candidate API (tag "candidate"). Hand-written from backend app/{consent,resumes,applications}/router.py,
 * which are the source of truth (TRD 04 3.3, 6). Must never import from api/recruiter (SEC-04).
 * TODO(OpenAPI): generate from openapi.candidate.json once responses are typed models, not dict.
 */
import type { ResumeContent } from '@/api/common/resume';
import { apiFetch } from '@/shared/http/client';
import { uploadFile } from '@/shared/http/upload';

const enc = encodeURIComponent;

// ---- consent (SEC-01, SEC-02) ----
/** One row of the SC-A05 consent table: what is collected, why, and for how long. */
export interface ConsentItem {
  code: string;
  label: string;
  purpose?: string;
  retention?: string;
  required: boolean;
}

export interface ConsentDocument {
  version: string;
  is_draft: boolean;
  body_md: string;
  items: ConsentItem[];
}

export interface ConsentRecordOut {
  consent_id: string;
  all_required_agreed: boolean;
  version: string;
  agreed_at: string;
}

export const fetchConsent = () => apiFetch<ConsentDocument>('/api/consent/current');

export const recordConsent = (postingId: string, body: { version: string; answers: Record<string, boolean> }) =>
  apiFetch<ConsentRecordOut>(`/api/candidate/postings/${enc(postingId)}/consents`, { method: 'POST', body });

// ---- resume (RES-01~07) ----
export type ExtractionStatus = 'PENDING' | 'DONE' | 'NO_TEXT' | 'FAILED';

export interface ResumeFileOut {
  file_id: string;
  page_count: number | null;
  text_extractable: boolean;
  extraction_status: ExtractionStatus;
  fields: ResumeContent | null;
  /** RES-04: TEXT = text layer, OCR = scanned pages or a photo were read by OCR. */
  method: 'TEXT' | 'OCR' | null;
  ocr_confidence: number | null;
  /** Form fields fed by unsure OCR lines: 'education.0', 'career.1', 'projects.0', 'skills', 'name'. */
  low_conf_fields: string[];
  manual_input_required: boolean;
}

export interface RegisteredResume {
  content: ResumeContent;
  source: 'PDF' | 'MANUAL';
  file_id: string | null;
  updated_at: string;
}

export const fetchRegisteredResume = () => apiFetch<{ resume: RegisteredResume | null }>('/api/candidate/resume');

export const saveRegisteredResume = (body: { content: ResumeContent; file_id?: string | null }) =>
  apiFetch<{ ok: true }>('/api/candidate/resume', { method: 'PUT', body });

export const uploadResumeFile = (file: File, onProgress?: (f: number) => void, signal?: AbortSignal) =>
  uploadFile<ResumeFileOut>('/api/candidate/resume-files', file, onProgress, signal);

export const fetchResumeFile = (fileId: string) => apiFetch<ResumeFileOut>(`/api/candidate/resume-files/${enc(fileId)}`);

// ---- applications (JOB-02, RES-07, 6.6) ----
export interface ApplyIn {
  consent_id: string;
  resume: ResumeContent;
  resume_file_id?: string | null;
  update_registered: boolean;
  /** SC-A05: unchecked = practice mode, the company never sees the result. */
  share_result: boolean;
}

export const applyToPosting = (postingId: string, body: ApplyIn) =>
  apiFetch<{ application_id: string }>(`/api/candidate/postings/${enc(postingId)}/applications`, { method: 'POST', body });

export type AttemptStatus = 'READY' | 'PREPARING' | 'INTERVIEWING' | 'CODING' | 'INTERRUPTED' | 'SUBMITTED' | 'EXPIRED';
export type Decision = 'UNDECIDED' | 'PASS' | 'FAIL';

/** Allow-listed candidate view: no scores, weights, quotes or notes (SEC-04). */
export interface CandidateApplication {
  id: string;
  posting: { id: string; title: string; company_name: string; coding_enabled: boolean };
  applied_at: string;
  attempt_status: AttemptStatus;
  status_label: string;
  evaluation_label: string | null;
  decision: Decision;
  answered_main_count: number;
  can_start: boolean;
  main_count: number | null;
  share_result: boolean;
  /** SC-A09 진행 상태 and its pill colour. */
  progress_label: string;
  progress_kind: 'good' | 'bad' | 'hold' | 'none';
}

export interface CandidateFeedback {
  criteria: { expertise: string; logic: string; attitude: string; delivery: string };
  strengths: string[];
  improvements: string[];
  coding: 'ALL_PASSED' | 'NOT_ALL_PASSED' | null;
  notice: string;
}

/** feedback is null until the recruiter decides (X-04, 6.6). There is no total score field. */
export interface CandidateResult {
  application_id: string;
  decision: Decision;
  status_label: string;
  feedback: CandidateFeedback | null;
}

export const fetchMyApplications = () => apiFetch<CandidateApplication[]>('/api/candidate/applications');
export const fetchMyApplication = (id: string) => apiFetch<CandidateApplication>(`/api/candidate/applications/${enc(id)}`);
export const fetchMyResult = (id: string) => apiFetch<CandidateResult>(`/api/candidate/applications/${enc(id)}/result`);

/** SC-A11 프로필. The email (id) is read-only. */
export interface Profile { name: string; email: string; birth_date: string | null; gender: 'M' | 'F' | null; phone: string | null }
export const fetchProfile = () => apiFetch<Profile>('/api/candidate/profile');
export const saveProfile = (body: Omit<Profile, 'email'>) => apiFetch<Profile>('/api/candidate/profile', { method: 'PUT', body });
export const sendPasswordLink = () => apiFetch<{ email: string }>('/api/candidate/profile/password-link', { method: 'POST' });

/** SC-A15 면접 일정: dates a company sent by mail, with the candidate's answer. */
export interface Schedule {
  id: string; application_id: string; company_name: string; title: string; starts_at: string; place: string | null;
  status: 'PROPOSED' | 'ACCEPTED' | 'RESCHEDULE_REQUESTED';
}
export const fetchSchedules = () => apiFetch<Schedule[]>('/api/candidate/schedules');

/** USR-07: password re-check; 409 ACTIVE_ATTEMPT while an attempt is in progress (OQ-12). */
export const withdrawAccount = (password: string) =>
  apiFetch<{ status: 'WITHDRAWN' }>('/api/candidate/me', { method: 'DELETE', body: { password } });

// ---- SC-R15 counterpart: ask the company about one's own application ----
export interface InquiryMessage { id: string; mine: boolean; sender: string; body: string; created_at: string }
export interface Inquiry { id: string; subject: string; messages: InquiryMessage[] }
export const fetchMyInquiry = (applicationId: string) => apiFetch<Inquiry | null>(`/api/candidate/applications/${encodeURIComponent(applicationId)}/inquiry`);
export const askInquiry = (applicationId: string, body: { subject: string; body: string }) =>
  apiFetch<Inquiry>(`/api/candidate/applications/${encodeURIComponent(applicationId)}/inquiry`, { method: 'POST', body });
