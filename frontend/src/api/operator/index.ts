/** Operator API (backend app/operator/router.py): company verification, SC-O01. */
import { apiFetch } from '@/shared/http/client';

export interface VerificationRow {
  company_id: string;
  company_name: string;
  contact_name: string | null;
  email: string | null;
  biz_no: string | null;
  method: 'DOMAIN_MAIL' | 'DOCUMENT' | 'ISSUED';
  status: 'PENDING' | 'APPROVED' | 'REJECTED';
  evidence_name: string | null;
  reject_reason: string | null;
  applied_at: string;
}

export interface VerificationList {
  counts: { pending: number; auto: number; approved: number; rejected: number };
  items: VerificationRow[];
}

const co = (id: string) => `/api/operator/companies/${encodeURIComponent(id)}`;
export const fetchVerifications = () => apiFetch<VerificationList>('/api/operator/companies');
export const evidenceUrl = (id: string) => `${co(id)}/evidence`;
export const approveCompany = (id: string) => apiFetch<{ status: string }>(`${co(id)}/approve`, { method: 'POST' });
export const rejectCompany = (id: string, reason: string) =>
  apiFetch<{ status: string }>(`${co(id)}/reject`, { method: 'POST', body: { reason } });

// ---- SC-O02 공고 관리 ----
export interface OperatorPosting {
  id: string;
  title: string | null;
  company_id: string;
  company_name: string;
  job_family: string | null;
  status: 'OPEN' | 'CLOSED';
  hidden: boolean;
  company_suspended: boolean;
}

const post = (path: string) => apiFetch<unknown>(path, { method: 'POST' });
export const fetchOperatorPostings = () => apiFetch<OperatorPosting[]>('/api/operator/postings');
export const hidePosting = (id: string) => post(`/api/operator/postings/${encodeURIComponent(id)}/hide`);
export const showPosting = (id: string) => post(`/api/operator/postings/${encodeURIComponent(id)}/show`);
export const suspendCompany = (id: string) => post(`${co(id)}/suspend`);
export const unsuspendCompany = (id: string) => post(`${co(id)}/unsuspend`);
