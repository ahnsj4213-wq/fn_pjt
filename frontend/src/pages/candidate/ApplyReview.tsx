import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Navigate, useNavigate, useParams } from 'react-router';
import { applyToPosting } from '@/api/candidate';
import { describeError } from '@/shared/http/errors';
import { ResumeView } from '@/shared/resume/ResumeView';
import { Button, Notice, Page, uiStyles as s } from '@/shared/ui/components';
import { useApplyFlow } from './apply/ApplyFlow';

/** Apply step 3: confirm and submit (JOB-02). The server stores an immutable resume snapshot (RES-07). */
export function ApplyReview() {
  const { pid = '' } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { consentId, draft } = useApplyFlow();
  const [updateRegistered, setUpdateRegistered] = useState(false);
  const submit = useMutation({
    mutationFn: () => applyToPosting(pid, { consent_id: consentId!, resume: draft!.content,
      resume_file_id: draft!.fileId, update_registered: updateRegistered }),
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: ['my-applications'] });
      navigate(`/postings/${encodeURIComponent(pid)}/apply/complete`, { state: { applicationId: r.application_id } });
    },
  });

  if (!consentId || !draft) return <Navigate to={`/postings/${encodeURIComponent(pid)}/apply/consent`} replace />;
  return (
    <Page title="제출 내용 확인" narrow>
      <div className={s.card}><ResumeView resume={draft.content} /></div>
      <label className={s.row}>
        <input type="checkbox" checked={updateRegistered} onChange={(e) => setUpdateRegistered(e.target.checked)} />
        이 내용으로 내 이력서도 저장하기
      </label>
      {submit.error && <Notice kind="error">{describeError(submit.error).message}</Notice>}
      <div className={s.row}>
        <Button onClick={() => navigate(-1)}>이전</Button>
        <Button variant="primary" onClick={() => submit.mutate()} busy={submit.isPending} data-testid="submit-application">
          지원서 제출
        </Button>
      </div>
    </Page>
  );
}
