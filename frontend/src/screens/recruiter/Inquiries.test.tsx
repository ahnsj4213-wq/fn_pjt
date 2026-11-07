import { fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { recruiterSession, renderRoutes } from '@/test/render';
import Inquiries from './Inquiries';
import PostingForm from './PostingForm';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const THREADS = [
  { id: 't1', kind: 'APPLICANT', sender: '김지원', subject: '일정 문의', last_at: '2026-11-02T01:00:00Z', unread: true },
  { id: 't2', kind: 'OPERATOR', sender: '운영팀', subject: '기업 인증이 반려되었습니다', last_at: '2026-11-01T01:00:00Z', unread: false },
];
const DETAIL = { id: 't1', kind: 'APPLICANT', sender: '김지원', subject: '일정 문의',
  messages: [{ id: 'm1', mine: false, sender: '김지원', body: '결과는 언제 나오나요?', created_at: '2026-11-02T01:00:00Z' }] };

describe('문의 쪽지 (SC-R15)', () => {
  it('shows the unread dot, opens a conversation and sends a reply', async () => {
    let sent: unknown = null;
    server.use(
      http.get('*/api/recruiter/inquiries', () => HttpResponse.json(THREADS)),
      http.get('*/api/recruiter/inquiries/unread-count', () => HttpResponse.json({ unread: 0 })),
      http.get('*/api/recruiter/inquiries/t1', () => HttpResponse.json(DETAIL)),
      http.post('*/api/recruiter/inquiries/t1/messages', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ ...DETAIL, messages: [...DETAIL.messages, { id: 'm2', mine: true, sender: '담당자', body: '다음 주에 안내드립니다', created_at: '2026-11-02T02:00:00Z' }] }, { status: 201 });
      }),
    );
    renderRoutes([{ path: '/recruiter/inquiries', element: <Inquiries /> }], { path: '/recruiter/inquiries', session: recruiterSession });
    expect(await screen.findByLabelText('읽지 않음')).toBeTruthy();
    fireEvent.click(screen.getByText('일정 문의'));
    expect(await screen.findByText('결과는 언제 나오나요?')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '보내기' }));
    expect((await screen.findByTestId('toast')).textContent).toBe('보낼 내용을 입력해 주십시오');
    fireEvent.change(screen.getByLabelText('답장'), { target: { value: '다음 주에 안내드립니다' } });
    fireEvent.click(screen.getByRole('button', { name: '보내기' }));
    // The typed text is also on screen in the reply box, so wait for the request itself and for the sent message bubble.
    await waitFor(() => expect(sent).toEqual({ body: '다음 주에 안내드립니다' }));
    expect(await screen.findByText('쪽지를 보냈습니다')).toBeTruthy();
  });
});

describe('템플릿 불러오기 (SC-R05)', () => {
  it('fills the form from a basic template and keeps the deadline', async () => {
    renderRoutes([{ path: '/recruiter/postings/new', element: <PostingForm /> }], { path: '/recruiter/postings/new', session: recruiterSession });
    const deadline = (await screen.findByLabelText('마감일')) as HTMLInputElement;
    const before = deadline.value;
    fireEvent.click(screen.getByRole('button', { name: '템플릿 불러오기' }));
    fireEvent.click(await screen.findByRole('tab', { name: '기본 템플릿' }));
    fireEvent.click(screen.getByRole('button', { name: /데이터·AI/ }));
    await waitFor(() => expect((screen.getByLabelText(/주요 업무/) as HTMLTextAreaElement).value).toContain('데이터 수집·정제 파이프라인 구축'));
    expect((screen.getByLabelText('직무 카테고리') as HTMLSelectElement).value).toBe('DATA_AI');
    expect((screen.getByLabelText('마감일') as HTMLInputElement).value).toBe(before);
    expect((await screen.findByTestId('toast')).textContent).toBe('템플릿을 불러왔습니다');
  });

  it('says so when no template is saved', async () => {
    server.use(http.get('*/api/recruiter/posting-templates', () => HttpResponse.json([])));
    renderRoutes([{ path: '/recruiter/postings/new', element: <PostingForm /> }], { path: '/recruiter/postings/new', session: recruiterSession });
    fireEvent.click(await screen.findByRole('button', { name: '템플릿 불러오기' }));
    fireEvent.click(await screen.findByRole('tab', { name: '저장한 템플릿' }));
    expect(await screen.findByText(/저장한 템플릿이 없습니다/)).toBeTruthy();
  });
});
