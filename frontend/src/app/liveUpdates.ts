import { type QueryClient, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { SESSION_KEY } from './session';

/**
 * Live updates (ADR-0013): the server pushes "application X changed" over SSE (Redis pub/sub behind it) and the
 * screen refetches its active queries through the normal allow-listed APIs; events carry no state values (SEC-04).
 * Without EventSource (tests) or when the server answers 503 (Redis off), screens simply keep their usual fetching.
 */
export function refetchOnChange(qc: QueryClient): void {
  void qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== SESSION_KEY[0] });
}

export function useLiveUpdates(userId: string | undefined): void {
  const qc = useQueryClient();
  useEffect(() => {
    if (!userId || typeof EventSource === 'undefined') return;
    const es = new EventSource('/api/events/stream');
    es.addEventListener('application', () => refetchOnChange(qc));
    return () => es.close();
  }, [userId, qc]);
}
