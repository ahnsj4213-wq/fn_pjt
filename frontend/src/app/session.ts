import { useQuery, type QueryClient } from '@tanstack/react-query';
import { fetchSession, type Role, type SessionOut } from '@/api/common/auth';
import { ApiError } from '@/shared/http/client';

export const SESSION_KEY = ['auth', 'me'] as const;

export function useSession() {
  return useQuery({ queryKey: SESSION_KEY, queryFn: fetchSession, staleTime: Infinity });
}

export function setSession(qc: QueryClient, s: SessionOut | null): void {
  qc.setQueryData(SESSION_KEY, s);
}

export function roleHome(role: Role): string {
  return role === 'RECRUITER' ? '/recruiter' : '/postings';
}

/** Only same-origin app paths are accepted as ?next= targets (no open redirect). */
export function safeNext(next: string | null): string | null {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return null;
  if (next.startsWith('/login') || next.startsWith('/signup')) return null;
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
