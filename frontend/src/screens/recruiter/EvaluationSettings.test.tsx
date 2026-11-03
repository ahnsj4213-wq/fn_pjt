import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { recruiterSession, renderRoutes } from '@/test/render';
import EvaluationSettings from './EvaluationSettings';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const POST = { id: 'p1', title: '백엔드 개발자', job_family: 'DEV', status: 'OPEN', effective_status: 'OPEN', deadline_date: null, headcount: 1,
  experience_level: null, employment_type: null, location: null, view_count: 0, hidden: false, coding_enabled: false, applicant_count: 0,
  completed_count: 0, updated_at: '2026-11-01T00:00:00Z' };
const SETTINGS = { version: 1, weights: { expertise: 30, logic: 30, attitude: 20, delivery: 20 }, custom_question: null,
  interview: { question_plan: null, think_sec: 10, answer_sec: 60, followup_max: 2 },
  question_areas: ['PERSONALITY', 'MOTIVATION', 'JOB_SKILL', 'PROJECT', 'COLLABORATION'], rubric_version: 'rubric-v1', locked: false, coding_enabled: false };
const routes = [{ path: '/recruiter/questions', element: <EvaluationSettings /> }];

describe('면접 질문 관리 (SC-R07)', () => {
  it('moves the questions of a turned-off area, blocks a wrong weight total and saves the plan', async () => {
    let saved: Record<string, unknown> | null = null;
    server.use(
      http.get('*/api/recruiter/postings', () => HttpResponse.json([POST])),
      http.get('*/api/recruiter/postings/p1/evaluation-settings', () => HttpResponse.json(SETTINGS)),
      http.put('*/api/recruiter/postings/p1/evaluation-settings', async ({ request }) => { saved = await request.json() as Record<string, unknown>; return HttpResponse.json(SETTINGS); }),
    );
    renderRoutes(routes, { path: '/recruiter/questions', session: recruiterSession });
    expect(await screen.findByLabelText('질문 1')).toBeTruthy();

    fireEvent.click(screen.getByRole('checkbox', { name: /협업/ }));
    expect((await screen.findByTestId('toast')).textContent).toBe('협업 분야의 질문 1개를 인성(으)로 옮겼습니다');

    fireEvent.change(screen.getByLabelText('질문 1'), { target: { value: '자기소개를 해 주십시오' } });
    fireEvent.change(screen.getByLabelText('논리성 가중치'), { target: { value: '40' } });
    expect(screen.getByTestId('weight-total').textContent).toContain('110%');
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect((await screen.findByTestId('toast')).textContent).toBe('가중치 합계를 100%로 맞춘 뒤 저장해 주십시오');
    expect(saved).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '비율 유지하며 100%로 맞추기' }));
    expect(screen.getByTestId('weight-total').textContent).toContain('100%');
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(saved).not.toBeNull());
    const body = saved as unknown as { question_areas: string[]; interview: { question_plan: { area: string; text: string | null }[] } };
    expect(body.question_areas).not.toContain('COLLABORATION');
    expect(body.interview.question_plan[0]).toEqual({ area: 'PERSONALITY', text: '자기소개를 해 주십시오' });
    expect(body.interview.question_plan[4].area).toBe('PERSONALITY');
  });

  it('keeps at least one area', async () => {
    server.use(
      http.get('*/api/recruiter/postings', () => HttpResponse.json([POST])),
      http.get('*/api/recruiter/postings/p1/evaluation-settings', () => HttpResponse.json({ ...SETTINGS, question_areas: ['PROJECT'] })),
    );
    renderRoutes(routes, { path: '/recruiter/questions', session: recruiterSession });
    fireEvent.click(await screen.findByRole('checkbox', { name: /프로젝트 경험/ }));
    expect((await screen.findByTestId('toast')).textContent).toBe('질문 분야를 하나 이상 골라 주십시오');
  });
});
