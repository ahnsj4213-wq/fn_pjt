import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { refetchOnChange } from './liveUpdates';
import { SESSION_KEY } from './session';

describe('live updates (ADR-0013)', () => {
  it('marks every query stale except the session', () => {
    const qc = new QueryClient();
    qc.setQueryData(SESSION_KEY, { user: null });
    qc.setQueryData(['my-applications'], []);
    qc.setQueryData(['my-result', 'a1'], {});
    refetchOnChange(qc);
    expect(qc.getQueryState(['my-applications'])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(['my-result', 'a1'])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(SESSION_KEY)?.isInvalidated).toBe(false);
  });
});
