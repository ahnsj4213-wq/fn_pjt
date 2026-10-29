import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';
import { fetchMyResult } from '@/api/candidate';
import { DECISION_LABEL, FIXED } from '@/shared/copy/fixed';
import { CANDIDATE_CRITERIA, CRITERION_LABEL } from '@/shared/copy/labels';
import { Notice, Page, uiStyles as s } from '@/shared/ui/components';
import { PlainText, QueryState } from '@/shared/ui/extra';

/** 결과 (RPT-02~04, 6.6): decision and text feedback only. There is no total score (X-04). */
export function Result() {
  const { aid = '' } = useParams();
  const result = useQuery({ queryKey: ['my-result', aid], queryFn: () => fetchMyResult(aid) });
  return (
    <Page title="면접 결과">
      <QueryState isPending={result.isPending} error={result.error} onRetry={() => result.refetch()}>
        {() => {
          const r = result.data!;
          const f = r.feedback;
          return (
            <>
              <p><strong>{DECISION_LABEL[r.decision]}</strong> · {r.status_label}</p>
              {!f ? (
                <Notice>담당자가 검토 중입니다. 결과가 정해지면 피드백을 볼 수 있습니다.</Notice>
              ) : (
                <div className={s.card} data-testid="feedback">
                  <Notice>{f.notice || FIXED.CANDIDATE_FEEDBACK_NOTICE}</Notice>
                  {CANDIDATE_CRITERIA.map((c) => (
                    <section key={c}>
                      <h3>{CRITERION_LABEL[c]}</h3>
                      <PlainText text={f.criteria[c]} />
                    </section>
                  ))}
                  <h3>잘한 점</h3>
                  <ul>{f.strengths.map((t, i) => <li key={i}>{t}</li>)}</ul>
                  <h3>보완할 점</h3>
                  <ul>{f.improvements.map((t, i) => <li key={i}>{t}</li>)}</ul>
                  {f.coding && (
                    <p>{CRITERION_LABEL.coding}: {f.coding === 'ALL_PASSED' ? FIXED.CODING_ALL_PASSED : FIXED.CODING_NOT_ALL_PASSED}</p>
                  )}
                </div>
              )}
              <Link to="/me/applications">지원 현황</Link>
            </>
          );
        }}
      </QueryState>
    </Page>
  );
}
