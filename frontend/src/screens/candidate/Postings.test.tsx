import { screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { candidateSession, renderRoutes } from '@/test/render';
import { Postings } from './Postings';

const base = {
  company_name: '테스트회사', company_intro: null, job_family: 'DEV', job_family_label: '개발', requirements: null, preferred: null,
  duties: null, headcount: 1, experience_level: '신입', employment_type: '정규직', location: '서울', image_url: null,
  question_areas: [], deadline_date: null, deadline_at: null, is_open: true, coding_enabled: false, coding_language: null,
  coding_time_limit_min: null,
};

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('공고 목록', () => {
  it('does not list a posting the candidate has already applied to', async () => {
    server.use(
      http.get('*/api/postings', () => HttpResponse.json([
        { ...base, id: 'p1', title: '지원 안 한 공고', has_applied: false },
        { ...base, id: 'p2', title: '이미 지원한 공고', has_applied: true },
      ])),
      http.get('*/api/job-families', () => HttpResponse.json([])),
    );
    renderRoutes([{ path: '/', element: <Postings /> }], { path: '/', session: candidateSession });
    expect(await screen.findByText('지원 안 한 공고')).toBeTruthy();
    expect(screen.queryByText('이미 지원한 공고')).toBeNull();
  });
});
