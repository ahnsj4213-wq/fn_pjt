import { Page, TodoPanel } from '@/shared/ui/components';

/** 지원 완료 (INT-13). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function ApplyComplete() {
  return (
    <Page title="지원 완료">
      <TodoPanel featureIds={['INT-13']}></TodoPanel>
    </Page>
  );
}
