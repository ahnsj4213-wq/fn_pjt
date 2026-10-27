import { Page, TodoPanel } from '@/shared/ui/components';

/** 회원 탈퇴 (USR-07). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export function Account() {
  return (
    <Page title="회원 탈퇴">
      <TodoPanel featureIds={['USR-07']}></TodoPanel>
    </Page>
  );
}
