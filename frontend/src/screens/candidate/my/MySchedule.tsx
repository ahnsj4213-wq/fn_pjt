import { useQuery } from '@tanstack/react-query';
import { fetchSchedules, type Schedule } from '@/api/candidate';
import { formatMonthDay, formatTime } from '@/shared/format';
import { QueryState } from '@/shared/ui/extra';

const STATUS: Record<Schedule['status'], [string, string]> = {
  ACCEPTED: ['수락함', 'good'], PROPOSED: ['응답 전', 'hold'], RESCHEDULE_REQUESTED: ['조율 요청함', 'hold'],
};

/** SC-A15 마이페이지 · 면접 일정: dates companies sent by mail, the answer given there, and the place. */
export function MySchedule() {
  const list = useQuery({ queryKey: ['schedules'], queryFn: fetchSchedules });
  return (
    <>
      <div className="ph"><div><h1>면접 일정</h1>
        <p>기업이 메일로 보낸 면접 일정입니다. 메일에서 수락하거나 조율을 요청하면 여기에 반영됩니다.</p></div></div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (
          <div className="card">
            {list.data!.length === 0 ? <p style={{ margin: 0 }} className="hint">아직 받은 면접 일정이 없습니다.</p> : (
              <div className="evlist" data-testid="schedules">
                {list.data!.map((e) => {
                  const [label, kind] = STATUS[e.status];
                  return (
                    <div key={e.id}>
                      <span className="when">{formatMonthDay(e.starts_at)}<br />{formatTime(e.starts_at)}</span>
                      <span><b>{e.title}</b> <span className={`pill ${kind}`}>{label}</span>
                        {e.place && <><br /><span className="hint">{e.place}</span></>}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </QueryState>
    </>
  );
}
