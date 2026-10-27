import { Page, TodoPanel } from '@/shared/ui/components';

/** 지원 현황 (ADM-04,INT-12,INT-13,RULE-6.6). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function MyApplications() {
  return (
    <Page title="지원 현황">
      <TodoPanel featureIds={['ADM-04', 'INT-12', 'INT-13', 'RULE-6.6']}></TodoPanel>
    </Page>
  );
}
