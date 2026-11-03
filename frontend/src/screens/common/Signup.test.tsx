import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { MESSAGES } from '@/shared/copy/fixed';
import { renderRoutes } from '@/test/render';
import { Login } from './Login';
import { formatPhone, required, SIGNUP_MESSAGES } from './signupForm';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const routes = [
  { path: '/login', element: <Login /> },
  { path: '/me/resume', element: <p>resume page</p> },
  { path: '/postings/:pid/apply', element: <p>apply step</p> },
];

type Label = '이름' | '생년월일' | '아이디 (이메일)' | '비밀번호' | '비밀번호 확인' | '연락처';
/** Queries inside the signup window (the login form behind it has fields with the same labels). */
const dlg = () => within([...document.querySelectorAll('dialog')].at(-1) as HTMLElement);

/** Matches a field label with or without the wireframe '필수' tag after it. */
const byLabel = (label: string) => new RegExp(`^${label.replace(/[()]/g, '\\$&')}( 필수)?$`);

function fill(values: Partial<Record<Label, string>>) {
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(dlg().getByLabelText(byLabel(label)), { target: { value } });
  }
}

const VALID = { 이름: '김지원', 생년월일: '1998-03-14', '아이디 (이메일)': 'kim@example.com', 비밀번호: 'secret1pass',
  '비밀번호 확인': 'secret1pass', 연락처: '01012345678' };

function openSignup() {
  renderRoutes(routes, { path: '/login', session: null });
  fireEvent.click(screen.getByRole('button', { name: '회원가입' }));
}

function completeChoices() {
  fireEvent.click(dlg().getByLabelText('여'));
  fireEvent.click(dlg().getByLabelText(/이용약관과 개인정보 처리방침에 동의합니다/));
}

const SESSION = { user: { id: 'u1', role: 'CANDIDATE', name: '김지원', company_id: null, company_name: null }, csrf_token: 'c' };

describe('SC-A01 지원자 회원가입 (작은 창)', () => {
  it('formats the phone number while typing', () => {
    expect(formatPhone('01012345678')).toBe('010-1234-5678');
    expect(formatPhone('010-12')).toBe('010-12');
    expect(formatPhone('010123456789999')).toBe('010-1234-5678');
    expect(required('이름')).toBe('이름을 입력해 주십시오');
    expect(required('아이디')).toBe('아이디를 입력해 주십시오');
  });

  it('shows the spec messages for every missing or wrong field', async () => {
    openSignup();
    fill({ 연락처: '0101234', 비밀번호: 'onlyletters', '비밀번호 확인': 'different1' });
    fireEvent.click(dlg().getByRole('button', { name: '가입하기' }));
    expect(await screen.findByText(SIGNUP_MESSAGES.name)).toBeTruthy();
    for (const m of [SIGNUP_MESSAGES.birth, SIGNUP_MESSAGES.password, SIGNUP_MESSAGES.passwordConfirm,
      SIGNUP_MESSAGES.gender, SIGNUP_MESSAGES.phone, SIGNUP_MESSAGES.terms]) {
      expect(screen.getByText(m)).toBeTruthy();
    }
  });

  it('signs up, signs in and goes to the resume page; a pending apply continues instead', async () => {
    let sent: unknown;
    server.use(
      http.post('*/api/auth/signup', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ user_id: 'u1' }, { status: 201 });
      }),
      http.post('*/api/auth/login', () => HttpResponse.json(SESSION)),
    );
    openSignup();
    fill(VALID);
    completeChoices();
    fireEvent.click(dlg().getByRole('button', { name: '가입하기' }));
    expect(await screen.findByText('resume page')).toBeTruthy();
    expect(screen.getByTestId('toast').textContent).toBe('가입했습니다. 반갑습니다!');
    expect(sent).toEqual({
      name: '김지원', birth_date: '1998-03-14', email: 'kim@example.com', password: 'secret1pass',
      password_confirm: 'secret1pass', gender: 'F', phone: '010-1234-5678', terms_agreed: true,
    });

    cleanup();
    sessionStorage.setItem('pendingApply', 'p9');
    openSignup();
    fill(VALID);
    completeChoices();
    fireEvent.click(dlg().getByRole('button', { name: '가입하기' }));
    expect(await screen.findByText('apply step')).toBeTruthy();
  });

  it('shows 409 EMAIL_UNAVAILABLE on the email field', async () => {
    server.use(http.post('*/api/auth/signup', () => HttpResponse.json(
      { error: { code: 'EMAIL_UNAVAILABLE', message: '사용할 수 없는 이메일입니다.', request_id: 'r' } }, { status: 409 })));
    openSignup();
    fill(VALID);
    completeChoices();
    fireEvent.click(dlg().getByRole('button', { name: '가입하기' }));
    await waitFor(() => expect(dlg().getByLabelText(byLabel('아이디 (이메일)')).getAttribute('aria-invalid')).toBe('true'));
    expect(screen.getByText(MESSAGES.EMAIL_UNAVAILABLE)).toBeTruthy();
  });
});
