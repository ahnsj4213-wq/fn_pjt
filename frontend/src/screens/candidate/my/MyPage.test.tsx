import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { candidateSession, renderRoutes } from '@/test/render';
import { fromFlat, toFlat } from '../resume/flatResume';
import { MyApplications } from '../MyApplications';
import { MyResume } from '../MyResume';
import { MyPageLayout } from './MyPageLayout';
import { MyProfile } from './MyProfile';
import { MySchedule } from './MySchedule';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const routes = [{
  path: '/me', element: <MyPageLayout />, children: [
    { path: 'applications', element: <MyApplications /> },
    { path: 'resume', element: <MyResume /> },
    { path: 'profile', element: <MyProfile /> },
    { path: 'schedule', element: <MySchedule /> },
  ],
}, { path: '/', element: <p>main page</p> }];

const APP = {
  posting: { id: 'p1', title: '프론트엔드 개발자', company_name: '넥스트웨이브', coding_enabled: false },
  applied_at: '2026-10-05T01:00:00Z', status_label: '', evaluation_label: null, decision: 'UNDECIDED', can_start: true,
  share_result: true,
};

describe('My Page (SC-A09~A11, SC-A15)', () => {
  it('shows the side menu and the application table of the wireframe', async () => {
    server.use(http.get('*/api/candidate/applications', () => HttpResponse.json([
      { ...APP, id: 'a1', attempt_status: 'SUBMITTED', answered_main_count: 5, main_count: 5,
        progress_label: '2차 면접 일정 확정', progress_kind: 'good' },
      { ...APP, id: 'a2', attempt_status: 'INTERRUPTED', answered_main_count: 3, main_count: 5,
        progress_label: '면접 중단', progress_kind: 'none' },
    ])));
    renderRoutes(routes, { path: '/me/applications', session: candidateSession });
    const nav = screen.getByRole('navigation', { name: '마이페이지 메뉴' });
    expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual(['지원 현황', '이력서', '프로필', '면접 일정']);
    expect(within(nav).getByRole('link', { name: '지원 현황' }).getAttribute('aria-current')).toBe('page');
    const table = await screen.findByTestId('my-applications');
    expect(within(table).getAllByText('10.05', { selector: 'td' })).toHaveLength(2);
    expect(within(table).getByText('중단 (질문 3/5)')).toBeTruthy();
    expect(within(table).getByText('2차 면접 일정 확정').className).toBe('pill good');
    expect(within(screen.getByTestId('action-a1')).getByRole('button').textContent).toBe('리포트 보기');
    expect(within(screen.getByTestId('action-a2')).getByRole('button').textContent).toBe('다시 지원');
    expect(screen.getByText('합격 여부는 기업이 결정하면 이곳과 메일로 알려 드립니다.')).toBeTruthy();
  });

  it('saves the profile and validates name and phone', async () => {
    let saved: unknown = null;
    server.use(
      http.get('*/api/candidate/profile', () => HttpResponse.json(
        { name: '김지원', email: 'jiwon@gmail.com', birth_date: '1999-03-14', gender: 'F', phone: '010-2345-6789' })),
      http.put('*/api/candidate/profile', async ({ request }) => {
        saved = await request.json();
        return HttpResponse.json({ ...(saved as object), email: 'jiwon@gmail.com' });
      }),
      http.get('*/api/auth/me', () => HttpResponse.json(candidateSession)),
      http.post('*/api/candidate/profile/password-link', () => HttpResponse.json({ email: 'jiwon@gmail.com' }, { status: 202 })),
    );
    renderRoutes(routes, { path: '/me/profile', session: candidateSession });
    const name = await screen.findByLabelText('이름');
    expect((screen.getByLabelText('아이디 (이메일)') as HTMLInputElement).readOnly).toBe(true);
    fireEvent.change(name, { target: { value: ' ' } });
    fireEvent.change(screen.getByLabelText('연락처'), { target: { value: '0101' } });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect(await screen.findByText('이름을 입력해 주십시오')).toBeTruthy();
    expect(screen.getByText('010-0000-0000 형식으로 입력해 주십시오')).toBeTruthy();
    fireEvent.change(name, { target: { value: '김지수' } });
    fireEvent.change(screen.getByLabelText('연락처'), { target: { value: '01099998888' } });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect(await screen.findByText('프로필을 저장했습니다')).toBeTruthy();
    expect(saved).toEqual({ name: '김지수', birth_date: '1999-03-14', gender: 'F', phone: '010-9999-8888' });
    fireEvent.click(screen.getByRole('button', { name: '비밀번호 변경' }));
    expect(await screen.findByText('jiwon@gmail.com로 비밀번호 변경 링크를 보냈습니다')).toBeTruthy();
  });

  it('lists interview schedules', async () => {
    server.use(http.get('*/api/candidate/schedules', () => HttpResponse.json([{
      id: 's1', application_id: 'a1', company_name: '넥스트웨이브', title: '넥스트웨이브 프론트엔드 개발자 2차 대면 면접',
      starts_at: '2026-10-24T01:30:00Z', place: '서울 강남 본사 7층 회의실', status: 'ACCEPTED',
    }])));
    renderRoutes(routes, { path: '/me/schedule', session: candidateSession });
    const list = await screen.findByTestId('schedules');
    expect(list.textContent).toContain('10.24');
    expect(list.textContent).toContain('10:30');
    expect(within(list).getByText('수락함').className).toBe('pill good');
    expect(list.textContent).toContain('서울 강남 본사 7층 회의실');
  });

  it('fills the resume form from an uploaded file and saves it with the file', async () => {
    let body: { content: Record<string, unknown>; file_id: string } | null = null;
    server.use(
      http.get('*/api/candidate/resume', () => HttpResponse.json({ resume: null })),
      http.post('*/api/candidate/resume-files', () => HttpResponse.json({
        file_id: 'f1', page_count: null, text_extractable: true, extraction_status: 'DONE', method: 'TEXT',
        ocr_confidence: null, low_conf_fields: [], manual_input_required: false,
        fields: { name: '김지원', education: [{ school: '한국대학교', major: '컴퓨터공학' }],
          career: [{ company: '커머스랩', role: '백엔드 개발자', period: '2020-2024' }], projects: [{ title: '예약 서비스' }],
          skills: ['Python', 'FastAPI'], certificates: [] },
      }, { status: 201 })),
      http.put('*/api/candidate/resume', async ({ request }) => {
        body = await request.json() as typeof body;
        return HttpResponse.json({ ok: true });
      }),
    );
    renderRoutes(routes, { path: '/me/resume', session: candidateSession });
    expect(await screen.findByText('아직 등록한 이력서가 없습니다. 아래 양식을 채우거나 파일을 올려 주십시오.')).toBeTruthy();
    const file = new File(['%PDF-1.4'], 'cv.docx', { type: 'application/octet-stream' });
    fireEvent.change(await screen.findByLabelText('기존 이력서·자기소개서 파일 (선택)'), { target: { files: [file] } });
    expect(await screen.findByText('첨부됨: cv.docx')).toBeTruthy();
    expect((screen.getByLabelText('최종 학력') as HTMLInputElement).value).toBe('한국대학교 컴퓨터공학');
    expect((screen.getByLabelText('보유 기술') as HTMLInputElement).value).toBe('Python, FastAPI');
    fireEvent.change(screen.getByLabelText('희망 직무'), { target: { value: 'DEV' } });
    fireEvent.change(screen.getByLabelText('경력'), { target: { value: 'Y3_5' } });
    fireEvent.click(screen.getByRole('button', { name: '이력서 등록' }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body!.file_id).toBe('f1');
    expect(body!.content).toMatchObject({ name: '김지원', desired_job: 'DEV', experience_level: 'Y3_5',
      education: [{ school: '한국대학교', major: '컴퓨터공학' }], career: [{ company: '커머스랩' }] });
  });

  it('round-trips the flat form: untouched boxes keep their rows, edited ones are parsed', () => {
    const base = { name: '김지원', education: [{ school: '한국대학교', major: '컴퓨터공학' }],
      career: [{ company: '커머스랩', role: '백엔드', period: '2020-2024', description: '주문 API' }],
      projects: [{ title: '예약 서비스', description: 'FastAPI' }], skills: ['Python'], certificates: [] };
    const flat = toFlat(base);
    expect(flat.proj).toBe('경력 · 커머스랩 / 백엔드 / 2020-2024: 주문 API\n예약 서비스: FastAPI');
    expect(fromFlat(flat, base)).toMatchObject({ education: base.education, career: base.career, projects: base.projects });
    const edited = fromFlat({ ...flat, proj: `${flat.proj}\n쇼핑몰: Django`, skill: 'Python, Go' }, base);
    expect(edited.career).toEqual([{ company: '커머스랩', role: '백엔드', period: '2020-2024', description: '주문 API' }]);
    expect(edited.projects.map((p) => p.title)).toEqual(['예약 서비스', '쇼핑몰']);
    expect(edited.skills).toEqual(['Python', 'Go']);
  });
});
