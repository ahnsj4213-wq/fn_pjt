import { isRouteErrorResponse, Link, useRouteError } from 'react-router';
import { checkBrowserSupport } from '@/media/support';
import { MESSAGES } from '@/shared/copy/fixed';
import { Notice, Page } from '@/shared/ui/components';

export function NotFound() {
  return (
    <Page title="페이지를 찾을 수 없습니다">
      <p>{MESSAGES.NOT_FOUND}</p>
      <Link to="/">첫 화면으로 이동</Link>
    </Page>
  );
}

/** NFR-8.7: PC Chrome/Edge only, mic and camera required. */
export function Unsupported() {
  const { reasons } = checkBrowserSupport();
  return (
    <Page title="지원하지 않는 환경">
      <Notice kind="error">PC의 크롬 또는 엣지 최신 버전에서 접속해 주세요. 마이크와 카메라가 필요합니다.</Notice>
      {reasons.length > 0 && <ul>{reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
    </Page>
  );
}

export function RouteError() {
  const err = useRouteError();
  if (isRouteErrorResponse(err) && err.status === 404) return <NotFound />;
  return (
    <Page title="오류가 발생했습니다">
      <Notice kind="error">{MESSAGES.SERVER_ERROR}</Notice>
      <Link to="/">첫 화면으로 이동</Link>
    </Page>
  );
}
