import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { fetchPosting } from '@/api/common/postings';
import { useSession } from '@/app/session';
import { MESSAGES } from '@/shared/copy/fixed';
import { PostingView } from '@/shared/posting/PostingView';
import { Button, Notice, Page } from '@/shared/ui/components';
import { QueryState } from '@/shared/ui/extra';

/**
 * Posting detail (JOB-01, JOB-02). Recruiters may read public postings (consolidation T21), but
 * [지원하기] is blocked with the USR-05 guidance; the server answers 403 ROLE_FORBIDDEN anyway (AC-02).
 */
export function PostingDetail() {
  const { pid = '' } = useParams();
  const { data } = useSession();
  const navigate = useNavigate();
  const [blocked, setBlocked] = useState(false);
  const posting = useQuery({ queryKey: ['posting', pid], queryFn: () => fetchPosting(pid) });

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
      <QueryState isPending={posting.isPending} error={posting.error} onRetry={() => posting.refetch()}>
        {() => {
          const p = posting.data!;
          return (
            <>
              <PostingView posting={p} />
              {p.has_applied ? (
                <Notice>이미 지원한 공고입니다. <Link to="/me/applications">지원 현황에서 응시하기</Link></Notice>
              ) : p.is_open ? (
                <Button variant="primary" onClick={onApply} data-testid="apply-button">지원하기</Button>
              ) : (
                <Notice>마감된 공고입니다.</Notice>
              )}
            </>
          );
        }}
      </QueryState>
    </Page>
  );
}
