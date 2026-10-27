import { Page, TodoPanel } from '@/shared/ui/components';

/** AI 면접 (INT-04,INT-05,INT-06,INT-07,INT-08,INT-09,INT-10,INT-11,INT-13,INT-15,INT-16,INT-18). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Interview() {
  return (
    <Page title="AI 면접">
      <TodoPanel featureIds={['INT-04', 'INT-05', 'INT-06', 'INT-07', 'INT-08', 'INT-09', 'INT-10', 'INT-11', 'INT-13', 'INT-15', 'INT-16', 'INT-18']}></TodoPanel>
    </Page>
  );
}
