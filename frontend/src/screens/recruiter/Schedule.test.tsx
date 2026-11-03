import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { ScheduleResponse } from '@/screens/common/ScheduleResponse';
import { todayKst } from '@/shared/kst';
import { recruiterSession, renderRoutes } from '@/test/render';
import { MailModal } from './MailModal';
import Schedule from './Schedule';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const noon = `${todayKst()}T03:00:00Z`; // 12:00 in Korea, today

describe('일정 관리 (SC-R11)', () => {
  it('shows deadlines read-only, adds an entry and asks for the content first', async () => {
    let added: Record<string, unknown> | null = null;
    server.use(
      http.get('*/api/recruiter/schedule', () => HttpResponse.json([
        { id: 'deadline:p1', starts_at: noon, kind: 'DEADLINE', content: '백엔드 공고 마감', place: null, auto: true, editable: false, status: null, application_id: null, candidate_name: null },
        { id: 's1', starts_at: noon, kind: 'INTERVIEW', content: '2차 면접', place: '본사', auto: true, editable: true, status: 'PROPOSED', application_id: 'a1', candidate_name: '김지원' }])),
      http.post('*/api/recruiter/schedule', async ({ request }) => { added = await request.json() as Record<string, unknown>; return HttpResponse.json({ ...added, id: 's2' }, { status: 201 }); }),
    );
    renderRoutes([{ path: '/recruiter/schedule', element: <Schedule /> }], { path: '/recruiter/schedule', session: recruiterSession });
    const cal = await screen.findByTestId('calendar');
    fireEvent.click(within(cal).getByText(/백엔드 공고 마감/));
    expect((await screen.findByTestId('toast')).textContent).toBe('공고 마감일은 공고 관리에서 공고를 수정하면 바뀝니다');

    fireEvent.click(screen.getByRole('button', { name: '일정 추가' }));
    expect((await screen.findAllByTestId('toast')).at(-1)!.textContent).toBe('내용을 입력해 주십시오');
    expect(added).toBeNull();
    fireEvent.change(screen.getByLabelText('내용'), { target: { value: '채용 회의' } });
    fireEvent.change(screen.getByLabelText('종류'), { target: { value: 'INTERNAL' } });
    fireEvent.click(screen.getByRole('button', { name: '일정 추가' }));
    await waitFor(() => expect(added).toMatchObject({ kind: 'INTERNAL', content: '채용 회의' }));

    fireEvent.click(within(cal).getByText(/2차 면접/));
    expect(await screen.findByText('일정 수정')).toBeTruthy();
    expect(screen.getAllByText('수락 대기').length).toBeGreaterThan(1); // month list and the answer line of the form
  });
});

describe('메일 보내기 (SC-R14)', () => {
  it('fills the text by kind and sends the interview date', async () => {
    let sent: Record<string, unknown> | null = null;
    server.use(http.post('*/api/recruiter/applications/a1/mail', async ({ request }) => {
      sent = await request.json() as Record<string, unknown>;
      return HttpResponse.json({ mail_id: 'm1', schedule_id: 's1' }, { status: 201 });
    }));
    renderRoutes([{ path: '/x', element: <MailModal applicationId="a1" name="김지원" email="k@mail.example" company="넥스트웨이브" decision="UNDECIDED" onClose={() => {}} /> }],
      { path: '/x', session: recruiterSession });
    const dlg = within(await screen.findByRole('dialog'));
    expect((dlg.getByLabelText('제목') as HTMLInputElement).value).toBe('2차 면접 일정 안내');
    expect(dlg.getByLabelText('메일 미리보기').textContent).toContain('[넥스트웨이브] 2차 면접 일정 안내');
    expect(dlg.getByText('일정 수락')).toBeTruthy();

    fireEvent.change(dlg.getByLabelText('제목'), { target: { value: ' ' } });
    fireEvent.click(dlg.getByRole('button', { name: '보내기' }));
    expect(await dlg.findByText('제목을 입력해 주십시오')).toBeTruthy();
    fireEvent.change(dlg.getByLabelText('제목'), { target: { value: '2차 면접 일정 안내' } });
    fireEvent.change(dlg.getByLabelText('장소'), { target: { value: '본사 5층' } });
    fireEvent.click(dlg.getByRole('button', { name: '보내기' }));
    await waitFor(() => expect(sent).toMatchObject({ kind: 'INTERVIEW', subject: '2차 면접 일정 안내', place: '본사 5층' }));
    expect(String(sent!.starts_at)).toMatch(/T05:00:00\.000Z$/); // 14:00 in Korea

    fireEvent.change(dlg.getByLabelText('메일 종류'), { target: { value: 'RESULT' } });
    expect(dlg.queryByLabelText('장소')).toBeNull();
    expect((dlg.getByLabelText('내용') as HTMLTextAreaElement).value).toContain('검토하고 있습니다');
  });
});

describe('면접 일정 응답 (메일 링크)', () => {
  it('lets the applicant accept without logging in', async () => {
    let answer: string | null = null;
    server.use(
      http.get('*/api/schedule-response', () => HttpResponse.json({ company_name: '넥스트웨이브', title: '2차 면접', starts_at: noon, place: '본사', status: 'PROPOSED' })),
      http.post('*/api/schedule-response', async ({ request }) => {
        answer = ((await request.json()) as { answer: string }).answer;
        return HttpResponse.json({ company_name: '넥스트웨이브', title: '2차 면접', starts_at: noon, place: '본사', status: 'ACCEPTED' });
      }),
    );
    renderRoutes([{ path: '/schedule-response', element: <ScheduleResponse /> }], { path: '/schedule-response?token=abc', session: null });
    fireEvent.click(await screen.findByRole('button', { name: '일정 수락' }));
    await waitFor(() => expect(answer).toBe('ACCEPT'));
  });
});
