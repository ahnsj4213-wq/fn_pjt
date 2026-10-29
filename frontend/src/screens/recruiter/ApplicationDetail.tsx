import { Page, TodoPanel } from '@/shared/ui/components';
import { FIXED } from '@/shared/copy/fixed';
/** 지원자 상세 (ADM-03,ADM-04,ADM-06,ADM-07,RES-08,RPT-02). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export default function ApplicationDetail() {
  return (
    <Page title="지원자 상세">
      <TodoPanel featureIds={['ADM-03', 'ADM-04', 'ADM-06', 'ADM-07', 'RES-08', 'RPT-02']}><p>{FIXED.RECRUITER_AI_NOTICE}</p></TodoPanel>
    </Page>
  );
}
