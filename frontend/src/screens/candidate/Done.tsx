import { Link } from 'react-router';
import { Notice, Page, uiStyles as s } from '@/shared/ui/components';

/** 응시 완료 (RPT-01): submitted. Results appear after the recruiter decides (X-04). */
export function Done() {
  return (
    <Page title="응시 완료" narrow>
      <Notice kind="success">응시가 끝났습니다. 수고하셨습니다.</Notice>
      <p>결과는 담당자 검토 후 지원 현황에서 확인할 수 있습니다.</p>
      <div className={s.row}><Link to="/me/applications" data-testid="go-applications">지원 현황</Link></div>
    </Page>
  );
}
