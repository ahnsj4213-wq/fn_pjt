import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { fetchConsent, recordConsent } from '@/api/candidate';
import { describeError } from '@/shared/http/errors';
import { Button, Notice, Page, uiStyles as s } from '@/shared/ui/components';
import { PlainText, QueryState } from '@/shared/ui/extra';
import { useApplyFlow } from './apply/ApplyFlow';

/** Apply step 1: personal-data consent (SEC-01, SEC-02). Every required item must be agreed. */
export function Consent() {
  const { pid = '' } = useParams();
  const navigate = useNavigate();
  const flow = useApplyFlow();
  const doc = useQuery({ queryKey: ['consent'], queryFn: fetchConsent });
  const [answers, setAnswers] = useState<Record<string, boolean>>({});
  const save = useMutation({
    mutationFn: () => recordConsent(pid, { version: doc.data!.version, answers }),
    onSuccess: (r) => {
      flow.setConsentId(r.consent_id);
      navigate(`/postings/${encodeURIComponent(pid)}/apply/resume`);
    },
  });

  return (
    <Page title="개인정보 수집·이용 동의" narrow>
      <QueryState isPending={doc.isPending} error={doc.error} onRetry={() => doc.refetch()}>
        {() => {
          const d = doc.data!;
          const requiredOk = d.items.filter((i) => i.required).every((i) => answers[i.code]);
          return (
            <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
              <div className={s.card}><PlainText text={d.body_md} /></div>
              <fieldset className={s.fieldset}>
                <legend>동의 항목</legend>
                {d.items.map((i) => (
                  <label key={i.code} className={s.row}>
                    <input type="checkbox" checked={!!answers[i.code]}
                      onChange={(e) => setAnswers({ ...answers, [i.code]: e.target.checked })} />
                    {i.required ? '[필수] ' : '[선택] '}{i.label}
                  </label>
                ))}
              </fieldset>
              {save.error && <Notice kind="error">{describeError(save.error).message}</Notice>}
              <Button type="submit" variant="primary" disabled={!requiredOk} busy={save.isPending}>동의하고 다음</Button>
            </form>
          );
        }}
      </QueryState>
    </Page>
  );
}
