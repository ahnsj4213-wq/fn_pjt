import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { recruiterSession, renderRoutes } from '@/test/render';
import { NotificationBell } from './NotificationBell';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const note = (over: object) => ({ id: 'n1', kind: 'DEADLINE', text: '「백엔드」 공고가 내일 마감됩니다', created_at: '2026-11-01T01:00:00Z', unread: true,
  application_id: null, posting_id: 'p1', posting_open: true, done_text: null, ...over });
const routes = [{ path: '/', element: <NotificationBell /> }];

describe('알림 창 (SC-R12)', () => {
  it('shows the count, handles a deadline alert and marks everything read when closed', async () => {
    let extended = false; let read = false;
    server.use(
      http.get('*/api/recruiter/notifications', () => HttpResponse.json({ unread: 2, items: [
        note({}), note({ id: 'n2', kind: 'APPLY', text: '김지원님이 면접을 마쳤습니다', application_id: 'a1', posting_open: null }),
        note({ id: 'n3', kind: 'DEADLINE', text: '「프론트」 공고가 내일 마감됩니다', posting_open: false, unread: false })] })),
      http.post('*/api/recruiter/notifications/n1/extend', () => { extended = true; return HttpResponse.json({ done_text: '마감일을 11.9로 늘렸습니다' }); }),
      http.post('*/api/recruiter/notifications/read', () => { read = true; return new HttpResponse(null, { status: 204 }); }),
    );
    renderRoutes(routes, { path: '/', session: recruiterSession });
    const bell = await screen.findByRole('button', { name: '알림 2건' });
    fireEvent.click(bell);
    const win = within(await screen.findByRole('dialog', { name: '알림' }));
    expect(win.getByText('김지원님이 면접을 마쳤습니다')).toBeTruthy();
    expect(win.getByText('이미 마감된 공고입니다')).toBeTruthy();
    expect(win.getByRole('button', { name: '응시자 보기' })).toBeTruthy();
    fireEvent.click(win.getAllByRole('button', { name: '7일 연장' })[0]);
    await waitFor(() => expect(extended).toBe(true));

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(read).toBe(true));
    expect(screen.queryByRole('dialog', { name: '알림' })).toBeNull();
  });

  it('says so when there is nothing new', async () => {
    server.use(http.get('*/api/recruiter/notifications', () => HttpResponse.json({ unread: 0, items: [] })));
    renderRoutes(routes, { path: '/', session: recruiterSession });
    fireEvent.click(await screen.findByRole('button', { name: '알림' }));
    expect(await screen.findByText('새 알림이 없습니다')).toBeTruthy();
  });
});
