import { NavLink, Outlet, useSearchParams } from 'react-router';
import { useSession } from '@/app/session';

const MENU: [string, string][] = [
  ['/me/applications', '지원 현황'], ['/me/resume', '이력서'], ['/me/profile', '프로필'], ['/me/schedule', '면접 일정'],
];

/**
 * 마이페이지 메뉴 (SC-A09 1): the four My Page screens share the side menu of the wireframe shellHTML.
 * The resume screen right after signup (SC-A13, ?welcome=1) is a standalone page without the menu.
 */
export function MyPageLayout() {
  const { data } = useSession();
  const welcome = useSearchParams()[0].get('welcome') === '1';
  if (welcome) return <Outlet />;
  return (
    <div className="admin">
      <nav className="side" aria-label="마이페이지 메뉴">
        <div className="side-h">{data?.user.name}님<small>마이페이지</small></div>
        {MENU.map(([to, label]) => <NavLink key={to} to={to} className="nav"><span>{label}</span></NavLink>)}
      </nav>
      <section className="panel" id="panel"><Outlet /></section>
    </div>
  );
}
