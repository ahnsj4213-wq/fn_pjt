import { Page, TodoPanel } from '@/shared/ui/components';

/** 채용공고 (JOB-01,JOB-08). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Postings() {
  return (
    <Page title="채용공고">
      <TodoPanel featureIds={['JOB-01', 'JOB-08']}></TodoPanel>
    </Page>
  );
}
