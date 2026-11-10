import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { SESSION_KEY, setSession } from './session';

describe('setSession', () => {
  it('drops lists fetched before login, so the main page does not keep showing postings already applied to', () => {
    const qc = new QueryClient();
    qc.setQueryData(['postings', '', ''], [{ id: 'p1', has_applied: false }]);
    setSession(qc, { user: { id: 'u1', role: 'CANDIDATE' } } as never);
    expect(qc.getQueryData(['postings', '', ''])).toBeUndefined();
    expect(qc.getQueryData(SESSION_KEY)).toEqual({ user: { id: 'u1', role: 'CANDIDATE' } });
  });
});
