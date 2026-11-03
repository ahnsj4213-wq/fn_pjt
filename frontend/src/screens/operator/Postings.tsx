import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchOperatorPostings, hidePosting, showPosting, suspendCompany, unsuspendCompany, type OperatorPosting } from '@/api/operator';
import { describeError } from '@/shared/http/errors';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';

const JOB: Record<string, string> = { DEV: '개발', DATA_AI: '데이터·AI', DESIGN: '디자인', MARKETING: '마케팅', MGMT: '경영지원' };

/** SC-O02 운영자 · 공고 관리. */
export default function Postings() {
  const toast = useToast();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ['operator', 'postings'], queryFn: fetchOperatorPostings });
  const done = () => qc.invalidateQueries({ queryKey: ['operator', 'postings'] });
  const onError = (e: unknown) => toast(describeError(e).message);

  const toggle = useMutation({
    mutationFn: (p: OperatorPosting) => (p.hidden ? showPosting(p.id) : hidePosting(p.id)).then(() => p),
    onSuccess: (p) => { toast(p.hidden ? '공고를 다시 보이게 했습니다' : '공고를 숨겼습니다'); void done(); }, onError,
  });
  const suspend = useMutation({
    mutationFn: (p: OperatorPosting) => (p.company_suspended ? unsuspendCompany(p.company_id) : suspendCompany(p.company_id)).then(() => p),
    onSuccess: (p) => {
      toast(p.company_suspended ? `${p.company_name} 정지를 풀었습니다` : `${p.company_name} 기업회원을 정지했습니다. 공고가 모두 내려갑니다`);
      void done();
    }, onError,
  });

  return (
    <>
      <div className="ph"><div><h1>공고 관리</h1>
        <p>문제가 있는 공고를 숨기고, 필요하면 기업회원을 정지합니다. 숨긴 공고는 메인 화면에서 바로 사라집니다.</p></div></div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (
          <div className="card" style={{ padding: 0 }}><div className="tblw"><table className="t">
            <thead><tr><th>공고</th><th>회사</th><th>직무</th><th>상태</th><th>관리</th></tr></thead>
            <tbody>
              {list.data!.length === 0 && <tr><td colSpan={5} style={{ textAlign: 'center', color: 'var(--muted)', padding: 28 }}>게시된 공고가 없습니다</td></tr>}
              {list.data!.map((p) => (
                <tr key={p.id}>
                  <td><b>{p.title || '(제목 없음)'}</b></td>
                  <td>{p.company_name}{p.company_suspended && <> <span className="pill bad">정지</span></>}</td>
                  <td>{p.job_family ? JOB[p.job_family] : '—'}</td>
                  <td><span className={`pill ${p.hidden ? 'hold' : p.status === 'OPEN' ? 'good' : 'none'}`}>{p.hidden ? '숨김' : p.status === 'OPEN' ? '게시 중' : '마감'}</span></td>
                  <td><div style={{ display: 'flex', gap: 6 }}>
                    <button className="btn" type="button" disabled={toggle.isPending} onClick={() => toggle.mutate(p)}>{p.hidden ? '다시 보이기' : '숨기기'}</button>
                    <button className="btn" type="button" disabled={suspend.isPending} onClick={() => suspend.mutate(p)}>{p.company_suspended ? '기업 정지 해제' : '기업 정지'}</button>
                  </div></td>
                </tr>
              ))}
            </tbody></table></div></div>
        )}
      </QueryState>
    </>
  );
}
