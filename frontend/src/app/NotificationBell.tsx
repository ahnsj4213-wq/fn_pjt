import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { closePostingNow, extendPosting, fetchInbox, markAllRead, type NotificationItem } from '@/api/common/notifications';
import { formatMonthDay, formatTime } from '@/shared/format';
import { describeError } from '@/shared/http/errors';
import { useToast } from '@/shared/ui/overlay';

const DOT = { DEADLINE: 'k-deadline', APPLY: 'k-apply', SCHEDULE: 'k-sched' } as const;

/** SC-R12 알림 창 (top bar bell). Closing the window marks everything read. */
export function NotificationBell() {
  const toast = useToast();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const inbox = useQuery({ queryKey: ['notifications'], queryFn: fetchInbox, refetchInterval: 30000 });
  const refresh = () => qc.invalidateQueries({ queryKey: ['notifications'] });

  const close = () => {
    setOpen(false);
    if ((inbox.data?.unread ?? 0) > 0) void markAllRead().then(refresh).catch(() => undefined);
  };
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) close(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  });

  const act = useMutation({
    mutationFn: ({ n, what }: { n: NotificationItem; what: 'extend' | 'close' }) => (what === 'extend' ? extendPosting(n.id) : closePostingNow(n.id)).then((r) => ({ ...r, what })),
    onSuccess: (r) => {
      toast(r.what === 'close' ? '공고를 마감했습니다. 메인 화면에서 내려갑니다' : r.done_text);
      void refresh(); void qc.invalidateQueries({ queryKey: ['recruiter'] });
    },
    onError: (e) => { toast(describeError(e).message); void refresh(); },
  });

  const unread = inbox.data?.unread ?? 0;
  return (
    <div ref={box} style={{ position: 'relative', display: 'inline-block' }}>
      <button type="button" className="btn icon-my bell" aria-label={unread ? `알림 ${unread}건` : '알림'} title="알림" aria-expanded={open}
        onClick={() => (open ? close() : setOpen(true))}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z" /><path d="M10 20.5a2.2 2.2 0 0 0 4 0" /></svg>
        {unread > 0 && <span className="nb">{unread}</span>}
      </button>
      {open && (
        <div className="npop" role="dialog" aria-label="알림" style={{ position: 'absolute', top: 'calc(100% + 8px)', right: 0 }}>
          <div className="npop-h"><b>알림</b><span className="hint">지원·일정·마감 소식</span></div>
          <div className="nlist">
            {(inbox.data?.items ?? []).length === 0 && <p className="hint" style={{ padding: '12px 14px' }}>새 알림이 없습니다</p>}
            {(inbox.data?.items ?? []).map((n) => (
              <div key={n.id} className={`nitem${n.unread ? ' un' : ''}`}>
                <span className={`ndot ${DOT[n.kind]}`} aria-hidden="true" />
                <div><p>{n.text}</p><small>{formatMonthDay(n.created_at)} {formatTime(n.created_at)}</small>
                  <div className="nact">
                    {n.kind === 'DEADLINE' && (n.done_text ? <span className="hint ok">{n.done_text}</span>
                      : n.posting_open ? (
                        <>
                          <button className="btn" type="button" disabled={act.isPending} onClick={() => act.mutate({ n, what: 'extend' })}>7일 연장</button>
                          <button className="btn" type="button" disabled={act.isPending} onClick={() => act.mutate({ n, what: 'close' })}>지금 마감</button>
                        </>
                      ) : <span className="hint">이미 마감된 공고입니다</span>)}
                    {n.kind === 'APPLY' && n.application_id && (
                      <button className="link" type="button" onClick={() => { close(); nav(`/recruiter/applications/${n.application_id}`); }}>응시자 보기</button>)}
                    {n.kind === 'SCHEDULE' && <Link className="link" to="/recruiter/schedule" onClick={close}>일정 보기</Link>}
                  </div></div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
