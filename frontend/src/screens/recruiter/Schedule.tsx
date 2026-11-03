import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import {
  addScheduleEntry, deleteScheduleEntry, fetchSchedule, updateScheduleEntry, type EntryKind, type ScheduleEntry,
} from '@/api/recruiter';
import { describeError } from '@/shared/http/errors';
import { fromKst, kstParts, todayKst } from '@/shared/kst';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';

export const KIND: Record<EntryKind, [string, string]> = {
  DEADLINE: ['공고 마감', 't-post'], INTERVIEW: ['면접', 't-iv'], RESULT: ['결과 발표', 't-result'], INTERNAL: ['내부 일정', 't-inner'],
};
export const ANSWER: Record<string, [string, string]> = { PROPOSED: ['수락 대기', 'hold'], ACCEPTED: ['수락', 'good'], RESCHEDULE_REQUESTED: ['조율 요청', 'bad'] };
const DOW = ['일', '월', '화', '수', '목', '금', '토'];
const pad = (n: number) => String(n).padStart(2, '0');

/** SC-R11 기업 관리 · 일정 관리. */
export default function Schedule() {
  const toast = useToast();
  const qc = useQueryClient();
  const today = todayKst();
  const [cursor, setCursor] = useState({ y: Number(today.slice(0, 4)), m: Number(today.slice(5, 7)) - 1 });
  const [editId, setEditId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [form, setForm] = useState({ d: today, t: '14:00', kind: 'INTERVIEW' as Exclude<EntryKind, 'DEADLINE'>, content: '' });
  const month = `${cursor.y}-${pad(cursor.m + 1)}`;
  const list = useQuery({ queryKey: ['recruiter', 'schedule', month], queryFn: () => fetchSchedule(month) });
  const entries = list.data ?? [];
  const editing = entries.find((e) => e.id === editId) ?? null;

  useEffect(() => {
    if (!editing) return;
    const p = kstParts(editing.starts_at);
    setForm({ d: p.ymd, t: p.hm, kind: editing.kind === 'DEADLINE' ? 'INTERNAL' : editing.kind, content: editing.content });
    setConfirmDelete(false);
  }, [editing]);

  const done = () => qc.invalidateQueries({ queryKey: ['recruiter'] });
  const onError = (e: unknown) => toast(describeError(e).message);
  const reset = () => { setEditId(null); setConfirmDelete(false); setForm({ d: today, t: '14:00', kind: 'INTERVIEW', content: '' }); };
  const save = useMutation({
    mutationFn: () => {
      const body = { starts_at: fromKst(form.d, form.t), kind: form.kind, content: form.content.trim(), place: editing?.place ?? null };
      return editId ? updateScheduleEntry(editId, body) : addScheduleEntry(body);
    },
    onSuccess: () => { toast(editId ? '일정을 고쳤습니다' : '일정을 추가했습니다'); const [y, m] = form.d.split('-').map(Number); setCursor({ y, m: m - 1 }); reset(); void done(); },
    onError,
  });
  const del = useMutation({
    mutationFn: () => deleteScheduleEntry(editId!),
    onSuccess: () => { toast('일정을 삭제했습니다'); reset(); void done(); }, onError,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!form.content.trim()) { toast('내용을 입력해 주십시오'); return; }
    save.mutate();
  };
  const pick = (e: ScheduleEntry) => {
    if (!e.editable) { toast('공고 마감일은 공고 관리에서 공고를 수정하면 바뀝니다'); return; }
    setEditId(e.id);
  };

  const first = new Date(Date.UTC(cursor.y, cursor.m, 1));
  const cells: { ymd: string; inMonth: boolean }[] = [];
  const start = new Date(first); start.setUTCDate(1 - first.getUTCDay());
  for (let i = 0; i < 42; i++) {
    const d = new Date(start); d.setUTCDate(start.getUTCDate() + i);
    cells.push({ ymd: d.toISOString().slice(0, 10), inMonth: d.getUTCMonth() === cursor.m });
    if (i >= 34 && d.getUTCDay() === 6 && d.getUTCMonth() !== cursor.m) break;
  }
  const move = (n: number) => setCursor((c) => { const t = c.y * 12 + c.m + n; return { y: Math.floor(t / 12), m: t % 12 }; });
  const byDay = (ymd: string) => entries.filter((e) => kstParts(e.starts_at).ymd === ymd);

  return (
    <>
      <div className="ph"><div><h1>일정 관리</h1>
        <p>공고 마감, 면접, 결과 발표 일정을 한곳에서 관리합니다. 공고 마감일과 메일로 잡은 2차 면접은 자동으로 들어옵니다. 일정을 누르면 고칠 수 있습니다.</p></div>
        <div className="filters">
          <button className="btn" type="button" aria-label="이전 달" onClick={() => move(-1)}>‹</button><b>{cursor.y}년 {cursor.m + 1}월</b>
          <button className="btn" type="button" aria-label="다음 달" onClick={() => move(1)}>›</button>
          <button className="btn" type="button" onClick={() => setCursor({ y: Number(today.slice(0, 4)), m: Number(today.slice(5, 7)) - 1 })}>오늘</button></div></div>
      <div className="filters">
        {(Object.keys(KIND) as EntryKind[]).map((k) => <span key={k} className={`ev ${KIND[k][1]}`} style={{ display: 'inline-block' }}>{KIND[k][0]}</span>)}
        <span className="tag">자동</span><span className="hint">공고 등록·메일 발송으로 자동 추가</span></div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (
          <div className="layout2">
            <div className="card" style={{ padding: 0 }}><div className="calw"><div className="cal" data-testid="calendar">
              {DOW.map((d) => <div className="dow" key={d}>{d}</div>)}
              {cells.map((c) => {
                const day = byDay(c.ymd);
                return (
                  <div key={c.ymd} className={`${c.inMonth ? '' : 'out'}${c.ymd === today ? ' today' : ''}`}
                    onClick={() => { if (editId) reset(); setForm((f) => ({ ...f, d: c.ymd })); }}>
                    <span className="d">{Number(c.ymd.slice(8))}</span>
                    {day.slice(0, 2).map((e) => (
                      <span key={e.id} className={`ev ${KIND[e.kind][1]}${e.id === editId ? ' sel' : ''}${e.status === 'PROPOSED' ? ' wait' : ''}`} role="button" tabIndex={0}
                        title={`${e.content}${e.status ? ` · ${ANSWER[e.status][0]}` : ''}`}
                        onClick={(ev) => { ev.stopPropagation(); pick(e); }} onKeyDown={(ev) => ev.key === 'Enter' && pick(e)}>
                        {kstParts(e.starts_at).hm} {e.content}</span>
                    ))}
                    {day.length > 2 && <span className="more">+{day.length - 2}개</span>}
                  </div>
                );
              })}
            </div></div></div>
            <div style={{ display: 'grid', gap: 16 }}>
              <div className="card"><h2>{editing ? '일정 수정' : '일정 추가'} <small>{editing ? (editing.auto ? '메일로 잡은 일정입니다' : '') : '달력의 날짜를 누르면 날짜가 채워집니다'}</small></h2>
                <form className="form" onSubmit={submit} noValidate>
                  <div className="row2">
                    <div className="field"><label htmlFor="ev-d">날짜</label><input id="ev-d" type="date" value={form.d} onChange={(e) => setForm({ ...form, d: e.target.value })} required /></div>
                    <div className="field"><label htmlFor="ev-t">시간</label><input id="ev-t" type="time" value={form.t} onChange={(e) => setForm({ ...form, t: e.target.value })} /></div>
                  </div>
                  <div className="field"><label htmlFor="ev-kind">종류</label>
                    <select id="ev-kind" value={form.kind} disabled={Boolean(editing?.application_id)}
                      onChange={(e) => setForm({ ...form, kind: e.target.value as typeof form.kind })}>
                      {(['INTERVIEW', 'RESULT', 'INTERNAL'] as const).map((k) => <option key={k} value={k}>{KIND[k][0]}</option>)}</select></div>
                  <div className="field"><label htmlFor="ev-content">내용</label>
                    <input id="ev-content" value={form.content} placeholder="예: 박서연 2차 대면 면접" onChange={(e) => setForm({ ...form, content: e.target.value })} /></div>
                  {editing?.status && <p className="hint" style={{ margin: 0 }}>지원자 응답: <span className={`pill ${ANSWER[editing.status][1]}`}>{ANSWER[editing.status][0]}</span>
                    {editing.status === 'ACCEPTED' && ' · 시간을 바꾸면 지원자에게 변경 메일이 발송됩니다'}</p>}
                  {editing ? (confirmDelete ? (
                    <div className="confirm"><span>이 일정을 삭제하시겠습니까?</span>
                      <button className="btn btn-danger" type="button" onClick={() => del.mutate()} disabled={del.isPending}>삭제</button>
                      <button className="btn" type="button" onClick={() => setConfirmDelete(false)}>취소</button></div>
                  ) : (
                    <div className="btnrow"><button className="btn btn-primary" type="submit" disabled={save.isPending}>수정 저장</button>
                      <button className="btn" type="button" onClick={reset}>취소</button>
                      <button className="btn danger-t" type="button" onClick={() => setConfirmDelete(true)}>삭제</button></div>
                  )) : <button className="btn btn-primary" type="submit" disabled={save.isPending}>일정 추가</button>}
                </form></div>
              <div className="card"><h2>{cursor.m + 1}월 일정 <small>{entries.length}건</small></h2>
                <div className="evlist">
                  {entries.length === 0 && <span className="hint">이 달에는 일정이 없습니다</span>}
                  {entries.map((e) => {
                    const p = kstParts(e.starts_at);
                    return (
                      <div key={e.id} style={{ cursor: 'pointer' }} onClick={() => pick(e)}>
                        <span className="when">{Number(p.ymd.slice(5, 7))}.{Number(p.ymd.slice(8))}<br />{p.hm}</span>
                        <span><span className={`ev ${KIND[e.kind][1]}`} style={{ display: 'inline-block' }}>{KIND[e.kind][0]}</span> {e.content}
                          {e.auto && <> <span className="tag">자동</span></>}
                          {e.status && <> <span className={`pill ${ANSWER[e.status][1]}`}>{ANSWER[e.status][0]}</span></>}</span>
                      </div>
                    );
                  })}
                </div></div>
            </div>
          </div>
        )}
      </QueryState>
    </>
  );
}
