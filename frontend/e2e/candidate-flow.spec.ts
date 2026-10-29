import { expect, test, type Page } from '@playwright/test';
import { FIXED } from '../src/shared/copy/fixed';

/**
 * The whole candidate journey against the real backend with fake AI/runner (backend/scripts/e2e_server.py):
 * apply -> precheck (WebRTC + STT round trip) -> voice interview over WebRTC -> coding run/submit -> 지원 현황.
 */
const PASSWORD = 'Str0ng-pass!';
const BASE = process.env.E2E_BASE_URL ?? 'http://localhost:5173';
const RECRUITER = process.env.E2E_RECRUITER ?? 'rec@e2e.example'; // seeded by backend/scripts/seed_demo.py
const ANSWER_TEXT = '주문 API의 응답 지연을';

async function signupAndLogin(page: Page): Promise<void> {
  const email = `cand${Date.now()}@e2e.example`;
  const headers = { Origin: BASE };
  let r = await page.request.post('/api/auth/signup', { headers, data: {
    name: '김지원', email, password: PASSWORD, password_confirm: PASSWORD, phone: '010-1234-5678' } });
  expect(r.status()).toBe(201);
  r = await page.request.post('/api/auth/login', { headers, data: { role: 'CANDIDATE', email, password: PASSWORD } });
  expect(r.ok()).toBeTruthy();
  const csrf = (await r.json()).csrf_token as string;
  r = await page.request.put('/api/candidate/resume', { headers: { ...headers, 'X-CSRF-Token': csrf }, data: { content: {
    name: '김지원', education: [], career: [{ company: '커머스랩', role: '백엔드 개발자', period: '2020-2024',
      description: '주문 API 응답 지연 개선' }], projects: [], skills: ['Python'], certificates: [] } } });
  expect(r.ok()).toBeTruthy();
}

test.setTimeout(240_000);

test('candidate applies, interviews by voice, submits code and reads the decided result', async ({ page, playwright }) => {
  // Browser-side AC-22 check (the decisive one is tools/offline/run.sh): the app never asks another host.
  const external: string[] = [];
  page.on('request', (r) => {
    const host = new URL(r.url()).hostname;
    if (!['localhost', '127.0.0.1'].includes(host) && !r.url().startsWith('data:')) external.push(r.url());
  });
  await signupAndLogin(page);

  // Apply (JOB-02, SEC-01, RES-07)
  await page.goto('/postings');
  await page.getByRole('link', { name: 'E2E 백엔드 개발자' }).click();
  await page.getByTestId('apply-button').click();
  await expect(page.getByRole('checkbox').first()).toBeVisible();
  for (const box of await page.getByRole('checkbox').all()) await box.check();
  await page.getByRole('button', { name: '동의하고 다음' }).click();
  await page.getByRole('button', { name: '다음' }).click();
  await page.getByTestId('submit-application').click();
  await expect(page.getByText('지원서를 제출했습니다.')).toBeVisible();
  await page.getByTestId('go-guide').click();
  await page.getByTestId('go-precheck').click();

  // Precheck (INT-01, INT-17): the start button stays disabled until the voice round trip passes (AC-08).
  await expect(page.getByTestId('start-interview')).toBeDisabled();
  await expect(page.getByTestId('check-voice')).toBeEnabled({ timeout: 15_000 });
  await page.getByTestId('check-voice').click();
  await expect(page.getByTestId('voice-check')).toContainText('확인됨', { timeout: 20_000 });
  await page.getByTestId('start-interview').click();

  // Voice interview (INT-04~07, INT-15): answer every question until the server moves on to coding.
  await expect(page).toHaveURL(/\/interview$/, { timeout: 60_000 });
  await expect(page.getByTestId('progress')).toHaveText('1 / 10', { timeout: 30_000 });
  let answered = 0;
  while (!/\/coding$/.test(page.url())) {
    const done = page.getByTestId('answer-done');
    const codingTimer = page.getByTestId('coding-timer');
    await expect(done.or(codingTimer)).toBeVisible({ timeout: 45_000 });
    if (await codingTimer.isVisible()) break;
    if (answered === 0) await expect(page.getByText(FIXED.ANSWER_PROMPT)).toBeVisible();
    await page.waitForTimeout(1_500); // let the fake microphone talk
    await done.click({ timeout: 3_000 }).catch(() => undefined); // the page may move on while clicking
    answered += 1;
    if (answered === 1) await expect(page.getByTestId('last-answer')).toContainText(ANSWER_TEXT, { timeout: 30_000 });
    expect(answered).toBeLessThan(20); // 10 main + at most 3 follow-ups (+ re-asks)
  }
  await expect(page).toHaveURL(/\/coding$/, { timeout: 60_000 });
  expect(answered).toBeGreaterThanOrEqual(10);

  // Coding (COD-02~04): [실행] says normal/abnormal and shows output, never correctness (AC-15).
  const editor = page.locator('.cm-content');
  await editor.click();
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.type('print(input())');
  await expect(page.getByTestId('save-state')).toContainText('자동 저장됨', { timeout: 10_000 });
  await page.getByTestId('run-code').click();
  const result = page.getByTestId('run-result');
  await expect(result).toContainText(FIXED.RUN_OK, { timeout: 30_000 });
  await expect(result).not.toContainText('정답');
  await page.getByRole('button', { name: '최종 제출' }).click();
  await page.getByRole('button', { name: '제출', exact: true }).click();
  await expect(page).toHaveURL(/\/done$/);

  await page.getByTestId('go-applications').click();
  await expect(page.getByTestId('my-applications')).toContainText('제출');
  const aid = (await page.locator('[data-testid^="action-"]').first().getAttribute('data-testid'))!.slice('action-'.length);

  // Evaluation runs in the worker (fake AI); the recruiter decides through the API (recruiter screens come later).
  const rec = await playwright.request.newContext({ baseURL: BASE, extraHTTPHeaders: { Origin: BASE } });
  const login = await rec.post('/api/auth/login', { data: { role: 'RECRUITER', email: RECRUITER, password: PASSWORD } });
  expect(login.ok()).toBeTruthy();
  const csrf = (await login.json()).csrf_token as string;
  await expect.poll(async () => {
    const list = await (await rec.get('/api/recruiter/applications')).json();
    return list.items.find((i: { id: string }) => i.id === aid)?.eval_status;
  }, { timeout: 60_000 }).toBe('DONE');
  const report = await (await rec.get(`/api/recruiter/applications/${aid}/report`)).json();
  expect(report.total_score).not.toBeNull();
  const decided = await rec.post(`/api/recruiter/applications/${aid}/decision`,
    { headers: { 'X-CSRF-Token': csrf }, data: { decision: 'PASS', expected_version: 0 } });
  expect(decided.ok()).toBeTruthy();
  await rec.dispose();

  // ADR-0013: the decision reaches the open 지원 현황 screen live (Redis pub/sub -> SSE), no reload.
  await expect(page.getByTestId('my-applications')).toContainText('합격', { timeout: 15_000 });
  // RPT-03/04: after the decision the candidate sees text feedback, never a total score.
  await page.getByTestId(`action-${aid}`).click();
  await expect(page.getByTestId('feedback')).toBeVisible();
  await expect(page.getByText('합격', { exact: true })).toBeVisible();
  expect(external).toEqual([]);
});
