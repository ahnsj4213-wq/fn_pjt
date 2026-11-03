import { useQuery } from '@tanstack/react-query';
import { NavLink, Outlet } from 'react-router';
import { fetchVerifications } from '@/api/operator';

/** 운영자 메뉴 (SC-O01 1): 기업 인증 옆 숫자는 '검토 필요' 건수. */
export function OperatorLayout() {
  const list = useQuery({ queryKey: ['operator', 'companies'], queryFn: fetchVerifications });
  const pending = list.data?.counts.pending ?? 0;
  return (
    <div className="admin">
      <nav className="side" aria-label="운영자 메뉴">
        <div className="side-h">운영자<small>플랫폼 운영</small></div>
        <NavLink to="/operator" end className="nav"><span>기업 인증</span>{pending > 0 && <span className="cnt">{pending}</span>}</NavLink>
        <NavLink to="/operator/postings" className="nav"><span>공고 관리</span></NavLink>
      </nav>
      <section className="panel" id="panel"><Outlet /></section>
    </div>
  );
}
