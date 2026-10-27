import { Page, TodoPanel } from '@/shared/ui/components';
import { FIXED } from '@/shared/copy/fixed';
/** 결과 확인 (RPT-03,RPT-04,RULE-6.6). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Result() {
  return (
    <Page title="결과 확인">
      <TodoPanel featureIds={['RPT-03', 'RPT-04', 'RULE-6.6']}><p>{FIXED.CANDIDATE_FEEDBACK_NOTICE}</p></TodoPanel>
    </Page>
  );
}
