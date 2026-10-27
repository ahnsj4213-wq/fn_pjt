import { Page, TodoPanel } from '@/shared/ui/components';

/** 면접 안내 (INT-03). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Guide() {
  return (
    <Page title="면접 안내">
      <TodoPanel featureIds={['INT-03']}></TodoPanel>
    </Page>
  );
}
