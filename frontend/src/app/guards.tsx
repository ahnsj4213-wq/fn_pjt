import type { ReactNode } from 'react';
import { Link, Navigate, useLocation } from 'react-router';
import type { Role } from '@/api/common/auth';
import { MESSAGES } from '@/shared/copy/fixed';
import { describeError } from '@/shared/http/errors';
import { Loading, Notice, Page } from '@/shared/ui/components';
import { loginPath, roleHome, useSession } from './session';

/** Not logged in -> /login?next=<current path>. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const session = useSession();
  const location = useLocation();
  if (session.isPending) return <Loading />;
  if (session.isError) {
    return (
      <Page title="연결 오류">
        <Notice kind="error">{describeError(session.error).message}</Notice>
      </Page>
    );
  }
  if (!session.data) return <Navigate to={loginPath(location.pathname + location.search)} replace />;
  return <>{children}</>;
}

/**
 * Role guard. The server's 403 ROLE_FORBIDDEN is the real control (SEC-03); this only shows the
 * USR-05 guidance instead of a broken screen. Children (e.g. lazy recruiter chunks) are not rendered,
 * so a candidate session never downloads recruiter code.
 */
export function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { data } = useSession();
  if (!data) return null;
  if (data.user.role !== role) {
    const message = role === 'CANDIDATE' ? MESSAGES.ROLE_FORBIDDEN_CANDIDATE_AREA : MESSAGES.ROLE_FORBIDDEN_RECRUITER_AREA;
    return (
      <Page title="이용할 수 없는 화면">
        <Notice kind="error">{message}</Notice>
        <Link to={roleHome(data.user.role)}>내 첫 화면으로 이동</Link>
      </Page>
    );
  }
  return <>{children}</>;
}

/** "/" -> role home or login. */
export function HomeRedirect() {
  const session = useSession();
  if (session.isPending) return <Loading />;
  return <Navigate to={session.data ? roleHome(session.data.user.role) : '/login'} replace />;
}
