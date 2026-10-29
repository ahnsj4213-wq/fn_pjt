import { Link, useLocation } from 'react-router';
import { Notice, Page, uiStyles as s } from '@/shared/ui/components';

/** Apply step 4: done. The interview can start now or later from 지원 현황 (7.1). */
export function ApplyComplete() {
  const aid = (useLocation().state as { applicationId?: string } | null)?.applicationId;
  return (
    <Page title="지원 완료" narrow>
      <Notice kind="success">지원서를 제출했습니다.</Notice>
      <p>AI 면접은 지금 바로 보거나, 지원 현황에서 나중에 시작할 수 있습니다.</p>
      <div className={s.row}>
        {aid && <Link to={`/applications/${encodeURIComponent(aid)}/guide`} data-testid="go-guide">AI 면접 안내 보기</Link>}
        <Link to="/me/applications">지원 현황</Link>
      </div>
    </Page>
  );
}
