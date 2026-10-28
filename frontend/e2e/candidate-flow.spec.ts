import { expect, test, type Page } from '@playwright/test';
import { FIXED } from '../src/shared/copy/fixed';

/**
 * The whole candidate journey against the real backend with fake AI/runner (backend/scripts/e2e_server.py):
 * apply -> precheck (WebRTC + STT round trip) -> voice interview over WebRTC -> coding run/submit -> 지원 현황.
 */
const PASSWORD = 'Str0ng-pass!';
const ANSWER_TEXT = '주문 API의 응답 지연을';

async function signupAndLogin(page: Page): Promise<void> {
  const email = `cand${Date.now()}@e2e.example`;
  const headers = { Origin: 'http://localhost:5173' };
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

test('candidate applies, passes precheck, interviews by voice and submits code', async ({ page }) => {
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
});
