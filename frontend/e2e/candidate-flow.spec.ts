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
// E2E_REAL_AI=1: the stack runs the real models (deploy/docker-compose.yml) and the microphone plays real speech
// (E2E_VOICE_WAV). Answers are transcribed rather than fixed, and grading on the CPU takes minutes.
const REAL_AI = !!process.env.E2E_REAL_AI;
const SPEAK_MS = REAL_AI ? 8_000 : 1_500;
const EVAL_TIMEOUT = REAL_AI ? 1_200_000 : 60_000;

// E2E_CANDIDATE (+ E2E_CANDIDATE_PASSWORD): log in as an existing account instead of signing up a new one, e.g. a test
// account in AIHIRE_TESTER_EMAILS that may apply to the same posting again.
const CANDIDATE = process.env.E2E_CANDIDATE;

async function signupAndLogin(page: Page): Promise<void> {
  const email = CANDIDATE ?? `cand${Date.now()}@e2e.example`;
  const password = CANDIDATE ? process.env.E2E_CANDIDATE_PASSWORD ?? PASSWORD : PASSWORD;
  const headers = { Origin: BASE };
  let r;
  if (!CANDIDATE) {
    r = await page.request.post('/api/auth/signup', { headers, data: {
      name: '김지원', email, password, password_confirm: password, phone: '010-1234-5678', birth_date: '1998-03-14', gender: 'F', terms_agreed: true } });
    expect(r.status(), await r.text()).toBe(201);
  }
  r = await page.request.post('/api/auth/login', { headers, data: { role: 'CANDIDATE', email, password } });
  expect(r.ok()).toBeTruthy();
  const csrf = (await r.json()).csrf_token as string;
  r = await page.request.put('/api/candidate/resume', { headers: { ...headers, 'X-CSRF-Token': csrf }, data: { content: {
    name: '김지원', education: [], career: [{ company: '커머스랩', role: '백엔드 개발자', period: '2020-2024',
      description: '주문 API 응답 지연 개선' }], projects: [], skills: ['Python'], certificates: [] } } });
  expect(r.ok()).toBeTruthy();
}

test.setTimeout(REAL_AI ? 1_800_000 : 240_000);

test('candidate applies, interviews by voice, submits code and reads the decided result', async ({ page, playwright }) => {
  // Browser-side AC-22 check (the decisive one is tools/offline/run.sh): the app never asks another host.
  const external: string[] = [];
  page.on('request', (r) => {
    const host = new URL(r.url()).hostname;
    if (!['localhost', '127.0.0.1'].includes(host) && !/^(data|blob):/.test(r.url())) external.push(r.url());
  });
  await signupAndLogin(page);

  // Apply (JOB-02, SEC-01, RES-07)
  await page.goto('/postings');
  await page.getByRole('button', { name: '닫기' }).click(); // the first-visit guide
  await page.getByRole('link', { name: process.env.E2E_POSTING ?? 'E2E 백엔드 개발자', exact: true }).click();
  await page.getByTestId('apply-button').click();

  // Device check (INT-01, INT-17) and consent (SEC-02), the same steps as coding-flow.spec.ts.
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

  // Voice interview (INT-04~07, INT-15): answer every question until the server moves on to coding.
  await expect(page).toHaveURL(/\/interview$/, { timeout: 60_000 });
  await expect(page.getByTestId('progress')).toContainText('1 / 10', { timeout: REAL_AI ? 300_000 : 30_000 });
  let answered = 0;
  while (!/\/coding$/.test(page.url())) {
    const done = page.getByTestId('answer-done');
    const codingTimer = page.getByTestId('coding-timer');
    await expect(done.or(codingTimer)).toBeVisible({ timeout: REAL_AI ? 180_000 : 45_000 });
    if (await codingTimer.isVisible()) break;
    if (answered === 0) await expect(page.getByText('답변 중', { exact: true })).toBeVisible();
    await page.waitForTimeout(SPEAK_MS); // let the fake microphone talk
    await done.click({ timeout: 3_000 }).catch(() => undefined); // the page may move on while clicking
    answered += 1;
    if (answered === 1) {
      // Real STT hears the looped recording from wherever it was when the microphone opened.
      await expect(page.getByTestId('captions')).toContainText(REAL_AI ? /응답|쿼리|인덱스|밀리초/ : ANSWER_TEXT,
        { timeout: 30_000 });
    }
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

  const aid = /applications\/([^/]+)\/done$/.exec(page.url())![1];
  await page.goto('/me/applications');
  await expect(page.getByTestId(`action-${aid}`)).toContainText('리포트 보기');

  // Evaluation runs in the worker (fake AI); the recruiter decides through the API (recruiter screens come later).
  const rec = await playwright.request.newContext({ baseURL: BASE, extraHTTPHeaders: { Origin: BASE } });
  const login = await rec.post('/api/auth/login', { data: { role: 'RECRUITER', email: RECRUITER, password: PASSWORD } });
  expect(login.ok()).toBeTruthy();
  const csrf = (await login.json()).csrf_token as string;
  await expect.poll(async () => {
    const list = await (await rec.get('/api/recruiter/applications')).json();
    return list.items.find((i: { id: string }) => i.id === aid)?.eval_status;
  }, { timeout: EVAL_TIMEOUT, intervals: REAL_AI ? [5_000] : undefined }).toBe('DONE');
  const report = await (await rec.get(`/api/recruiter/applications/${aid}/report`)).json();
  expect(report.total_score).not.toBeNull();
  const decided = await rec.post(`/api/recruiter/applications/${aid}/decision`,
    { headers: { 'X-CSRF-Token': csrf }, data: { decision: 'PASS', expected_version: 0 } });
  expect(decided.ok()).toBeTruthy();
  await rec.dispose();

  // ADR-0013: the decision reaches the open 지원 현황 screen live (Redis pub/sub -> SSE), no reload.
  await expect(page.getByTestId('my-applications')).toContainText('합격', { timeout: 15_000 });
  // RPT-03/04: after the decision the candidate sees text feedback, never a total score.
  await page.getByTestId(`action-${aid}`).getByRole('button', { name: '채용 결과 보기' }).click();
  await expect(page.getByTestId('feedback')).toBeVisible();
  await expect(page.getByText('합격', { exact: true })).toBeVisible();
  expect(external).toEqual([]);
});
