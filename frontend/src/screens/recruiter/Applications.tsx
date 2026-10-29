import { Page, TodoPanel } from '@/shared/ui/components';

/** 지원자 목록 (ADM-02). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export default function Applications() {
  return (
    <Page title="지원자 목록">
      <TodoPanel featureIds={['ADM-02']}></TodoPanel>
    </Page>
  );
}
