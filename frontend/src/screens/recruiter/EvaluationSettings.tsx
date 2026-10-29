import { Page, TodoPanel } from '@/shared/ui/components';

/** 평가 설정 (ADM-05,QST-04,JOB-06). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export default function EvaluationSettings() {
  return (
    <Page title="평가 설정">
      <TodoPanel featureIds={['ADM-05', 'QST-04', 'JOB-06']}></TodoPanel>
    </Page>
  );
}
