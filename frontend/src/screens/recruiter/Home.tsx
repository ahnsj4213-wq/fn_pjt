import { Page, TodoPanel } from '@/shared/ui/components';

/** 공고 관리 (ADM-01,JOB-07). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export default function Home() {
  return (
    <Page title="공고 관리">
      <TodoPanel featureIds={['ADM-01', 'JOB-07']}></TodoPanel>
    </Page>
  );
}
