import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { approveCompany, evidenceUrl, fetchVerifications, rejectCompany, type VerificationRow } from '@/api/operator';
import { describeError } from '@/shared/http/errors';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';

const STATUS_LABEL = { PENDING: '검토 필요', APPROVED: '승인', REJECTED: '반려' } as const;
const STATUS_PILL = { PENDING: 'hold', APPROVED: 'good', REJECTED: 'bad' } as const;
const label = (r: VerificationRow) => (r.status === 'APPROVED' && r.method === 'DOMAIN_MAIL' ? '자동 승인' : STATUS_LABEL[r.status]);
const day = (iso: string) => iso.slice(0, 10).replaceAll('-', '.');

/** SC-O01 운영자 · 기업 인증. */
export default function Companies() {
  const toast = useToast();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ['operator', 'companies'], queryFn: fetchVerifications });
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const done = () => qc.invalidateQueries({ queryKey: ['operator', 'companies'] });
  const onError = (e: unknown) => toast(describeError(e).message);

  const approve = useMutation({
    mutationFn: (r: VerificationRow) => approveCompany(r.company_id).then(() => r),
    onSuccess: (r) => { toast(`${r.company_name} 기업회원을 승인했습니다`); void done(); }, onError,
  });
  const reject = useMutation({
    mutationFn: (r: VerificationRow) => rejectCompany(r.company_id, reason.trim()).then(() => r),
    onSuccess: (r) => { toast(`${r.company_name} 신청을 반려했습니다`); setRejecting(null); setReason(''); void done(); }, onError,
  });
  const confirmReject = (r: VerificationRow) => {
    if (!reason.trim()) { toast('반려 사유를 입력해 주십시오'); return; }
    reject.mutate(r);
  };

  return (
    <>
      <div className="ph"><div><h1>기업 인증</h1>
        <p>회사 메일로 인증한 기업은 자동 승인됩니다. 개인 메일로 가입한 기업만 증빙을 보고 승인해 주십시오.</p></div></div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => {
          const c = list.data!.counts;
          return (
            <>
              <div className="filters" data-testid="counts">
                <span className="pill hold">검토 필요</span> <span className="hint">{c.pending}건</span>&nbsp;&nbsp;
                <span className="pill good">자동 승인</span> <span className="hint">{c.auto}건</span>&nbsp;&nbsp;
                <span className="pill good">승인</span> <span className="hint">{c.approved}건</span>&nbsp;&nbsp;
                <span className="pill bad">반려</span> <span className="hint">{c.rejected}건</span>
              </div>
              <div className="card" style={{ padding: 0 }}><div className="tblw"><table className="t">
                <thead><tr><th>회사</th><th>담당자</th><th>이메일</th><th>사업자등록번호</th><th>확인 방법</th><th>증빙</th><th>신청일</th><th>상태</th><th>처리</th></tr></thead>
                <tbody>
                  {list.data!.items.map((r) => (
                    <tr key={r.company_id}>
                      <td><b>{r.company_name}</b></td><td>{r.contact_name ?? '—'}</td><td>{r.email ?? '—'}</td><td>{r.biz_no ?? '—'}</td>
                      <td>{r.method === 'DOMAIN_MAIL' ? '회사 메일 인증' : r.method === 'ISSUED' ? '운영자 발급' : '개인 메일'}</td>
                      <td>{r.evidence_name
                        ? <a className="link" href={evidenceUrl(r.company_id)} target="_blank" rel="noreferrer">{r.evidence_name}</a> : '—'}</td>
                      <td>{day(r.applied_at)}</td>
                      <td><span className={`pill ${label(r) === '자동 승인' ? 'good' : STATUS_PILL[r.status]}`}>{label(r)}</span></td>
                      <td>
                        {r.status === 'PENDING' ? (rejecting === r.company_id ? (
                          <div className="inline">
                            <input aria-label="반려 사유" placeholder="반려 사유 (기업에 전달됩니다)" value={reason}
                              onChange={(e) => setReason(e.target.value)} />
                            <button className="btn" type="button" onClick={() => confirmReject(r)} disabled={reject.isPending}>반려 확정</button>
                            <button className="btn" type="button" onClick={() => { setRejecting(null); setReason(''); }}>취소</button>
                          </div>
                        ) : (
                          <div style={{ display: 'flex', gap: 6 }}>
                            <button className="btn btn-primary" type="button" onClick={() => approve.mutate(r)} disabled={approve.isPending}>승인</button>
                            <button className="btn" type="button" onClick={() => { setRejecting(r.company_id); setReason(''); }}>반려</button>
                          </div>
                        )) : r.status === 'REJECTED'
                          ? <span className="hint" style={{ whiteSpace: 'normal', display: 'block', minWidth: 180 }}>{r.reject_reason}</span> : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table></div></div>
            </>
          );
        }}
      </QueryState>
    </>
  );
}
