import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import { fetchPostingPreview } from '@/api/recruiter';
import { PostingView } from '@/shared/posting/PostingView';
import { QueryState } from '@/shared/ui/extra';

/** SC-R06 공고 미리보기: the page candidates will see, drawn from the saved draft. */
export default function PostingPreview() {
  const { pid = '' } = useParams();
  const p = useQuery({ queryKey: ['recruiter', 'preview', pid], queryFn: () => fetchPostingPreview(pid) });
  return (
    <>
      <Link to="/recruiter/postings" className="back" style={{ margin: 0 }}>‹ 공고 목록</Link>
      <div className="banner" style={{ margin: 0 }}>지원자가 공고 상세에서 보게 될 화면입니다. 버튼은 눌러도 동작하지 않습니다.</div>
      <QueryState isPending={p.isPending} error={p.error} onRetry={() => p.refetch()}>
        {() => <PostingView posting={p.data!} />}
      </QueryState>
    </>
  );
}
