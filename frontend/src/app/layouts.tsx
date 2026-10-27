import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, NavLink, Outlet, useNavigate } from 'react-router';
import { logout } from '@/api/common/auth';
import { Button } from '@/shared/ui/components';
import s from './layouts.module.css';
import { useSession } from './session';

interface NavItem {
  to: string;
  label: string;
  end?: boolean;
}

const CANDIDATE_NAV: NavItem[] = [
  { to: '/postings', label: '채용공고' },
  { to: '/me/applications', label: '지원 현황' },
  { to: '/me/resume', label: '내 이력서' },
];
const RECRUITER_NAV: NavItem[] = [
  { to: '/recruiter', label: '공고 관리', end: true },
  { to: '/recruiter/applications', label: '지원자' },
  { to: '/recruiter/stats', label: '통계' },
];

export function PublicLayout() {
  return (
    <>
      <header className={s.header}>
        <Link to="/" className={s.brand}>AI 채용 플랫폼</Link>
      </header>
      <main id="main"><Outlet /></main>
    </>
  );
}

export function AppLayout() {
  const { data } = useSession();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const nav = data?.user.role === 'RECRUITER' ? RECRUITER_NAV : CANDIDATE_NAV;

  const onLogout = async () => {
    setBusy(true);
    try {
      await logout();
    } catch {
      // Session already gone or network down: clear local state anyway.
    } finally {
      qc.clear();
      navigate('/login', { replace: true });
    }
  };

  return (
    <>
      <header className={s.header}>
        <Link to="/" className={s.brand}>AI 채용 플랫폼</Link>
        <nav className={s.nav} aria-label="주 메뉴">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end ?? false}>{n.label}</NavLink>
          ))}
        </nav>
        <div className={s.user}>
          <span>{data?.user.company_name ? `${data.user.company_name} · ` : ''}{data?.user.name}</span>
          <Button onClick={onLogout} busy={busy}>로그아웃</Button>
        </div>
      </header>
      <main id="main"><Outlet /></main>
    </>
  );
}

/** Interview and coding screens: no navigation menu, so a stray click cannot leave the test (design D 3.3). */
export function FocusLayout() {
  return <main id="main" className={s.focus}><Outlet /></main>;
}
