import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { closePosting, deletePosting, fetchRecruiterPostings, type PostingSummary } from '@/api/recruiter';
import { formatDate } from '@/shared/format';
import { describeError } from '@/shared/http/errors';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';

type Filter = 'all' | 'OPEN' | 'DRAFT' | 'CLOSED';
const FILTERS: [Filter, string][] = [['all', '전체'], ['OPEN', '진행 중'], ['DRAFT', '작성 중'], ['CLOSED', '마감']];
const PILL: Record<string, [string, string]> = { OPEN: ['게시 중', 'good'], DRAFT: ['작성 중', 'hold'], CLOSED: ['마감', 'none'] };
const JOB: Record<string, string> = { DEV: '개발', DATA_AI: '데이터·AI', DESIGN: '디자인', MARKETING: '마케팅', MGMT: '경영지원' };

/** SC-R03 기업 관리 · 공고 관리. */
export default function Postings() {
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>('all');
  const [deleting, setDeleting] = useState<string | null>(null);
  const list = useQuery({ queryKey: ['recruiter', 'postings'], queryFn: () => fetchRecruiterPostings() });
  const refresh = () => qc.invalidateQueries({ queryKey: ['recruiter'] });
  const onError = (e: unknown) => toast(describeError(e).message);
  const close = useMutation({ mutationFn: (p: PostingSummary) => closePosting(p.id),
    onSuccess: () => { toast('공고를 마감했습니다. 메인 화면에서 내려갑니다'); void refresh(); }, onError });
  const del = useMutation({ mutationFn: (p: PostingSummary) => deletePosting(p.id).then(() => p),
    onSuccess: (p) => { toast(`「${p.title ?? '제목 없음'}」 공고를 삭제했습니다`); setDeleting(null); void refresh(); },
    onError: (e) => { setDeleting(null); onError(e); } });

  return (
    <>
      <div className="ph"><div><h1>공고 관리</h1><p>게시한 공고는 메인 화면에 바로 보입니다. 마감일이 지나면 자동으로 마감됩니다.</p></div>
        <Link to="/recruiter/postings/new" className="btn btn-primary">새 공고 등록</Link></div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => {
          const all = list.data!;
          const shown = all.filter((p) => filter === 'all' || p.effective_status === filter);
          const count = (k: Filter) => (k === 'all' ? all.length : all.filter((p) => p.effective_status === k).length);
          return (
            <>
              <div className="chips" role="group" aria-label="공고 상태">
                {FILTERS.map(([k, l]) => <button key={k} className="chip" type="button" aria-pressed={filter === k} onClick={() => { setFilter(k); setDeleting(null); }}>
                  {l}<span className="n">{count(k)}</span></button>)}
              </div>
              <div className="card" style={{ padding: 0 }}><div className="tblw"><table className="t">
                <thead><tr><th>공고</th><th>직무</th><th>상태</th><th className="num">조회수</th><th className="num">응시자</th><th>마감</th><th>관리</th></tr></thead>
                <tbody>
                  {shown.length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: 'var(--muted)', padding: 28 }}>이 상태의 공고가 없습니다</td></tr>}
                  {shown.map((p) => {
                    const [label, kind] = PILL[p.effective_status];
                    return (
                      <tr key={p.id}>
                        <td><b>{p.title || '(제목 없음)'}</b><br /><span className="hint">{[p.experience_level, p.employment_type].filter(Boolean).join(' · ')} 모집 {p.headcount ?? '—'}명</span></td>
                        <td>{p.job_family ? JOB[p.job_family] : '—'}</td>
                        <td><span className={`pill ${p.hidden ? 'hold' : kind}`}>{p.hidden ? '숨김' : label}</span>{p.hidden && <><br /><span className="hint">운영자가 숨김 처리</span></>}</td>
                        <td className="num">{p.view_count.toLocaleString()}</td><td className="num">{p.applicant_count}</td>
                        <td>{p.effective_status === 'CLOSED' ? '마감됨' : p.deadline_date ? formatDate(p.deadline_date) : '—'}</td>
                        <td>{deleting === p.id ? (
                          <div className="confirm"><span>이 공고를 삭제하시겠습니까? 지원자 화면에서 바로 사라지고 되돌릴 수 없습니다.{p.applicant_count > 0 ? ` 응시자 ${p.applicant_count}명의 결과는 응시자 목록에 남습니다.` : ''}</span>
                            <button className="btn btn-danger" type="button" onClick={() => del.mutate(p)} disabled={del.isPending}>삭제</button>
                            <button className="btn" type="button" onClick={() => setDeleting(null)}>취소</button></div>
                        ) : (
                          <div className="btnrow">
                            <button className="btn btn-sm" type="button" onClick={() => nav(`/recruiter/postings/${p.id}/edit`)}>{p.status === 'DRAFT' ? '이어서 작성' : '수정'}</button>
                            <button className="btn btn-sm" type="button" onClick={() => nav(`/recruiter/postings/${p.id}/preview`)}>미리보기</button>
                            {p.status === 'OPEN' && p.effective_status === 'OPEN' && <button className="btn btn-sm" type="button" onClick={() => close.mutate(p)} disabled={close.isPending}>마감하기</button>}
                            <button className="btn btn-sm danger-t" type="button" onClick={() => setDeleting(p.id)}>삭제</button>
                          </div>
                        )}</td>
                      </tr>
                    );
                  })}
                </tbody></table></div></div>
            </>
          );
        }}
      </QueryState>
    </>
  );
}
