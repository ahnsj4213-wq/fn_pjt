import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { NavLink, Outlet } from 'react-router';
import { fetchCompany, uploadCompanyEvidence } from '@/api/common/auth';
import { fetchInquiryUnread } from '@/api/recruiter';
import { useSession } from '@/app/session';
import { describeError } from '@/shared/http/errors';
import { useToast } from '@/shared/ui/overlay';

const MENU: [string, string, boolean][] = [
  ['/recruiter', '대시보드', true], ['/recruiter/applications', '응시자 목록', false], ['/recruiter/schedule', '일정 관리', false], ['/recruiter/questions', '면접 질문 관리', false], ['/recruiter/postings', '공고 관리', false],
  ['/recruiter/inquiries', '문의 쪽지', false],
];

/** 기업 관리 메뉴 (SC-R13 1): shared by every company screen, with the verification notice of SC-R01. */
export function RecruiterShell() {
  const { data: session } = useSession();
  const toast = useToast();
  const qc = useQueryClient();
  const file = useRef<HTMLInputElement>(null);
  const company = useQuery({ queryKey: ['company'], queryFn: fetchCompany });
  const upload = useMutation({
    mutationFn: (f: File) => uploadCompanyEvidence(f),
    onSuccess: () => { toast('증빙 서류를 올렸습니다. 운영자가 확인합니다'); void qc.invalidateQueries({ queryKey: ['company'] }); },
    onError: (e) => toast(describeError(e).message),
  });
  const unread = useQuery({ queryKey: ['recruiter', 'inquiry-unread'], queryFn: fetchInquiryUnread, refetchInterval: 60_000 });
  const c = company.data;
  const askFile = () => file.current?.click();
  return (
    <div className="admin">
      <nav className="side" aria-label="기업 관리 메뉴">
        <div className="side-h">{session?.user.company_name}<small>기업 관리</small></div>
        {MENU.map(([to, label, end]) => <NavLink key={to} to={to} end={end} className="nav"><span>{label}</span>{to === '/recruiter/inquiries' && (unread.data?.unread ?? 0) > 0 && <b className="cnt" aria-label={`읽지 않은 쪽지 ${unread.data!.unread}건`}>{unread.data!.unread}</b>}</NavLink>)}
      </nav>
      <section className="panel" id="panel">
        {c?.verification === 'PENDING' && (
          <div className="banner" role="status" style={{ margin: 0 }}>
            기업 인증을 운영자가 확인하고 있습니다. 승인 전에는 공고를 게시할 수 없지만, 나머지 메뉴는 미리 둘러볼 수 있습니다.
            {!c.evidence_name && <> 개인 메일로 가입하셨다면 <button type="button" className="link" onClick={askFile}>재직증명서나 사업자등록증 올리기</button></>}
          </div>
        )}
        {c?.suspended && (
          <div className="banner" role="alert" style={{ margin: 0 }}>
            운영자가 이 기업을 정지했습니다. 공고가 모두 내려갔고 새 공고를 게시할 수 없습니다. 문의가 필요하면 운영팀에 연락해 주십시오.
          </div>
        )}
        {c?.verification === 'REJECTED' && (
          <div className="banner" role="alert" style={{ margin: 0 }}>
            기업 인증이 반려되었습니다{c.reject_reason ? `: ${c.reject_reason}` : ''}. <button type="button" className="link" onClick={askFile}>서류를 다시 올려 재신청</button>
          </div>
        )}
        <input ref={file} type="file" accept=".pdf,.jpg,.jpeg,.png" hidden aria-label="증빙 서류"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ''; }} />
        <Outlet />
      </section>
    </div>
  );
}
