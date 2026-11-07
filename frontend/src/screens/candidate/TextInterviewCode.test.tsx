import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { InterviewState } from '@/api/candidate/interview';
import { candidateSession, renderRoutes } from '@/test/render';
import { TextInterview } from './Interview';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const turn = (mode: 'SPEECH' | 'CODE') => ({ turn_id: 't1', main_no: 3, is_followup: false, presentation_no: 1, phase: 'ANSWERING', question_text: '구간 합 구하기: 화면의 문제를 코드로 풀면서, 어떻게 풀었는지 설명해 주십시오.',
  area: 'JOB_SKILL', answer_mode: mode, limit_sec: 180, think_until: null, opened_at: '2026-11-03T00:00:00Z', deadline_at: '2099-01-01T00:00:00Z' });
const state = (mode: 'SPEECH' | 'CODE'): InterviewState => ({ attempt_status: 'INTERVIEWING', interrupted_from: null, mode: 'TEXT', server_now: '2026-11-03T00:00:10Z',
  total_main: 5, answered_main: 2, followups_used: 0, followup_max: 2, gaze_warnings: 0, next_after_interview: 'DONE', warnings: [], turn: turn(mode),
  slot_grace_until: null, coding_deadline_at: null } as unknown as InterviewState);

const PROBLEM = { title: '구간 합 구하기', statement_md: '배열의 구간 합을 구합니다.\n\n### 입력\n`n` `q`', examples: [{ input: '3 1\n-1 0 1\n1 3\n', output: '0\n', note: '1~3번째 합은 0입니다.' }] };

describe('텍스트 모드의 라이브 코딩 질문', () => {
  it('shows the problem and a code editor, and sends the code with no separate explanation', async () => {
    const calls: string[] = [];
    server.use(
      http.get('*/api/candidate/applications/a1/interview/turns/t1/problem', () => HttpResponse.json(PROBLEM)),
      http.put('*/api/candidate/applications/a1/interview/turns/t1/code', async ({ request }) => { calls.push(`code:${((await request.json()) as { code: string }).code.trim()}`); return HttpResponse.json({ turn_id: 't1', saved_at: 'x' }); }),
      http.post('*/api/candidate/applications/a1/interview/turns/t1/text-answer', async ({ request }) => { calls.push(`text:${((await request.json()) as { text: string }).text}`); return HttpResponse.json({ turn_id: 't1', state: 'CLOSED' }, { status: 202 }); }),
      http.post('*/api/candidate/applications/a1/interview/next', () => HttpResponse.json({ ...state('SPEECH'), attempt_status: 'SUBMITTED', turn: null })),
    );
    renderRoutes([{ path: '/x', element: <TextInterview aid="a1" initial={state('CODE')} onFinished={() => undefined} /> }], { path: '/x', session: candidateSession });
    const panel = await screen.findByLabelText('문제 설명');
    expect(await within(panel).findByText(/1~3번째 합은 0입니다/)).toBeTruthy();
    const editor = screen.getByLabelText('코드 입력') as HTMLTextAreaElement;
    fireEvent.change(editor, { target: { value: 'print(0)' } });
    expect(screen.queryByRole('textbox', { name: '답변' })).toBeNull(); // the explanation goes in the code's comments
    fireEvent.click(screen.getByTestId('text-answer-submit'));
    await waitFor(() => expect(calls).toEqual(['code:print(0)', 'text:']));  // code first; there is no separate explanation
  });

  it('keeps the plain answer box for other questions', async () => {
    renderRoutes([{ path: '/x', element: <TextInterview aid="a1" initial={state('SPEECH')} onFinished={() => undefined} /> }], { path: '/x', session: candidateSession });
    expect(await screen.findByLabelText('답변')).toBeTruthy();
    expect(screen.queryByLabelText('코드 입력')).toBeNull();
  });
});
