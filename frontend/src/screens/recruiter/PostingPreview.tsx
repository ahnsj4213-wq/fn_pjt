import { Page, TodoPanel } from '@/shared/ui/components';

/** 공고 미리보기 (JOB-03). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export default function PostingPreview() {
  return (
    <Page title="공고 미리보기">
      <TodoPanel featureIds={['JOB-03']}></TodoPanel>
    </Page>
  );
}
