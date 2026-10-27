import { Page, TodoPanel } from '@/shared/ui/components';

/** 이력서 제출 (RES-01,RES-02,RES-04,RES-06). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function ApplyResume() {
  return (
    <Page title="이력서 제출">
      <TodoPanel featureIds={['RES-01', 'RES-02', 'RES-04', 'RES-06']}></TodoPanel>
    </Page>
  );
}
