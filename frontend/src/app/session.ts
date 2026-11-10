import { useQuery, type QueryClient } from '@tanstack/react-query';
import { fetchSession, type Role, type SessionOut } from '@/api/common/auth';
import { ApiError } from '@/shared/http/client';

export const SESSION_KEY = ['auth', 'me'] as const;

export function useSession() {
  return useQuery({ queryKey: SESSION_KEY, queryFn: fetchSession, staleTime: Infinity });
}

/**
 * A new session (login, signup, logout) drops every other cached answer: lists fetched before it were answered for
 * someone else (e.g. the main page's postings without `has_applied`), and staleTime would keep showing them.
 */
export function setSession(qc: QueryClient, s: SessionOut | null): void {
  qc.removeQueries({ predicate: (q) => q.queryKey[0] !== SESSION_KEY[0] });
  qc.setQueryData(SESSION_KEY, s);
}

/** Candidates land on the main page (SC-A02), recruiters on the company dashboard (SC-R13). */
export function roleHome(role: Role): string {
  return role === 'RECRUITER' ? '/recruiter' : role === 'OPERATOR' ? '/operator' : '/';
}

/**
 * "지원하기" pressed while logged out (SC-A02, SC-C01): remembered for this tab only so login or signup can carry on
 * with that posting. The logo clears it (SC-C00).
 */
const PENDING_APPLY = 'pendingApply';

export function setPendingApply(postingId: string | null): void {
  try {
    if (postingId) sessionStorage.setItem(PENDING_APPLY, postingId);
    else sessionStorage.removeItem(PENDING_APPLY);
  } catch {
    // Storage blocked: the user simply starts again from the posting.
  }
}

export function getPendingApply(): string | null {
  try {
    return sessionStorage.getItem(PENDING_APPLY);
  } catch {
    return null;
  }
}

/** First step of applying: SC-A04 환경 점검. */
export function applyStartPath(postingId: string): string {
  return `/postings/${encodeURIComponent(postingId)}/apply`;
}

/** Only same-origin app paths are accepted as ?next= targets (no open redirect). */
export function safeNext(next: string | null): string | null {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return null;
  if (next.startsWith('/login') || next.startsWith('/signup') || next.startsWith('/reset-password')) return null;
  return next;
}

export function loginPath(pathWithSearch: string, reason?: 'expired'): string {
  const params = new URLSearchParams({ next: pathWithSearch });
  if (reason) params.set('reason', reason);
  return `/login?${params.toString()}`;
}

/** GET retries only for network/5xx errors, at most twice (design D 3.11). */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  return error instanceof ApiError && (error.status === 0 || error.status >= 500);
}
