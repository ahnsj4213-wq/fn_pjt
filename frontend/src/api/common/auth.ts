/** Auth API (backend app/auth/router.py). Shared by both roles. */
import { ApiError, apiFetch, setCsrfToken } from '@/shared/http/client';

export type Role = 'CANDIDATE' | 'RECRUITER';

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

/** Exactly these five fields: no birth date or gender (USR-02, AC-01; backend extra="forbid"). */
export interface SignupIn {
  name: string;
  email: string;
  password: string;
  password_confirm: string;
  phone: string;
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
