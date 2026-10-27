import { Page, TodoPanel } from '@/shared/ui/components';

/** 면접 환경 점검 (INT-01,INT-02,INT-14,INT-17). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Precheck() {
  return (
    <Page title="면접 환경 점검">
      <TodoPanel featureIds={['INT-01', 'INT-02', 'INT-14', 'INT-17']}></TodoPanel>
    </Page>
  );
}
