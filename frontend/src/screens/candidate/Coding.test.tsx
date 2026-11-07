import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { candidateSession, renderRoutes } from '@/test/render';
import { Coding } from './Coding';

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const STARTER = 'def solution(n):\n    return 0\n';
const VIEW = {
  problem: { title: '화단 스프링클러', statement_md: '### 문제 상황\n화단에 물을 줍니다.\n\n### 입력\n`h` `w`', level: 3, time_limit_ms: 4000, memory_mb: 256,
    starter_code: STARTER, examples: [{ input: '2 2\nS.\n..\n', output: '2\n', note: '가장 먼 칸까지 2분 걸립니다.' }] },
  language: 'python', languages: ['python'], started_at: '2026-11-05T00:00:00Z', deadline_at: '2099-01-01T00:00:00Z', server_now: '2026-11-05T00:00:10Z',
  draft: { code: STARTER, revision: 0, saved_at: null }, run_available: true,
};
const ROUTE = [{ path: 'applications/:aid/coding', element: <Coding /> }, { path: 'applications/:aid/done', element: <p>제출 완료 화면</p> }];

function setup(view: unknown = VIEW) {
  server.use(
    http.get('*/api/candidate/applications/a1/coding', () => HttpResponse.json(view as object)),
    http.post('*/api/candidate/applications/a1/heartbeat', () => HttpResponse.json({})),
    http.post('*/api/candidate/applications/a1/interview/heartbeat', () => HttpResponse.json({})),
  );
  return renderRoutes(ROUTE, { path: '/applications/a1/coding', session: candidateSession });
}

describe('코딩테스트 화면', () => {
  it('shows the problem panel, level, limits, timer and progress, and hides algorithm hints', async () => {
    setup();
    const panel = await screen.findByTestId('problem-panel');
    expect(within(panel).getByText('문제 상황')).toBeTruthy();
    expect(within(panel).getByText(/가장 먼 칸까지/)).toBeTruthy();
    expect(screen.getByTestId('coding-level').textContent).toBe('Lv.3');
    expect(screen.getByTestId('coding-limits').textContent).toContain('4초');
    expect(screen.getByTestId('coding-timer').textContent).toContain('남은 시간');
    expect(screen.getByRole('progressbar', { name: '남은 시간' })).toBeTruthy();
    expect(screen.getByRole('list', { name: '진행 상태' }).textContent).toContain('코딩테스트 진행 중');
    expect(document.body.textContent).not.toMatch(/BFS|태그|출처/);
  });

  it('runs and shows input, expected and own output with no verdict (AC-15)', async () => {
    server.use(http.post('*/api/candidate/applications/a1/coding/runs', () => HttpResponse.json({
      run_id: 'r1', verdict: 'NORMAL', outcome: 'OK', stdout: '', stderr: '', truncated: false, duration_ms: 12,
      examples: [{ input: '2 2\nS.\n..\n', stdout: '7\n', stderr: '', outcome: 'OK' }] })));
    setup();
    fireEvent.click(await screen.findByTestId('run-code'));
    const result = await screen.findByTestId('run-result');
    expect(within(result).getByText('기대 출력')).toBeTruthy();
    expect(within(result).getByText('내 출력')).toBeTruthy();
    expect(result.textContent).toContain('7');
    expect(result.textContent).not.toMatch(/정답|오답|통과|맞았|틀렸/);
  });

  it('keeps the test cases one tab away and reset goes back to the starter code', async () => {
    setup();
    await screen.findByTestId('problem-panel');
    expect(screen.getByRole('tab', { name: '테스트케이스', selected: true })).toBeTruthy();
    expect(screen.getByRole('tabpanel').textContent).toContain('비공개 테스트');
    fireEvent.click(screen.getByRole('tab', { name: '실행 결과' }));
    expect(screen.getByRole('tabpanel').textContent).toContain('정답 여부는 알려 주지 않으니');
    fireEvent.click(screen.getByRole('button', { name: '초기화' }));
    fireEvent.click(await screen.findByRole('button', { name: '되돌리기' }));
    await waitFor(() => expect(document.querySelector('.cm-content')?.textContent).toContain('def solution'));
  });
});
