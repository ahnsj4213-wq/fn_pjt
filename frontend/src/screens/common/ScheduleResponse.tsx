import { useMutation, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';
import { answerSchedule, fetchScheduleInfo } from '@/api/common/schedule';
import { describeError } from '@/shared/http/errors';
import { kstParts } from '@/shared/kst';
import { QueryState } from '@/shared/ui/extra';

const LABEL: Record<string, string> = { PROPOSED: '아직 답하지 않았습니다', ACCEPTED: '수락했습니다', RESCHEDULE_REQUESTED: '조율을 요청했습니다' };

/** Page behind the link in the interview mail (SC-R14): the applicant accepts the date or asks to change it, no login. */
export function ScheduleResponse() {
  const token = useSearchParams()[0].get('token') ?? '';
  const info = useQuery({ queryKey: ['schedule-response', token], queryFn: () => fetchScheduleInfo(token), enabled: Boolean(token), retry: false });
  const answer = useMutation({ mutationFn: (a: 'ACCEPT' | 'RESCHEDULE') => answerSchedule(token, a), onSuccess: () => info.refetch() });
  const when = info.data ? kstParts(info.data.starts_at) : null;
  return (
    <div className="wrap" style={{ maxWidth: 560, paddingBlock: 40 }}>
      <div className="card" style={{ display: 'grid', gap: 12 }}>
        <h1 style={{ margin: 0 }}>면접 일정 확인</h1>
        {!token ? <div className="notice bad">잘못된 링크입니다.</div> : (
          <QueryState isPending={info.isPending} error={info.error} onRetry={() => info.refetch()}>
            {() => (
              <>
                <p style={{ margin: 0 }}><b>{info.data!.company_name}</b>이(가) {info.data!.title} 일정을 보냈습니다.</p>
                <p style={{ margin: 0 }}>일시: <b>{when!.ymd} {when!.hm}</b>{info.data!.place ? <><br />장소: {info.data!.place}</> : null}</p>
                <p className="hint" style={{ margin: 0 }}>현재 상태: {LABEL[info.data!.status] ?? info.data!.status}</p>
                {answer.isError && <div className="notice bad" role="alert">{describeError(answer.error).message}</div>}
                <div className="btnrow">
                  <button className="btn btn-primary" type="button" disabled={answer.isPending} onClick={() => answer.mutate('ACCEPT')}>일정 수락</button>
                  <button className="btn" type="button" disabled={answer.isPending} onClick={() => answer.mutate('RESCHEDULE')}>일정 조율 요청</button>
                </div>
              </>
            )}
          </QueryState>
        )}
      </div>
    </div>
  );
}
