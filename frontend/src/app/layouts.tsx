import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router';
import { logout } from '@/api/common/auth';
import { useToast } from '@/shared/ui/overlay';
import { useLiveUpdates } from './liveUpdates';
import { NotificationBell } from './NotificationBell';
import { setPendingApply, setSession, useSession } from './session';

const BADGE = { CANDIDATE: '면접자', RECRUITER: '기업회원', OPERATOR: '운영자' } as const;

/** SC-C00 상단바, markup of the wireframe top bar. Hidden only during the AI interview (FocusLayout). */
export function TopBar() {
  const { data } = useSession();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  useLiveUpdates(data?.user.id);
  const user = data?.user;
  const inCompanyArea = location.pathname.startsWith('/recruiter');
  const onMyPage = location.pathname.startsWith('/me');

  const onLogout = async () => {
    setBusy(true);
    try {
      await logout();
    } catch {
      // Session already gone or network down: clear local state anyway.
    } finally {
      qc.clear();
      setSession(qc, null);
      setBusy(false);
      toast('로그아웃했습니다');
      navigate('/', { replace: true });
    }
  };

  return (
    <header className="topbar">
      <div className="wrap">
        {/* The logo also forgets a '지원하기' pressed before logging in. */}
        <Link to="/" className="logo" onClick={() => setPendingApply(null)}>
          <span className="logo-mark" aria-hidden="true">면</span>모의면접
        </Link>
        <span className="spacer" />
        {!user ? (
          <Link to="/login" className="btn btn-primary">로그인 / 회원가입</Link>
        ) : (
          <div className="user-chip">
            {user.role === 'OPERATOR' && (
              <Link to={location.pathname.startsWith('/operator') ? '/' : '/operator'} className="btn btn-primary">
                {location.pathname.startsWith('/operator') ? '서비스 화면' : '운영자 페이지'}
              </Link>
            )}
            {user.role === 'RECRUITER' && (
              <Link to={inCompanyArea ? '/' : '/recruiter'} className="btn btn-primary">
                {inCompanyArea ? '서비스 화면' : '기업 관리'}
              </Link>
            )}
            {user.role === 'RECRUITER' && <NotificationBell />}
            {user.role === 'CANDIDATE' && (
              <Link to="/me/applications" className={`btn icon-my${onMyPage ? ' on' : ''}`} aria-label="마이페이지" title="마이페이지">
                <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" /></svg>
                <span className="uname">마이페이지</span>
              </Link>
            )}
            <span className="avatar" aria-hidden="true">{user.name.slice(0, 1)}</span>
            <span className="uname">{user.name}님</span>
            <span className="badge">{BADGE[user.role]}</span>
            <button type="button" className="btn" onClick={onLogout} disabled={busy}>로그아웃</button>
          </div>
        )}
      </div>
    </header>
  );
}

/** Screen spec 2: moving to another screen starts at the top. */
function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export function AppLayout() {
  return (
    <>
      <ScrollToTop />
      <TopBar />
      <main id="main"><Outlet /></main>
    </>
  );
}

/** Interview and coding screens: no top bar, so a stray click cannot leave the test (SC-A06). */
export function FocusLayout() {
  return (
    <>
      <ScrollToTop />
      <main id="main" style={{ minHeight: '100vh' }}><Outlet /></main>
    </>
  );
}
