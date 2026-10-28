import { fireEvent, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { candidateSession, renderRoutes } from '@/test/render';
import { Account } from './Account';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const routes = [
  { path: '/me/account', element: <Account /> },
  { path: '/login', element: <p>login page</p> },
];

function withdraw(password: string) {
  fireEvent.change(screen.getByLabelText('비밀번호 확인'), { target: { value: password } });
  fireEvent.click(screen.getByRole('button', { name: '회원 탈퇴' }));
  fireEvent.click(screen.getByRole('button', { name: '탈퇴' }));
}

describe('Account withdrawal (USR-07)', () => {
  it('shows the re-auth error on the password field', async () => {
    server.use(http.delete('*/api/candidate/me', () => HttpResponse.json(
      { error: { code: 'REAUTH_FAILED', message: '비밀번호가 올바르지 않습니다.', request_id: 'r' } }, { status: 403 })));
    renderRoutes(routes, { path: '/me/account', session: candidateSession });
    withdraw('wrong');
    expect(await screen.findByText('비밀번호가 올바르지 않습니다.')).toBeTruthy();
  });

  it('explains an attempt in progress and stays on the page', async () => {
    server.use(http.delete('*/api/candidate/me', () => HttpResponse.json(
      { error: { code: 'ACTIVE_ATTEMPT', message: '응시가 끝난 뒤 탈퇴할 수 있습니다.', request_id: 'r' } }, { status: 409 })));
    const { router } = renderRoutes(routes, { path: '/me/account', session: candidateSession });
    withdraw('secret');
    expect(await screen.findByText('응시가 끝난 뒤 탈퇴할 수 있습니다.')).toBeTruthy();
    expect(router.state.location.pathname).toBe('/me/account');
  });

  it('sends the password and leaves for the login page', async () => {
    let sent: unknown = null;
    server.use(http.delete('*/api/candidate/me', async ({ request }) => {
      sent = await request.json();
      return HttpResponse.json({ status: 'WITHDRAWN' }, { status: 202 });
    }));
    const { router } = renderRoutes(routes, { path: '/me/account', session: candidateSession });
    withdraw('secret');
    expect(await screen.findByText('login page')).toBeTruthy();
    expect(sent).toEqual({ password: 'secret' });
    expect(router.state.location.search).toBe('?withdrawn=1');
  });
});
