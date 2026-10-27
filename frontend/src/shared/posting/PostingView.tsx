import type { PostingPublic } from '@/api/common/postings';
import { formatDate } from '@/shared/format';
import { PlainText } from '@/shared/ui/extra';
import { uiStyles as s } from '@/shared/ui/components';

/**
 * What a candidate sees for a posting (JOB-01). Used by the candidate detail page and the recruiter
 * preview (JOB-03), so both render the same thing. All posting text is plain text.
 */
export function PostingView({ posting }: { posting: PostingPublic }) {
  return (
    <article>
      <p className={s.muted}>
        {posting.company_name} · {posting.job_family_label} · 마감 {formatDate(posting.deadline_date)}
        {!posting.is_open && ' (마감됨)'}
      </p>
      <dl>
        <dt className={s.label}>모집 인원</dt>
        <dd>{posting.headcount ? `${posting.headcount}명` : '-'}</dd>
        <dt className={s.label}>코딩테스트</dt>
        <dd>
          {posting.coding_enabled
            ? `포함 (언어 ${posting.coding_language === 'python' ? 'Python' : posting.coding_language ?? '-'}, 제한시간 ${posting.coding_time_limit_min ?? '-'}분)`
            : '없음'}
        </dd>
      </dl>
      <h2>담당 업무</h2>
      <PlainText text={posting.duties} />
      <h2>지원 요건</h2>
      <PlainText text={posting.requirements} />
      <h2>우대 사항</h2>
      <PlainText text={posting.preferred} />
      {posting.company_intro && (
        <>
          <h2>회사 소개</h2>
          <PlainText text={posting.company_intro} />
        </>
      )}
    </article>
  );
}
