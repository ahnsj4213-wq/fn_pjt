import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import { fetchMyApplications, type CandidateApplication } from '@/api/candidate';
import { DECISION_LABEL } from '@/shared/copy/fixed';
import { formatDate } from '@/shared/format';
import { Page, uiStyles as s } from '@/shared/ui/components';
import { Badge, EmptyState, QueryState } from '@/shared/ui/extra';

const enc = encodeURIComponent;

/** The one action a candidate can take next for an application (7.1). */
function nextAction(a: CandidateApplication): { to: string; label: string } | null {
  const base = `/applications/${enc(a.id)}`;
  switch (a.attempt_status) {
    case 'READY':
    case 'PREPARING':
      return a.can_start ? { to: `${base}/guide`, label: 'AI 면접 보기' } : null;
    case 'INTERVIEWING':
    case 'INTERRUPTED': // the interview screen reads interrupted_from and moves on to coding when needed
      return a.can_start ? { to: `${base}/interview`, label: '면접 이어서 하기' } : null;
    case 'CODING':
      return { to: `${base}/coding`, label: '코딩테스트 이어서 하기' };
    case 'SUBMITTED':
      return { to: `/me/applications/${enc(a.id)}/result`, label: '결과 보기' };
    default:
      return null;
  }
}

/** 지원 현황 (7.1, RPT-01~03, ADM-04, INT-12, INT-13): status, decision and the next step. Scores are never shown (SEC-04). */
export function MyApplications() {
  const list = useQuery({ queryKey: ['my-applications'], queryFn: fetchMyApplications });
  return (
    <Page title="지원 현황">
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (list.data!.length === 0 ? (
          <EmptyState>아직 지원한 공고가 없습니다. <Link to="/postings">채용공고 보기</Link></EmptyState>
        ) : (
          <table className={s.table} data-testid="my-applications">
            <thead>
              <tr><th>공고</th><th>지원일</th><th>응시 상태</th><th>결과</th><th /></tr>
            </thead>
            <tbody>
              {list.data!.map((a) => {
                const act = nextAction(a);
                return (
                  <tr key={a.id}>
                    <td><strong>{a.posting.title}</strong><br /><span className={s.muted}>{a.posting.company_name}</span></td>
                    <td>{formatDate(a.applied_at)}</td>
                    <td><Badge kind={a.attempt_status === 'INTERRUPTED' ? 'warn' : undefined}>{a.status_label}</Badge></td>
                    <td>{DECISION_LABEL[a.decision]}</td>
                    <td>{act && <Link to={act.to} data-testid={`action-${a.id}`}>{act.label}</Link>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ))}
      </QueryState>
    </Page>
  );
}
