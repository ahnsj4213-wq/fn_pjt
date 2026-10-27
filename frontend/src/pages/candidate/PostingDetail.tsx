import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { useSession } from '@/app/session';
import { MESSAGES } from '@/shared/copy/fixed';
import { Button, Notice, Page, TodoPanel } from '@/shared/ui/components';

/**
 * Posting detail (JOB-01, JOB-02). Recruiters may read public postings (consolidation T21), but
 * [지원하기] is blocked with the USR-05 guidance; the server answers 403 ROLE_FORBIDDEN anyway (AC-02).
 * TODO(04-api-contract): load the posting, show "이미 지원한 공고입니다" from my_application (JOB-02).
 */
export function PostingDetail() {
  const { pid = '' } = useParams();
  const { data } = useSession();
  const navigate = useNavigate();
  const [blocked, setBlocked] = useState(false);

  const onApply = () => {
    if (data?.user.role !== 'CANDIDATE') {
      setBlocked(true);
      return;
    }
    navigate(`/postings/${encodeURIComponent(pid)}/apply/consent`);
  };

  return (
    <Page title="채용공고 상세">
      {blocked && <Notice kind="error">{MESSAGES.ROLE_FORBIDDEN_CANDIDATE_AREA}</Notice>}
      <TodoPanel featureIds={['JOB-01', 'JOB-02', 'USR-05']} />
      <Button variant="primary" onClick={onApply} data-testid="apply-button">지원하기</Button>
    </Page>
  );
}
