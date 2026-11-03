import { fireEvent, screen, waitFor, within } from '@testing-library/react';
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
  it('adds the company\'s own items, shares the 100% with them and saves their criteria', async () => {
    let saved: Record<string, unknown> | null = null;
    server.use(
      http.get('*/api/recruiter/postings', () => HttpResponse.json([POST])),
      http.get('*/api/recruiter/postings/p1/evaluation-settings', () => HttpResponse.json(SETTINGS)),
      http.put('*/api/recruiter/postings/p1/evaluation-settings', async ({ request }) => { saved = await request.json() as Record<string, unknown>; return HttpResponse.json(SETTINGS); }),
    );
    renderRoutes(routes, { path: '/recruiter/questions', session: recruiterSession });
    expect(await screen.findByLabelText('질문 1')).toBeTruthy();

    // A recommended item for the job family (개발) starts at 10%, so the total is 110% until it is balanced.
    fireEvent.click(screen.getByRole('button', { name: '+ 기술 이해도' }));
    expect(screen.getByTestId('weight-total').textContent).toContain('110%');
    expect((screen.getByRole('button', { name: '+ 기술 이해도' }) as HTMLButtonElement).disabled).toBe(true);  // no twin
    // An item typed by hand gets the default 상/중/하 criteria, which can be edited.
    fireEvent.change(screen.getByLabelText('새 항목 이름'), { target: { value: '책임감' } });
    fireEvent.change(screen.getByLabelText('새 항목 한 줄 설명'), { target: { value: '끝까지 하는가' } });
    fireEvent.click(screen.getByRole('button', { name: '추가' }));
    expect(screen.getByTestId('weight-total').textContent).toContain('120%');
    const card = screen.getByLabelText('평가 항목 책임감');
    fireEvent.change(within(card).getByLabelText('상 기준'), { target: { value: '끝까지 책임진 사례가 있다' } });
    // 0% = not evaluated, shown dimmed with the sentence of the spec.
    fireEvent.change(within(card).getByLabelText('책임감 가중치'), { target: { value: '0' } });
    expect(within(card).getByText('이 항목은 평가하지 않습니다.')).toBeTruthy();
    expect(screen.getByTestId('weight-total').textContent).toContain('110%');

    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect((await screen.findByTestId('toast')).textContent).toBe('가중치 합계를 100%로 맞춘 뒤 저장해 주십시오');
    fireEvent.click(screen.getByRole('button', { name: '비율 유지하며 100%로 맞추기' }));
    expect(screen.getByTestId('weight-total').textContent).toContain('100%');
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(saved).not.toBeNull());
    const body = saved as unknown as { weights: Record<string, number>; custom_items: { name: string; weight: number; high: string }[] };
    expect(body.custom_items.map((c) => c.name)).toEqual(['기술 이해도', '책임감']);
    expect(body.custom_items[1]).toMatchObject({ weight: 0, high: '끝까지 책임진 사례가 있다' });
    expect(Object.values(body.weights).reduce((a, b) => a + b, 0) + body.custom_items.reduce((a, c) => a + c.weight, 0)).toBe(100);

    fireEvent.click(within(screen.getByLabelText('평가 항목 기술 이해도')).getByRole('button', { name: '기술 이해도 삭제' }));
    expect(screen.queryByLabelText('평가 항목 기술 이해도')).toBeNull();
  });

  it('allows at most five own items', async () => {
    server.use(
      http.get('*/api/recruiter/postings', () => HttpResponse.json([POST])),
      http.get('*/api/recruiter/postings/p1/evaluation-settings', () => HttpResponse.json({ ...SETTINGS,
        custom_items: [1, 2, 3, 4, 5].map((n) => ({ id: `id00000${n}`, name: `항목${n}`, description: '', high: 'a', mid: 'b', low: 'c', weight: 0 })) })),
    );
    renderRoutes(routes, { path: '/recruiter/questions', session: recruiterSession });
    fireEvent.change(await screen.findByLabelText('새 항목 이름'), { target: { value: '여섯째' } });
    fireEvent.click(screen.getByRole('button', { name: '추가' }));
    expect((await screen.findByTestId('toast')).textContent).toBe('평가 항목은 직접 5개까지 추가할 수 있습니다');
  });
});
