import { useQuery } from '@tanstack/react-query';
import { useNavigate, useSearchParams } from 'react-router';
import { fetchApplications, fetchRecruiterPostings, type ApplicationRow } from '@/api/recruiter';
import { formatDate } from '@/shared/format';
import { QueryState } from '@/shared/ui/extra';

export const DECISION: Record<string, [string, string]> = { UNDECIDED: ['검토 전', 'none'], PASS: ['합격', 'good'], HOLD: ['보류', 'hold'], FAIL: ['불합격', 'bad'] };
export const AI_REF: Record<string, [string, string]> = { PASS: ['합격 추천', 'good'], HOLD: ['보류', 'hold'], FAIL: ['불합격 추천', 'bad'] };

function Progress({ r }: { r: ApplicationRow }) {
  const done = r.attempt_status === 'SUBMITTED' || r.attempt_status === 'EXPIRED';
  const n = r.answered_main_count;
  return <span className="mini">{done ? '완료' : r.attempt_status === 'READY' ? '시작 전' : '진행 중'} · 답변 {n}개</span>;
}

/** SC-R08 기업 관리 · 응시자 목록. */
export default function Applications() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const posting = params.get('posting') ?? '';
  const decision = params.get('decision') ?? '';
  const posts = useQuery({ queryKey: ['recruiter', 'postings'], queryFn: () => fetchRecruiterPostings() });
  const list = useQuery({ queryKey: ['recruiter', 'applications', posting, decision],
    queryFn: () => fetchApplications({ posting_id: posting || undefined, decision: decision || undefined, size: 50 }) });
  const set = (k: string) => (e: { target: { value: string } }) => {
    const next = new URLSearchParams(params);
    if (e.target.value) next.set(k, e.target.value); else next.delete(k);
    setParams(next, { replace: true });
  };
  return (
    <>
      <div className="ph"><div><h1>응시자 목록</h1><p>결과 공유에 동의하고 우리 공고로 AI 면접을 본 응시자입니다.</p></div></div>
      <div className="filters">
        <select aria-label="공고" value={posting} onChange={set('posting')}>
          <option value="">모든 공고</option>
          {posts.data?.filter((p) => p.applicant_count > 0).map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
        </select>
        <select aria-label="담당자 결정" value={decision} onChange={set('decision')}>
          <option value="">담당자 결정 전체</option><option value="UNDECIDED">검토 전</option><option value="PASS">합격</option><option value="HOLD">보류</option><option value="FAIL">불합격</option>
        </select>
        <span className="hint">{list.data ? `${list.data.total}명` : ''}</span>
      </div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (
          <div className="card" style={{ padding: 0 }}><div className="tblw"><table className="t">
            <thead><tr><th>이름</th><th>공고</th><th>응시일</th><th>면접 진행</th><th className="num">AI 종합 점수</th><th>AI 예상 합격</th><th>담당자 결정</th><th>평가 리포트</th></tr></thead>
            <tbody>
              {list.data!.items.length === 0 && <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--muted)', padding: 28 }}>조건에 맞는 응시자가 없습니다</td></tr>}
              {list.data!.items.map((r) => {
                const [dl, dk] = DECISION[r.decision];
                const open = () => nav(`/recruiter/applications/${r.id}`);
                const ai = r.ai_reference ? AI_REF[r.ai_reference] : null;
                return (
                  <tr key={r.id} className="click" tabIndex={0} onClick={open} onKeyDown={(e) => e.target === e.currentTarget && e.key === 'Enter' && open()}>
                    <td><b>{r.candidate_name}</b></td><td>{r.posting.title}</td><td>{formatDate(r.applied_at)}</td><td><Progress r={r} /></td>
                    <td className="num">{r.total_score ? Math.round(Number(r.total_score)) : '—'}</td>
                    <td>{ai ? <span className={`pill ${ai[1]}`}>{ai[0]}</span> : <span className="hint">점수 없음</span>}</td>
                    <td><span className={`pill ${dk}`}>{dl}</span></td>
                    <td><button className="btn btn-sm" type="button" disabled={r.eval_status !== 'DONE'}
                      title={r.eval_status !== 'DONE' ? '평가가 끝나지 않아 리포트가 없습니다' : undefined}
                      onClick={(e) => { e.stopPropagation(); nav(`/recruiter/applications/${r.id}/report`); }}>리포트</button></td>
                  </tr>
                );
              })}
            </tbody></table></div></div>
        )}
      </QueryState>
      <p className="fine" style={{ margin: 0 }}>AI 예상 합격은 점수로 낸 참고값이고, 최종 합격 여부는 담당자 결정 칸에 담당자가 정한 값입니다.</p>
    </>
  );
}
