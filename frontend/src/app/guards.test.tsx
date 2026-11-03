import { fireEvent, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { MESSAGES } from '@/shared/copy/fixed';
import { candidateSession, recruiterSession, renderRoutes } from '@/test/render';
import { routes } from './routes';
import { safeNext } from './session';
import { NOT_FOR_COMPANY } from './useStartApply';

const POSTING = {
  id: 'p1', title: '백엔드 개발자', company_name: '테스트회사', company_intro: null, job_family: 'DEV',
  job_family_label: '개발', requirements: null, preferred: null, duties: null, headcount: 1, experience_level: '신입',
  employment_type: '정규직', location: '서울', image_url: null, question_areas: [{ code: 'PERSONALITY', label: '인성' }],
  deadline_date: null, deadline_at: null, is_open: true, coding_enabled: false, coding_language: null,
  coding_time_limit_min: null, has_applied: false,
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('route guards', () => {
  it('sends a logged-out user to /login?next=', async () => {
    server.use(http.get('*/api/auth/me', () => HttpResponse.json(
      { error: { code: 'UNAUTHENTICATED', message: '로그인이 필요합니다.', request_id: 'r' } }, { status: 401 })));
    const { router } = renderRoutes(routes, { path: '/me/applications' });
    expect(await screen.findByRole('heading', { name: '지원자 로그인' })).toBeTruthy();
    expect(router.state.location.search).toBe(`?next=${encodeURIComponent('/me/applications')}`);
  });

  it('shows the USR-05 guidance when a recruiter opens a candidate-only screen', async () => {
    renderRoutes(routes, { path: '/me/applications', session: recruiterSession });
    expect(await screen.findByText(MESSAGES.ROLE_FORBIDDEN_CANDIDATE_AREA)).toBeTruthy();
  });

  it('lets a recruiter read a posting but blocks [AI 면접 지원하기] (AC-02, SC-A03)', async () => {
    server.use(http.get('*/api/postings/p1', () => HttpResponse.json(POSTING)));
    renderRoutes(routes, { path: '/postings/p1', session: recruiterSession });
    fireEvent.click(await screen.findByTestId('apply-button'));
    expect((await screen.findByTestId('toast')).textContent).toBe(NOT_FOR_COMPANY);
  });

  it('shows a posting to a logged-out visitor and sends [AI 면접 지원하기] to login (SC-A03, SC-C01)', async () => {
    server.use(
      http.get('*/api/postings/p1', () => HttpResponse.json(POSTING)),
      http.get('*/api/auth/me', () => HttpResponse.json(
        { error: { code: 'UNAUTHENTICATED', message: '로그인이 필요합니다.', request_id: 'r' } }, { status: 401 })),
    );
    const { router } = renderRoutes(routes, { path: '/postings/p1', session: null });
    expect(await screen.findByText(/로그인 필요/)).toBeTruthy();
    fireEvent.click(screen.getByTestId('apply-button'));
    expect(await screen.findByText(/로그인하면 보던 공고로 이어집니다/)).toBeTruthy();
    expect(router.state.location.pathname).toBe('/login');
    expect(sessionStorage.getItem('pendingApply')).toBe('p1');
  });

  it('keeps candidates out of recruiter screens without rendering them', async () => {
    renderRoutes(routes, { path: '/recruiter', session: candidateSession });
    expect(await screen.findByText(MESSAGES.ROLE_FORBIDDEN_RECRUITER_AREA)).toBeTruthy();
    expect(screen.queryByText('대시보드', { selector: 'h1' })).toBeNull();
  });

  it('loads the lazy recruiter home for a recruiter', async () => {
    server.use(
      http.get('*/api/recruiter/company', () => HttpResponse.json({ id: 'c1', name: '테스트회사', biz_no: null, verification: 'APPROVED', evidence_name: null, reject_reason: null })),
      http.get('*/api/recruiter/dashboard', () => HttpResponse.json({ open_postings: 0, awaiting_review: 0, applicants_this_month: 0, applicants_last_month: 0,
        views_total: 0, completion_rate: null, postings: [] })),
      http.get('*/api/recruiter/schedule/upcoming', () => HttpResponse.json([])),
      http.get('*/api/recruiter/stats', () => HttpResponse.json({ monthly_applicants: [], avg_score_by_job_family: [], pass_rate_by_posting: [], pass_rate_by_job_family: [] })),
    );
    renderRoutes(routes, { path: '/recruiter', session: recruiterSession });
    expect(await screen.findByRole('heading', { name: '대시보드', level: 1 })).toBeTruthy();
  });

  it('accepts only same-origin paths as ?next=', () => {
    expect(safeNext('/me/applications')).toBe('/me/applications');
    expect(safeNext('//evil.example')).toBeNull();
    expect(safeNext('https://evil.example')).toBeNull();
    expect(safeNext('/\\evil')).toBeNull();
    expect(safeNext('/login?next=/x')).toBeNull();
  });
});
