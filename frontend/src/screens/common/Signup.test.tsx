import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { MESSAGES } from '@/shared/copy/fixed';
import { renderRoutes } from '@/test/render';
import { Signup } from './Signup';
import { formatPhone, SIGNUP_MESSAGES } from './signupForm';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const routes = [
  { path: '/signup', element: <Signup /> },
  { path: '/login', element: <p>login page</p> },
];

function fill(values: Partial<Record<'이름' | '이메일(아이디)' | '비밀번호' | '비밀번호 확인' | '연락처', string>>) {
  for (const [label, value] of Object.entries(values)) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
}

const VALID = { 이름: '김지원', '이메일(아이디)': 'kim@example.com', 비밀번호: 'secret-pass-1', '비밀번호 확인': 'secret-pass-1', 연락처: '01012345678' };

describe('Signup (USR-02, AC-01)', () => {
  it('has exactly name, email, password, confirm and phone inputs; no birth date or gender', () => {
    const { container } = renderRoutes(routes, { path: '/signup' });
    const names = [...container.querySelectorAll('input, select, textarea')].map((el) => el.getAttribute('name'));
    expect(names).toEqual(['name', 'email', 'password', 'password_confirm', 'phone']);
    expect(screen.queryByLabelText(/생년월일|생일|성별/)).toBeNull();
    expect(container.textContent).not.toMatch(/생년월일|성별/);
  });

  it('formats the phone number as 010-0000-0000 while typing', () => {
    expect(formatPhone('01012345678')).toBe('010-1234-5678');
    expect(formatPhone('010-12')).toBe('010-12');
    expect(formatPhone('010123456789999')).toBe('010-1234-5678');
    renderRoutes(routes, { path: '/signup' });
    fill({ 연락처: '01098765432' });
    expect((screen.getByLabelText('연락처') as HTMLInputElement).value).toBe('010-9876-5432');
  });

  it('rejects a phone that is not 010-0000-0000 and a mismatched confirmation', async () => {
    renderRoutes(routes, { path: '/signup' });
    fill({ ...VALID, 연락처: '0111234567', '비밀번호 확인': 'different-1' });
    fireEvent.click(screen.getByRole('button', { name: '가입하기' }));
    expect(await screen.findByText(SIGNUP_MESSAGES.phone)).toBeTruthy();
    expect(screen.getByText(SIGNUP_MESSAGES.passwordConfirm)).toBeTruthy();
  });

  it('posts exactly the five fields and goes to login', async () => {
    let sent: unknown;
    server.use(http.post('*/api/auth/signup', async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ user_id: 'u1' }, { status: 201 });
    }));
    renderRoutes(routes, { path: '/signup' });
    fill(VALID);
    fireEvent.click(screen.getByRole('button', { name: '가입하기' }));
    expect(await screen.findByText('login page')).toBeTruthy();
    expect(sent).toEqual({
      name: '김지원', email: 'kim@example.com', password: 'secret-pass-1', password_confirm: 'secret-pass-1', phone: '010-1234-5678',
    });
  });

  it('shows 409 EMAIL_UNAVAILABLE on the email field', async () => {
    server.use(http.post('*/api/auth/signup', () => HttpResponse.json(
      { error: { code: 'EMAIL_UNAVAILABLE', message: '사용할 수 없는 이메일입니다.', request_id: 'r' } }, { status: 409 })));
    renderRoutes(routes, { path: '/signup' });
    fill(VALID);
    fireEvent.click(screen.getByRole('button', { name: '가입하기' }));
    await waitFor(() => expect(screen.getByLabelText('이메일(아이디)').getAttribute('aria-invalid')).toBe('true'));
    expect(screen.getByText(MESSAGES.EMAIL_UNAVAILABLE)).toBeTruthy();
  });
});
