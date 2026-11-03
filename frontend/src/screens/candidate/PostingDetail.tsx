import { useQuery } from '@tanstack/react-query';
import { useEffect } from 'react';
import { Link, useParams } from 'react-router';
import { fetchRegisteredResume } from '@/api/candidate';
import { fetchPosting } from '@/api/common/postings';
import { useSession } from '@/app/session';
import { useStartApply } from '@/app/useStartApply';
import { PostingView } from '@/shared/posting/PostingView';
import { QueryState } from '@/shared/ui/extra';

/** SC-A03 공고 상세 (wireframe renderDetail). No login needed; applying follows the main page rules. */
export function PostingDetail() {
  const { pid = '' } = useParams();
  const { data: session } = useSession();
  const startApply = useStartApply();
  const posting = useQuery({ queryKey: ['posting', pid], queryFn: () => fetchPosting(pid) });
  const isCandidate = session?.user.role === 'CANDIDATE';
  const resume = useQuery({ queryKey: ['resume'], queryFn: fetchRegisteredResume, enabled: isCandidate });

  useEffect(() => {
    if (posting.data) document.title = `${posting.data.title} | 모의면접`;
  }, [posting.data]);

  return (
    <div className="wrap">
      <Link to="/" className="back" style={{ display: 'inline-block', textDecoration: 'none' }}>‹ 공고 목록</Link>
      <QueryState isPending={posting.isPending} error={posting.error} onRetry={() => posting.refetch()}>
        {() => {
          const p = posting.data!;
          const hasResume = Boolean(resume.data?.resume);
          return (
            <PostingView posting={p} tail={(
              <div className="start-bar">
                <p>
                  <b>이 공고에 AI 면접으로 지원하기</b>
                  환경 점검과 개인정보 동의를 마치면 공고 내용과 내 이력서로 만든 질문으로 면접이 시작됩니다.
                  <span className="checks">
                    {session && !isCandidate
                      ? <span className="no">! 기업회원·운영자 계정은 지원할 수 없습니다</span>
                      : (
                        <>
                          <span className={session ? 'ok' : 'no'}>{session ? '✓ 로그인됨' : '! 로그인 필요'}</span>
                          <span className={hasResume ? 'ok' : 'no'}>{hasResume ? '✓ 등록된 이력서 있음' : '이력서는 지원 단계에서 올릴 수 있습니다'}</span>
                        </>
                      )}
                  </span>
                </p>
                {p.has_applied
                  ? <Link to="/me/applications" className="btn btn-lg">지원 현황에서 보기</Link>
                  : (
                    <button type="button" className="btn btn-primary btn-lg" disabled={!p.is_open} data-testid="apply-button"
                      onClick={() => startApply(p.id)}>
                      {p.is_open ? 'AI 면접 지원하기' : '마감된 공고입니다'}
                    </button>
                  )}
              </div>
            )} />
          );
        }}
      </QueryState>
    </div>
  );
}
