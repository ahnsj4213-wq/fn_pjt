/** Auth API (backend app/auth/router.py). Shared by both roles. */
import { ApiError, apiFetch, setCsrfToken } from '@/shared/http/client';
import { uploadFile } from '@/shared/http/upload';

export type Role = 'CANDIDATE' | 'RECRUITER' | 'OPERATOR';

export interface SessionUser {
  id: string;
  role: Role;
  name: string;
  company_id: string | null;
  company_name: string | null;
}

export interface SessionOut {
  user: SessionUser;
  csrf_token: string;
}

/** SC-A01. Birth date and gender stay on the account (backend never puts them in AI inputs). */
export interface SignupIn {
  name: string;
  birth_date: string;
  email: string;
  password: string;
  password_confirm: string;
  gender: 'M' | 'F';
  phone: string;
  terms_agreed: boolean;
}

export interface LoginIn {
  role: Role;
  email: string;
  password: string;
}

/** Current session or null when not logged in. Also refreshes the in-memory CSRF token. */
export async function fetchSession(): Promise<SessionOut | null> {
  try {
    const s = await apiFetch<SessionOut>('/api/auth/me', { authRedirect: false });
    setCsrfToken(s.csrf_token);
    return s;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      setCsrfToken(null);
      return null;
    }
    throw e;
  }
}

export async function login(body: LoginIn): Promise<SessionOut> {
  const s = await apiFetch<SessionOut>('/api/auth/login', { method: 'POST', body, authRedirect: false });
  setCsrfToken(s.csrf_token);
  return s;
}

export function signup(body: SignupIn): Promise<{ user_id: string }> {
  return apiFetch<{ user_id: string }>('/api/auth/signup', { method: 'POST', body, authRedirect: false });
}

export async function logout(): Promise<void> {
  try {
    await apiFetch<void>('/api/auth/logout', { method: 'POST', authRedirect: false });
  } finally {
    setCsrfToken(null);
  }
}

/** SC-A12: masked addresses such as jiw***@gmail.com. */
export function findId(body: { name: string; phone: string }): Promise<{ emails: string[] }> {
  return apiFetch<{ emails: string[] }>('/api/auth/find-id', { method: 'POST', body, authRedirect: false });
}

/** Same answer whether or not the address exists. */
export function requestPasswordReset(email: string): Promise<{ message: string }> {
  return apiFetch<{ message: string }>('/api/auth/password-reset', { method: 'POST', body: { email }, authRedirect: false });
}

export function confirmPasswordReset(body: { token: string; password: string; password_confirm: string }): Promise<void> {
  return apiFetch<void>('/api/auth/password-reset/confirm', { method: 'POST', body, authRedirect: false });
}

// ---- 기업회원 가입 (SC-R01) ----
export interface CompanySignupIn {
  company_name: string;
  biz_no: string;
  name: string;
  email: string;
  code: string;
  password: string;
  password_confirm: string;
}

export interface CompanyInfo {
  id: string;
  name: string;
  biz_no: string | null;
  verification: 'APPROVED' | 'PENDING' | 'REJECTED';
  evidence_name: string | null;
  reject_reason: string | null;
  suspended: boolean;
}

/** Sends the 6-digit code; `free_mail` tells whether the address is a personal mail service (needs a document). */
export const sendCompanyCode = (email: string) =>
  apiFetch<{ free_mail: boolean; expires_in_sec: number }>('/api/auth/company-signup/code', {
    method: 'POST', body: { email }, authRedirect: false });

export async function companySignup(body: CompanySignupIn): Promise<SessionOut> {
  const s = await apiFetch<SessionOut>('/api/auth/company-signup', { method: 'POST', body, authRedirect: false });
  setCsrfToken(s.csrf_token);
  return s;
}

export const fetchCompany = () => apiFetch<CompanyInfo>('/api/recruiter/company');

export const uploadCompanyEvidence = (file: File) => uploadFile<CompanyInfo>('/api/recruiter/company/evidence', file);
