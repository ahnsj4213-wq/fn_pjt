import { Page, TodoPanel } from '@/shared/ui/components';

/** 내 이력서 (RES-07). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function MyResume() {
  return (
    <Page title="내 이력서">
      <TodoPanel featureIds={['RES-07']}></TodoPanel>
    </Page>
  );
}
