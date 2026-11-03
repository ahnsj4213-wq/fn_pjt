import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { MyReport, ReportAnswer } from '@/api/candidate/report';
import { candidateSession, recruiterSession, renderRoutes } from '@/test/render';
import { Report } from './Report';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }));
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

const TEXT = '컴퓨터공학을 전공한 김지원입니다. 아직 실무 경험은 부족하지만 빠르게 배우는 편입니다.';
const answer = (over: Partial<ReportAnswer> = {}): ReportAnswer => ({
  text: TEXT, seconds: 52, input_mode: 'VOICE', no_answer: false,
  highlights: [{ start: 0, end: 9, kind: 'good', criterion: '전문성' }], negative: ['부족하지만'],
  evidence: [{ criterion: 'EXPERTISE', label: '전문성', kind: 'good', quote: '컴퓨터공학을 전공한', comment: '전공을 첫 문장에서 밝혔습니다' }],
  criteria: [{ key: 'EXPERTISE', label: '전문성', score: 80 }, { key: 'LOGIC', label: '논리성', score: 60 }],
  score: 70, good: '전공과 역할을 짧게 정리했습니다', fix: '마지막 문장에 사례를 넣어 보십시오', code: null, code_language: null, ...over,
});
const REPORT: MyReport = {
  application_id: 'a1', posting_id: 'p1', posting_title: '백엔드 개발자 (신입)', company_name: '넥스트웨이브', candidate_name: '김지원',
  interviewed_at: '2026-10-19T05:00:00Z', main_count: 2, followup_count: 1, avg_answer_sec: 49.8, total: 76.4, grade: '양호', grade_kind: 'good',
  criteria: [{ key: 'EXPERTISE', label: '전문성', desc: '직무 지식', score: 80, weight: 35 }, { key: 'LOGIC', label: '논리성', desc: '이유와 사례', score: 70, weight: 25 }],
  coding: null, summary: '직무 경험을 수치로 설명했습니다.',
  strengths: [{ rank: 1, text: '경험을 수치로 설명했습니다', refs: [2] }], improvements: [{ rank: 1, text: '마무리가 흐려졌습니다', refs: [1] }],
  chips: [{ label: 'Q1', no: 1, score: 70, followup: false }, { label: 'Q1-1', no: 1, score: 66, followup: true }, { label: 'Q2', no: 2, score: 80, followup: false }],
  questions: [
    { no: 1, label: 'Q1', type_label: '인성', text: '자기소개를 해 주십시오.', limit_sec: 60, answer: answer(),
      followup: { label: 'Q1-1', text: '구체적인 사례를 말씀해 주십시오.', why: '답변이 모호해서', answer: answer({ score: 66, highlights: [], negative: [], evidence: [] }) } },
    { no: 2, label: 'Q2', type_label: '지원 동기', text: '지원 동기는 무엇입니까?', limit_sec: 60, answer: answer({ no_answer: true, text: null, score: 20 }), followup: null },
  ],
  areas: [{ key: 'EXPERTISE', label: '전문성', desc: '직무 지식', score: 80, items: [{ label: 'Q1', score: 80, comment: '구체적입니다' }] }],
  attitude: { negative: ['부족하지만'], times: [{ label: 'Q1', sec: 52, limit: 60 }], limit_sec: 60 },
  proctor: { leave_count: 1, away_ms: 4000 }, media: { audio_deleted: false, audio_auto_delete_days: 30 }, share_result: true,
  notice: 'AI 분석 결과는 연습을 돕기 위한 참고 자료입니다.',
};
const routes = [{ path: '/me/applications/:aid/report', element: <Report /> }];
const open = () => renderRoutes(routes, { path: '/me/applications/a1/report', session: candidateSession });

describe('결과 리포트 (SC-A08)', () => {
  it('shows the score, the points with links, the marked answer and the follow-up, with no decision needed', async () => {
    server.use(http.get('*/api/candidate/applications/a1/report', () => HttpResponse.json(REPORT)));
    open();
    expect(await screen.findByRole('heading', { name: '백엔드 개발자 (신입)' })).toBeTruthy();
    expect(screen.getByText('76.4')).toBeTruthy();
    expect(screen.getAllByText('양호').length).toBeGreaterThan(0);
    expect(screen.getByText(/질문 2개 \+ 꼬리질문 1개 · 평균 답변 49.8초/)).toBeTruthy();
    expect(screen.getByText('×35%')).toBeTruthy();
    expect(screen.getByText('경험을 수치로 설명했습니다')).toBeTruthy();

    const q1 = document.getElementById('rq-1')!;
    const marks = [...q1.querySelectorAll('mark')].map((m) => [m.className, m.textContent]);
    expect(marks).toContainEqual(['pos', TEXT.slice(0, 9)]);
    expect(marks).toContainEqual(['neg', '부족하지만']);
    expect(within(q1).getByText(/AI가 이 질문을 한 이유/)).toBeTruthy();
    expect(within(q1).getByText('답변이 모호해서')).toBeTruthy();
    expect(screen.getByText('답변이 없었습니다.')).toBeTruthy();
    expect(screen.getByText('AI 분석 결과는 연습을 돕기 위한 참고 자료입니다.')).toBeTruthy();
    expect(screen.queryByText(/합격|불합격/)).toBeNull(); // the decision is another screen
  });

  it('opens the question a link points to', async () => {
    server.use(http.get('*/api/candidate/applications/a1/report', () => HttpResponse.json(REPORT)));
    open();
    await screen.findByRole('heading', { name: '백엔드 개발자 (신입)' });
    const q2 = document.getElementById('rq-2') as HTMLDetailsElement;
    expect(q2.open).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '관련 질문 Q2 보기' }));
    expect(q2.open).toBe(true);
  });

  it('asks before deleting the audio and then says it is gone', async () => {
    let deleted = false;
    server.use(
      http.get('*/api/candidate/applications/a1/report', () => HttpResponse.json({ ...REPORT, media: { ...REPORT.media, audio_deleted: deleted } })),
      http.post('*/api/candidate/applications/a1/media/delete', () => { deleted = true; return new HttpResponse(null, { status: 204 }); }),
    );
    open();
    fireEvent.click(await screen.findByRole('button', { name: '영상·음성 지금 삭제' }));
    expect(screen.getByText('영상과 음성을 영구 삭제하시겠습니까?')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '영구 삭제' }));
    await waitFor(() => expect(deleted).toBe(true));
    expect(await screen.findByText('면접 영상과 음성 원본을 영구 삭제했습니다.')).toBeTruthy();
  });

  it('waits while the report is being made and then shows it', async () => {
    let ready = false;
    server.use(http.get('*/api/candidate/applications/a1/report', () => ready
      ? HttpResponse.json(REPORT)
      : HttpResponse.json({ error: { code: 'REPORT_NOT_READY', message: '리포트를 만들고 있습니다.', request_id: 'r' } }, { status: 409 })));
    open();
    expect(await screen.findByRole('heading', { name: '리포트를 만들고 있습니다' })).toBeTruthy();
    ready = true;
    expect(await screen.findByRole('heading', { name: '백엔드 개발자 (신입)' }, { timeout: 6000 })).toBeTruthy();
  }, 10000);
});

describe('결과 리포트 (채용담당자용, SC-R10)', () => {
  it('has the same content with the AI badge and without the applicant-only buttons and data card', async () => {
    server.use(http.get('*/api/recruiter/applications/a1/report-view', () => HttpResponse.json({ ...REPORT, ai_reference: 'PASS' })));
    renderRoutes([{ path: '/recruiter/applications/:aid/report', element: <Report mode="recruiter" /> }],
      { path: '/recruiter/applications/a1/report', session: recruiterSession });
    expect(await screen.findByRole('heading', { name: '백엔드 개발자 (신입)' })).toBeTruthy();
    expect(screen.getByText('76.4')).toBeTruthy();
    expect(screen.getByText(/AI 추천 · 담당자에게만 보입니다 · 합격 추천/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: '다시 면접 보기' })).toBeNull();
    expect(screen.queryByRole('button', { name: '다른 공고 보기' })).toBeNull();
    expect(screen.queryByText('내 면접 데이터')).toBeNull();
    expect(screen.getByText('‹ 응시자 상세')).toBeTruthy();
  });
});
