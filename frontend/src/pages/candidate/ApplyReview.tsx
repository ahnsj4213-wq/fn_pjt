import { Page, TodoPanel } from '@/shared/ui/components';

/** 이력서 확인·수정 (RES-03,RES-05,RES-07). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function ApplyReview() {
  return (
    <Page title="이력서 확인·수정">
      <TodoPanel featureIds={['RES-03', 'RES-05', 'RES-07']}></TodoPanel>
    </Page>
  );
}
