import { fireEvent, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { MESSAGES } from '@/shared/copy/fixed';
import { candidateSession, recruiterSession, renderRoutes } from '@/test/render';
import { routes } from './routes';
import { safeNext } from './session';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('route guards', () => {
  it('sends a logged-out user to /login?next=', async () => {
    server.use(http.get('*/api/auth/me', () => HttpResponse.json(
      { error: { code: 'UNAUTHENTICATED', message: '로그인이 필요합니다.', request_id: 'r' } }, { status: 401 })));
    const { router } = renderRoutes(routes, { path: '/me/applications' });
    expect(await screen.findByRole('heading', { name: '로그인' })).toBeTruthy();
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent('/me/applications')}`);
  });

  it('shows the USR-05 guidance when a recruiter opens a candidate-only screen', async () => {
    renderRoutes(routes, { path: '/me/applications', session: recruiterSession });
    expect(await screen.findByText(MESSAGES.ROLE_FORBIDDEN_CANDIDATE_AREA)).toBeTruthy();
  });

  it('lets a recruiter read a posting but blocks [지원하기] (AC-02)', async () => {
    renderRoutes(routes, { path: '/postings/p1', session: recruiterSession });
    fireEvent.click(await screen.findByTestId('apply-button'));
    expect(screen.getByText(MESSAGES.ROLE_FORBIDDEN_CANDIDATE_AREA)).toBeTruthy();
  });

  it('keeps candidates out of recruiter screens without rendering them', async () => {
    renderRoutes(routes, { path: '/recruiter', session: candidateSession });
    expect(await screen.findByText(MESSAGES.ROLE_FORBIDDEN_RECRUITER_AREA)).toBeTruthy();
    expect(screen.queryByText('공고 관리', { selector: 'h1' })).toBeNull();
  });

  it('loads the lazy recruiter home for a recruiter', async () => {
    renderRoutes(routes, { path: '/recruiter', session: recruiterSession });
    expect(await screen.findByRole('heading', { name: '공고 관리', level: 1 })).toBeTruthy();
  });

  it('accepts only same-origin paths as ?next=', () => {
    expect(safeNext('/me/applications')).toBe('/me/applications');
    expect(safeNext('//evil.example')).toBeNull();
    expect(safeNext('https://evil.example')).toBeNull();
    expect(safeNext('/\\evil')).toBeNull();
    expect(safeNext('/login?next=/x')).toBeNull();
  });
});
