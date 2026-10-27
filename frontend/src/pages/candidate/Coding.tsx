import { Page, TodoPanel } from '@/shared/ui/components';

/** 코딩테스트 (COD-01,COD-02,COD-03,COD-04,COD-05,COD-06). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Coding() {
  return (
    <Page title="코딩테스트">
      <TodoPanel featureIds={['COD-01', 'COD-02', 'COD-03', 'COD-04', 'COD-05', 'COD-06']}></TodoPanel>
    </Page>
  );
}
