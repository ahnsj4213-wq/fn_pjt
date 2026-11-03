import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { fetchMyApplications, type CandidateApplication } from '@/api/candidate';
import { formatMonthDay } from '@/shared/format';
import { QueryState } from '@/shared/ui/extra';
import { InquiryModal } from './InquiryModal';

const enc = encodeURIComponent;
const STOPPED = new Set(['INTERRUPTED', 'EXPIRED']);

/** 면접 column: 완료, '중단 (질문 3/5)', or where the attempt stands. */
function interviewText(a: CandidateApplication): string {
  if (a.attempt_status === 'SUBMITTED') return '완료';
  if (STOPPED.has(a.attempt_status)) return `중단 (질문 ${a.answered_main_count}/${a.main_count ?? '-'})`;
  if (a.attempt_status === 'INTERVIEWING' || a.attempt_status === 'CODING') return '진행 중';
  return '응시 전';
}

/**
 * SC-A09 마이페이지 · 지원 현황 (RPT-01~03, INT-12): applications, progress and the report. Scores are never shown
 * (SEC-04). '다시 지원' continues a stopped attempt while the posting is open (INT-12: one attempt per application).
 */
export function MyApplications() {
  const navigate = useNavigate();
  const list = useQuery({ queryKey: ['my-applications'], queryFn: fetchMyApplications });
  const [asking, setAsking] = useState<CandidateApplication | null>(null);

  const action = (a: CandidateApplication) => {
    if (a.attempt_status === 'SUBMITTED') {
      return (
        <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
          <button className="btn" type="button" onClick={() => navigate(`/me/applications/${enc(a.id)}/report`)}>리포트 보기</button>
          {(a.decision === 'PASS' || a.decision === 'FAIL') && (
            <button className="btn" type="button" onClick={() => navigate(`/me/applications/${enc(a.id)}/result`)}>채용 결과 보기</button>
          )}
        </span>
      );
    }
    if (STOPPED.has(a.attempt_status)) {
      return (
        <button className="btn" type="button" disabled={!a.can_start}
          title={a.can_start ? undefined : '마감되었거나 다시 응시할 수 없는 지원입니다'}
          onClick={() => navigate(`/applications/${enc(a.id)}/interview`)}>다시 지원</button>
      );
    }
    if (a.attempt_status === 'CODING') {
      return <button className="btn" type="button" onClick={() => navigate(`/applications/${enc(a.id)}/coding`)}>코딩테스트 이어서 하기</button>;
    }
    if (a.attempt_status === 'INTERVIEWING') {
      return <button className="btn" type="button" onClick={() => navigate(`/applications/${enc(a.id)}/interview`)}>면접 이어서 하기</button>;
    }
    return a.can_start
      ? <button className="btn" type="button" onClick={() => navigate(`/postings/${enc(a.posting.id)}/apply`)}>AI 면접 보기</button>
      : null;
  };

  return (
    <>
      <div className="ph"><div><h1>지원 현황</h1><p>AI 면접으로 지원한 공고와 진행 상태입니다.</p></div></div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (
          <div className="card" style={{ padding: 0 }}>
            {list.data!.length === 0 ? (
              <p style={{ padding: 18, margin: 0 }}>아직 지원한 공고가 없습니다. <Link to="/">채용공고 보기</Link></p>
            ) : (
              <div className="tblw">
                <table className="t" data-testid="my-applications">
                  <thead><tr><th>공고</th><th>지원일</th><th>면접</th><th>진행 상태</th><th>결과 리포트</th></tr></thead>
                  <tbody>
                    {list.data!.map((a) => (
                      <tr key={a.id}>
                        <td><b>{a.posting.title}</b><br /><span className="hint">{a.posting.company_name}</span><br />
                          {a.share_result && <button type="button" className="link" onClick={() => setAsking(a)}>기업에 문의</button>}</td>
                        <td>{formatMonthDay(a.applied_at)}</td>
                        <td>{interviewText(a)}</td>
                        <td><span className={`pill ${a.progress_kind}`}>{a.progress_label}</span></td>
                        <td data-testid={`action-${a.id}`}>{action(a)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </QueryState>
      {asking && <InquiryModal applicationId={asking.id} title={asking.posting.title} onClose={() => setAsking(null)} />}
      <p className="fine" style={{ margin: 0 }}>합격 여부는 기업이 결정하면 이곳과 메일로 알려 드립니다.</p>
    </>
  );
}
