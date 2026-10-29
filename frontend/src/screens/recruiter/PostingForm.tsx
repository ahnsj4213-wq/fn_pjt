import { Page, TodoPanel } from '@/shared/ui/components';

/** 공고 작성·수정 (JOB-03,JOB-04,JOB-05,JOB-06). TODO: implement after docs/trd/04-api-contract.md (and 06 for realtime screens). */
export default function PostingForm() {
  return (
    <Page title="공고 작성·수정">
      <TodoPanel featureIds={['JOB-03', 'JOB-04', 'JOB-05', 'JOB-06']}></TodoPanel>
    </Page>
  );
}
