import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { FIXED } from '../src/shared/copy/fixed';

/**
 * ADR-0021, the whole path in a real browser: server-chosen problem -> writing code -> [실행] -> [최종 제출] -> automatic grading
 * -> AI interview evaluation. The candidate here "solved" the problem with its reference program (backend/scripts/reference_code.py),
 * so a full mark proves the problem really gets graded.
 *
 * With backend/scripts/e2e_server.py: fake AI, and the candidate's code runs in a plain process.
 * With E2E_BASE_URL + E2E_REAL_AI=1 (deploy/docker-compose.yml): the real models and the real sandbox.
 */
const PASSWORD = 'Str0ng-pass!';
const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:5173';
const RECRUITER = process.env.E2E_RECRUITER ?? 'rec@e2e.example'; // seeded by backend/scripts/seed_demo.py
const POSTING = process.env.E2E_POSTING ?? 'E2E 백엔드 개발자';
const REAL_AI = !!process.env.E2E_REAL_AI;
const SPEAK_MS = REAL_AI ? 8_000 : 1_500;
const EVAL_TIMEOUT = REAL_AI ? 1_500_000 : 90_000;
test.setTimeout(REAL_AI ? 2_400_000 : 300_000);

function referenceProgram(title: string): string {
  const script = path.resolve(import.meta.dirname, '../../backend/scripts/reference_code.py');
  return execFileSync(process.env.E2E_PYTHON ?? 'python', [script, title], { encoding: 'utf-8' });
}

/** Replaces the editor's text the way pasting does (typing would trigger auto-indent and bracket closing). */
async function pasteCode(editor: Locator, code: string): Promise<void> {
  await editor.click();
  await editor.press('ControlOrMeta+A');
  await editor.evaluate((el, text) => {
    const data = new DataTransfer();
    data.setData('text/plain', text);
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  }, code);
}

async function signupAndLogin(page: Page): Promise<void> {
  const email = `coding${Date.now()}@e2e.example`;
  const headers = { Origin: BASE };
  let r = await page.request.post('/api/auth/signup', { headers, data: {
    name: '김지원', email, password: PASSWORD, password_confirm: PASSWORD, phone: '010-1234-5678', birth_date: '1998-03-14', gender: 'F', terms_agreed: true } });
  expect(r.status(), await r.text()).toBe(201);
  r = await page.request.post('/api/auth/login', { headers, data: { role: 'CANDIDATE', email, password: PASSWORD } });
  expect(r.ok()).toBeTruthy();
  const csrf = (await r.json()).csrf_token as string;
  r = await page.request.put('/api/candidate/resume', { headers: { ...headers, 'X-CSRF-Token': csrf }, data: { content: {
    name: '김지원', education: [], career: [{ company: '커머스랩', role: '백엔드 개발자', period: '2020-2024',
      description: '주문 API 응답 지연 개선' }], projects: [], skills: ['Python'], certificates: [] } } });
  expect(r.ok(), await r.text()).toBeTruthy();
}

async function applyAndCheckDevices(page: Page): Promise<void> {
  await page.goto('/');
  await page.getByRole('button', { name: '닫기' }).click(); // the first-visit guide
  await page.getByRole('link', { name: POSTING }).first().click();
  await page.getByTestId('apply-button').click();
  await expect(page.getByTestId('st-cam')).toHaveText('정상', { timeout: 20_000 });
  await page.getByRole('button', { name: '마이크 테스트' }).click();
  await expect(page.getByTestId('st-mic')).toHaveText('정상', { timeout: 20_000 });
  await page.getByRole('button', { name: '테스트 소리 재생' }).click();
  await page.getByRole('button', { name: '들립니다' }).click();
  await page.getByTestId('check-next').click();
  await page.getByRole('radio', { name: '동의합니다' }).check();
  await page.locator('#consent').check(); // share the result with the company: otherwise the recruiter never sees it
  await expect(page.getByTestId('start-interview')).toBeEnabled({ timeout: 15_000 });
  await page.getByTestId('start-interview').click();
}

test('server-chosen problem: write, run, submit, get graded and reach the AI evaluation', async ({ page, playwright }) => {
  await signupAndLogin(page);
  await applyAndCheckDevices(page);

  // The interview, until the server moves on to the coding test: by voice over WebRTC, or typed when the voice link does not
  // come up and the screen falls back to text mode (INT-18). `mode` records which one this run really exercised.
  await expect(page).toHaveURL(/\/interview$/, { timeout: 90_000 });
  const timer = page.getByTestId('coding-timer');
  let mode = 'voice';
  for (let answered = 0; !/\/coding$/.test(page.url()); answered += 1) {
    expect(answered).toBeLessThan(25);
    const done = page.getByTestId('answer-done');
    const typed = page.getByTestId('text-answer-submit');
    await expect(done.or(timer).or(typed)).toBeVisible({ timeout: REAL_AI ? 180_000 : 45_000 });
    if (await timer.isVisible()) break;
    if (await typed.isVisible()) {
      mode = 'text';
      await page.getByRole('textbox', { name: '답변' }).fill('주문 API의 응답 지연을 캐시와 인덱스로 줄인 경험이 있습니다. 쿼리 실행 계획을 보고 병목을 찾았습니다.');
      await typed.click({ timeout: 3_000 }).catch(() => undefined);
      await expect(typed).toBeEnabled({ timeout: REAL_AI ? 120_000 : 30_000 }).catch(() => undefined);
    } else {
      await page.waitForTimeout(SPEAK_MS);
      await done.click({ timeout: 3_000 }).catch(() => undefined); // the page may move on while clicking
    }
    await page.waitForTimeout(300);
  }
  test.info().annotations.push({ type: 'interview-mode', description: mode });
  console.log(`interview-mode: ${mode}`);
  await expect(page).toHaveURL(/\/coding$/, { timeout: 60_000 });

  // The screen: enough explanation to write code, level, limits, progress, and working starter code.
  const panel = page.getByTestId('problem-panel');
  for (const part of ['입력', '출력', '예제 1', '설명']) await expect(panel).toContainText(part);
  await expect(page.getByTestId('coding-level')).toHaveText(/^Lv\.[0-5]$/);
  await expect(page.getByRole('progressbar', { name: '남은 시간' })).toBeVisible();
  await expect(page.getByRole('list', { name: '진행 상태' })).toContainText('코딩테스트 진행 중');
  const editor = page.locator('.cm-content');
  await expect(editor).toContainText('def solution');
  await page.screenshot({ path: test.info().outputPath('coding-screen.png'), fullPage: true });

  // [실행] shows the output next to the expected one and never says whether it is right (AC-15).
  await page.getByTestId('run-code').click();
  const result = page.getByTestId('run-result');
  await expect(result).toContainText(FIXED.RUN_OK, { timeout: 30_000 });
  await expect(result).toContainText('기대 출력');
  await expect(result).toContainText('내 출력');
  for (const word of ['정답', '오답', '통과']) await expect(result).not.toContainText(word);

  // The candidate API gives nothing away: no tags, no origin, no hidden test.
  const aid = /applications\/([^/]+)\/coding/.exec(page.url())![1];
  const view = await (await page.request.get(`/api/candidate/applications/${aid}/coding`)).json();
  expect(Object.keys(view.problem).sort()).toEqual(['examples', 'level', 'memory_mb', 'starter_code', 'statement_md', 'time_limit_ms', 'title']);
  expect(JSON.stringify(view)).not.toMatch(/HIDDEN|reference_urls|"tags"/);

  // Writing the solution, running it, submitting it.
  await pasteCode(editor, referenceProgram(view.problem.title));
  await expect(page.getByTestId('save-state')).toContainText('자동 저장됨', { timeout: 10_000 });
  await page.getByTestId('run-code').click();
  await expect(result).toContainText(FIXED.RUN_OK, { timeout: 30_000 });
  await expect(result).toContainText(view.problem.examples[0].output.trim());
  await page.screenshot({ path: test.info().outputPath('coding-run.png'), fullPage: true });
  await page.getByRole('button', { name: '최종 제출' }).click();
  await page.getByRole('button', { name: '제출', exact: true }).click();
  await expect(page).toHaveURL(/\/done$/);

  // Automatic grading and the evaluation, as the recruiter sees them.
  const rec = await playwright.request.newContext({ baseURL: BASE, extraHTTPHeaders: { Origin: BASE } });
  expect((await rec.post('/api/auth/login', { data: { role: 'RECRUITER', email: RECRUITER, password: PASSWORD } })).ok()).toBeTruthy();
  await expect.poll(async () => {
    const list = await (await rec.get('/api/recruiter/applications')).json();
    return list.items.find((i: { id: string }) => i.id === aid)?.eval_status;
  }, { timeout: EVAL_TIMEOUT, intervals: [5_000] }).toBe('DONE');

  const coding = await (await rec.get(`/api/recruiter/applications/${aid}/coding-result`)).json();
  expect(coding.submission.grading_status).toBe('DONE');
  expect(coding.submission.total).toBeGreaterThanOrEqual(6);
  expect(coding.submission.passed).toBe(coding.submission.total);
  expect(Number(coding.submission.score)).toBe(100);
  expect(coding.tests.some((t: { visibility: string }) => t.visibility === 'HIDDEN')).toBe(true);
  expect(coding.problem.level).toBe(view.problem.level);
  expect(coding.problem.tags.length).toBeGreaterThan(0);
  const report = await (await rec.get(`/api/recruiter/applications/${aid}/report`)).json();
  expect(report.total_score).not.toBeNull();
  // The evaluation got the grade: its coding score combines the full mark with the AI code review of the level (ADR-0021).
  expect(Number(report.coding.score)).toBeGreaterThan(0);
  expect(report.coding.items.length).toBeGreaterThan(0);
  await rec.dispose();
});
