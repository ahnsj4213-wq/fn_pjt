import { Page, TodoPanel } from '@/shared/ui/components';

/** 통계 (ADM-08). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export default function Stats() {
  return (
    <Page title="통계">
      <TodoPanel featureIds={['ADM-08']}></TodoPanel>
    </Page>
  );
}
