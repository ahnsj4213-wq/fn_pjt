import { Page, TodoPanel } from '@/shared/ui/components';

/** 응시 완료 (RPT-01). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Done() {
  return (
    <Page title="응시 완료">
      <TodoPanel featureIds={['RPT-01']}></TodoPanel>
    </Page>
  );
}
