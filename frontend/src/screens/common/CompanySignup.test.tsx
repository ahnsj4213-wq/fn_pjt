import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { renderRoutes } from '@/test/render';
import { formatBiz, isFreeMail } from './CompanySignupModal';
import { Login } from './Login';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const routes = [{ path: '/login', element: <Login /> }, { path: '/recruiter', element: <p>recruiter home</p> }];
const dlg = () => within([...document.querySelectorAll('dialog')].at(-1) as HTMLElement);
const SESSION = { user: { id: 'u1', role: 'RECRUITER', name: '박지훈', company_id: 'c1', company_name: '넥스트웨이브' }, csrf_token: 't' };

function open() {
  renderRoutes(routes, { path: '/login?tab=recruiter', session: null });
  fireEvent.click(screen.getByRole('button', { name: '기업회원 가입' }));
}
const type = (label: string, value: string) => fireEvent.change(dlg().getByLabelText(label), { target: { value } });

describe('기업회원 가입 (SC-R01)', () => {
  it('knows free mail domains and formats the business number', () => {
    expect(isFreeMail('me@Gmail.com')).toBe(true);
    expect(isFreeMail('hr@nextwave.example')).toBe(false);
    expect(formatBiz('1234567890')).toBe('123-45-67890');
  });

  it('asks for the mail code first and shows the document field only for personal mail', async () => {
    server.use(http.post('*/api/auth/company-signup/code', () => HttpResponse.json({ free_mail: false, expires_in_sec: 600 }, { status: 202 })));
    open();
    expect(dlg().queryByLabelText('인증번호')).toBeNull();
    type('회사 이메일 (아이디)', 'hr@nextwave.example');
    expect(dlg().getByText('회사 도메인 메일로 인증하면 가입하자마자 승인됩니다.')).toBeTruthy();
    fireEvent.click(dlg().getByRole('button', { name: '인증번호 받기' }));
    expect(await dlg().findByLabelText('인증번호')).toBeTruthy();
    expect(dlg().getByText('hr@nextwave.example로 인증번호를 보냈습니다.')).toBeTruthy();
    type('회사 이메일 (아이디)', 'hr@gmail.com'); // editing the address hides the code field again
    expect(dlg().queryByLabelText('인증번호')).toBeNull();
    expect(dlg().getByLabelText('재직증명서 또는 사업자등록증')).toBeTruthy();
    expect(dlg().getByText(/개인 메일은 자동 승인되지 않습니다/)).toBeTruthy();
  });

  it('validates, signs up with a company mail and goes to the recruiter home', async () => {
    let sent: Record<string, string> | null = null;
    server.use(
      http.post('*/api/auth/company-signup/code', () => HttpResponse.json({ free_mail: false, expires_in_sec: 600 }, { status: 202 })),
      http.post('*/api/auth/company-signup', async ({ request }) => { sent = await request.json() as Record<string, string>; return HttpResponse.json(SESSION, { status: 201 }); }),
    );
    open();
    fireEvent.click(dlg().getByRole('button', { name: '가입하기' }));
    expect(await dlg().findByText('회사명을 입력해 주십시오')).toBeTruthy();
    expect(dlg().getByText('000-00-00000 형식으로 입력해 주십시오')).toBeTruthy();
    expect(dlg().getByText('회사 이메일을 입력해 주십시오')).toBeTruthy();
    type('회사명', '넥스트웨이브'); type('사업자등록번호', '1234567890'); type('담당자 이름', '박지훈');
    type('회사 이메일 (아이디)', 'hr@nextwave.example');
    fireEvent.click(dlg().getByRole('button', { name: '인증번호 받기' }));
    type('인증번호', await dlg().findByLabelText('인증번호').then(() => '123456'));
    type('비밀번호', 'password12'); type('비밀번호 확인', 'password13');
    fireEvent.click(dlg().getByRole('button', { name: '가입하기' }));
    expect(await dlg().findByText('비밀번호가 서로 다릅니다')).toBeTruthy();
    type('비밀번호 확인', 'password12');
    fireEvent.click(dlg().getByRole('button', { name: '가입하기' }));
    expect(await screen.findByText('recruiter home')).toBeTruthy();
    await waitFor(() => expect(sent).toMatchObject({ company_name: '넥스트웨이브', biz_no: '123-45-67890', code: '123456',
      email: 'hr@nextwave.example' }));
  });
});
