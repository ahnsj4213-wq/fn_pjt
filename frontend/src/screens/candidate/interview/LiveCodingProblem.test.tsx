import { fireEvent, render, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { candidateSession, renderRoutes } from '@/test/render';
import { MiniMarkdown, ProblemView } from '@/shared/ui/ProblemView';
import { CodeTool } from './tools';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const PROBLEM = {
  title: '회문 판별',
  statement_md: '문자열이 **회문**인지 판별합니다. 한 줄의 문자열 `s`를 읽습니다.\n\n### 입력\n한 줄의 문자열 `s` (길이 1 이상 200 이하)\n\n### 출력\n회문이면 `YES`, 아니면 `NO`\n',
  examples: [
    { input: 'race a car\n', output: 'NO\n', note: '공백을 빼면 raceacar이고, 거꾸로 읽으면 racaecar로 달라서 NO입니다.' },
    { input: 'abba\n', output: 'YES\n', note: null },
  ],
};

describe('문제 설명 표시', () => {
  it('turns the statement into headings, code and bold instead of showing the markup', () => {
    const { container } = render(<MiniMarkdown text={PROBLEM.statement_md} />);
    expect([...container.querySelectorAll('h4')].map((h) => h.textContent)).toEqual(['입력', '출력']);
    expect([...container.querySelectorAll('code')].map((c) => c.textContent)).toEqual(['s', 's', 'YES', 'NO']);
    expect(container.querySelector('b')?.textContent).toBe('회문');
    expect(container.textContent).not.toMatch(/###|`|\*\*/);
  });

  it('keeps list items together and never turns text into markup', () => {
    const { container } = render(<MiniMarkdown text={'- 첫째\n- 둘째\n\n<img src=x onerror=alert(1)>'} />);
    expect(container.querySelectorAll('li').length).toBe(2);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('shows each example with input and output side by side and its explanation', () => {
    render(<ProblemView statement="문제" examples={PROBLEM.examples} />);
    const first = within(screen.getByLabelText('예제 1'));
    expect(first.getByText('입력')).toBeTruthy();
    expect(first.getByText('race a car')).toBeTruthy();
    expect(first.getByText('NO')).toBeTruthy();
    expect(first.getByText(/거꾸로 읽으면 racaecar로 달라서 NO입니다/)).toBeTruthy();
    expect(within(screen.getByLabelText('예제 2')).queryByText('설명')).toBeNull(); // no note, no empty box
  });
});

describe('라이브 코딩 화면', () => {
  it('shows the problem the question points at, above the editor', async () => {
    server.use(http.get('*/api/candidate/applications/a1/interview/turns/t1/problem', () => HttpResponse.json(PROBLEM)));
    renderRoutes([{ path: '/x', element: <CodeTool aid="a1" turnId="t1" canSubmit /> }], { path: '/x', session: candidateSession });
    const panel = await screen.findByLabelText('문제 설명');
    expect(await within(panel).findByRole('heading', { name: '예제' })).toBeTruthy();
    expect(within(within(panel).getByLabelText('예제 1')).getByText('출력')).toBeTruthy();
    expect(screen.getByText('문제 · 회문 판별')).toBeTruthy();
    expect(within(panel).getByText(/거꾸로 읽으면 racaecar/)).toBeTruthy();
    expect(screen.getByLabelText('코드 입력')).toBeTruthy();
  });

  it('says so and offers a retry when the problem cannot be loaded', async () => {
    let calls = 0;
    server.use(http.get('*/api/candidate/applications/a1/interview/turns/t1/problem', () => {
      calls += 1;
      return calls < 3 ? HttpResponse.json({ error: { code: 'X', message: 'x' } }, { status: 500 }) : HttpResponse.json(PROBLEM);
    }));
    renderRoutes([{ path: '/x', element: <CodeTool aid="a1" turnId="t1" canSubmit /> }], { path: '/x', session: candidateSession });
    fireEvent.click(await screen.findByRole('button', { name: '다시 불러오기' }, { timeout: 5000 }));
    expect(await screen.findByText('문제 · 회문 판별')).toBeTruthy();
  });
});
