import { Page, TodoPanel } from '@/shared/ui/components';

/** 개인정보 수집·이용 동의 (SEC-01,SEC-02). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Consent() {
  return (
    <Page title="개인정보 수집·이용 동의">
      <TodoPanel featureIds={['SEC-01', 'SEC-02']}></TodoPanel>
    </Page>
  );
}
