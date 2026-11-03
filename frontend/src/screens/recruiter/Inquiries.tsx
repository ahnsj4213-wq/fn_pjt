import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { fetchInquiries, fetchInquiry, replyInquiry } from '@/api/recruiter';
import { describeError } from '@/shared/http/errors';
import { kstParts } from '@/shared/kst';
import { QueryState } from '@/shared/ui/extra';
import { useToast } from '@/shared/ui/overlay';

const KIND: Record<string, string> = { APPLICANT: '응시자', OPERATOR: '운영자' };
const stamp = (iso: string) => { const k = kstParts(iso); return `${k.ymd.slice(5).replace('-', '.')} ${k.hm}`; };

/** SC-R15 기업 관리 · 문의 쪽지: threads on the left, the open conversation and the reply box on the right. */
export default function Inquiries() {
  const toast = useToast();
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<string | null>(null);
  const [text, setText] = useState('');
  const list = useQuery({ queryKey: ['recruiter', 'inquiries'], queryFn: fetchInquiries });
  const thread = useQuery({
    queryKey: ['recruiter', 'inquiry', openId], queryFn: () => fetchInquiry(openId!), enabled: Boolean(openId),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['recruiter', 'inquiries'] });
    void qc.invalidateQueries({ queryKey: ['recruiter', 'inquiry-unread'] });
  };
  const send = useMutation({
    mutationFn: () => replyInquiry(openId!, text.trim()),
    onSuccess: (d) => { qc.setQueryData(['recruiter', 'inquiry', openId], d); setText(''); toast('쪽지를 보냈습니다'); refresh(); },
    onError: (e) => toast(describeError(e).message),
  });
  const open = (id: string) => { setOpenId(id); setTimeout(refresh, 400); };  // opening marks it read on the server
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) { toast('보낼 내용을 입력해 주십시오'); return; }
    send.mutate();
  };

  return (
    <>
      <div className="ph"><div><h1>문의 쪽지</h1><p>응시자와 운영팀이 보낸 쪽지를 보고 답장합니다.</p></div></div>
      <QueryState isPending={list.isPending} error={list.error} onRetry={() => list.refetch()}>
        {() => (
          <div className="inbox2" style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 320px) 1fr', gap: 16, alignItems: 'start' }}>
            <div className="card" style={{ padding: 0 }}>
              {list.data!.length === 0 ? <p style={{ padding: 18, margin: 0 }} className="hint">받은 쪽지가 없습니다.</p> : (
                <ul style={{ listStyle: 'none', margin: 0, padding: 0 }} aria-label="쪽지 목록">
                  {list.data!.map((t) => (
                    <li key={t.id}>
                      <button type="button" onClick={() => open(t.id)} aria-current={openId === t.id || undefined}
                        style={{ display: 'block', width: '100%', textAlign: 'left', padding: '12px 16px', border: 0, borderBottom: '1px solid var(--line)',
                          background: openId === t.id ? 'var(--bg)' : 'none', cursor: 'pointer' }}>
                        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                          {t.unread && <span aria-label="읽지 않음" style={{ width: 8, height: 8, borderRadius: 4, background: 'var(--bad, #d33)' }} />}
                          <b>{t.sender}</b> <span className="pill">{KIND[t.kind]}</span>
                        </div>
                        <div style={{ marginTop: 2 }}>{t.subject}</div>
                        <div className="hint">{stamp(t.last_at)}</div>
                      </button>
                    </li>))}
                </ul>)}
            </div>
            <div className="card">
              {!openId ? <p className="hint" style={{ margin: 0 }}>왼쪽에서 쪽지를 고르면 대화가 열립니다.</p> : (
                <QueryState isPending={thread.isPending} error={thread.error} onRetry={() => thread.refetch()}>
                  {() => {
                    const d = thread.data!;
                    return (
                      <div style={{ display: 'grid', gap: 12 }}>
                        <div><h2 style={{ margin: 0 }}>{d.subject}</h2><span className="hint">{d.sender} · {KIND[d.kind]}</span></div>
                        <div style={{ display: 'grid', gap: 8 }} aria-label="주고받은 쪽지">
                          {d.messages.length === 0 && <p className="hint">아직 주고받은 쪽지가 없습니다. 첫 쪽지를 보내 보십시오.</p>}
                          {d.messages.map((m) => (
                            <div key={m.id} style={{ justifySelf: m.mine ? 'end' : 'start', maxWidth: '80%', padding: '8px 12px', borderRadius: 10,
                              background: m.mine ? 'var(--primary-soft, #e8efff)' : 'var(--bg)' }}>
                              <div className="hint">{m.sender} · {stamp(m.created_at)}</div>
                              <div style={{ whiteSpace: 'pre-wrap' }}>{m.body}</div>
                            </div>))}
                        </div>
                        <form className="form" onSubmit={submit} noValidate aria-label="답장 쓰기">
                          <div className="field"><label htmlFor="iq-reply">답장</label>
                            <textarea id="iq-reply" value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} /></div>
                          <div className="btnrow"><button className="btn btn-primary" type="submit" disabled={send.isPending}>보내기</button></div>
                        </form>
                      </div>);
                  }}
                </QueryState>)}
            </div>
          </div>)}
      </QueryState>
    </>
  );
}
