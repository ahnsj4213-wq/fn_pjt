import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { SessionOut } from '@/api/common/auth';
import { OperatorLayout } from '@/screens/operator/OperatorLayout';
import { renderRoutes } from '@/test/render';
import Companies from './Companies';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const OP: SessionOut = { user: { id: 'o1', role: 'OPERATOR', name: '운영자', company_id: null, company_name: null }, csrf_token: 't' };
const row = (over: object) => ({ company_id: 'c1', company_name: '개인메일기업', contact_name: '박지훈', email: 'me@gmail.com', biz_no: '123-45-67890',
  method: 'DOCUMENT', status: 'PENDING', evidence_name: '증빙.pdf', reject_reason: null, applied_at: '2026-11-01T00:00:00Z', ...over });
const routes = [{ path: '/operator', element: <OperatorLayout />, children: [{ index: true, element: <Companies /> }] }];

describe('운영자 · 기업 인증 (SC-O01)', () => {
  it('shows the counts, approves a pending company and asks for a reason before rejecting', async () => {
    let approved = false; let reason: string | null = null;
    server.use(
      http.get('*/api/operator/companies', () => HttpResponse.json({ counts: { pending: 1, auto: 1, approved: 0, rejected: 1 }, items: [
        row({}), row({ company_id: 'c2', company_name: '회사메일기업', method: 'DOMAIN_MAIL', status: 'APPROVED', evidence_name: null }),
        row({ company_id: 'c3', company_name: '반려기업', status: 'REJECTED', reject_reason: '서류가 흐립니다' })] })),
      http.post('*/api/operator/companies/c1/approve', () => { approved = true; return HttpResponse.json({ status: 'APPROVED' }); }),
      http.post('*/api/operator/companies/c1/reject', async ({ request }) => { reason = ((await request.json()) as { reason: string }).reason; return HttpResponse.json({ status: 'REJECTED' }); }),
    );
    renderRoutes(routes, { path: '/operator', session: OP });
    expect(await screen.findByText('개인메일기업')).toBeTruthy();
    expect(screen.getByTestId('counts').textContent).toContain('검토 필요 1건');
    expect(screen.getAllByText('자동 승인', { selector: 'span.pill' })).toHaveLength(2);
    expect(screen.getByText('서류가 흐립니다')).toBeTruthy();
    expect(screen.getAllByRole('link', { name: '증빙.pdf' })[0].getAttribute('href')).toBe('/api/operator/companies/c1/evidence');

    fireEvent.click(screen.getByRole('button', { name: '반려' }));
    fireEvent.click(screen.getByRole('button', { name: '반려 확정' }));
    expect(await screen.findByText('반려 사유를 입력해 주십시오')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('반려 사유'), { target: { value: '서류 확인 불가' } });
    fireEvent.click(screen.getByRole('button', { name: '반려 확정' }));
    await waitFor(() => expect(reason).toBe('서류 확인 불가'));

    fireEvent.click(screen.getByRole('button', { name: '승인' }));
    await waitFor(() => expect(approved).toBe(true));
  });
});
