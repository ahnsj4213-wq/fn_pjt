import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { askInquiry, fetchMyInquiry } from '@/api/candidate';
import { describeError } from '@/shared/http/errors';
import { kstParts } from '@/shared/kst';
import { Modal, useToast } from '@/shared/ui/overlay';

/** 기업에 문의 (작은 창): the applicant's side of SC-R15. One conversation per application; the company answers in 문의 쪽지. */
export function InquiryModal({ applicationId, title, onClose }: { applicationId: string; title: string; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const key = ['my-inquiry', applicationId];
  const thread = useQuery({ queryKey: key, queryFn: () => fetchMyInquiry(applicationId) });
  const [subject, setSubject] = useState('');
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const send = useMutation({
    mutationFn: () => askInquiry(applicationId, { subject: subject.trim() || `${title} 문의`, body: text.trim() }),
    onSuccess: (d) => { qc.setQueryData(key, d); setText(''); toast('쪽지를 보냈습니다. 기업이 확인하면 이곳에 답이 보입니다'); },
    onError: (e) => setError(describeError(e).message),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim()) { setError('보낼 내용을 입력해 주십시오'); return; }
    setError(null);
    send.mutate();
  };
  const stamp = (iso: string) => { const k = kstParts(iso); return `${k.ymd.slice(5).replace('-', '.')} ${k.hm}`; };

  return (
    <Modal title="기업에 문의" onClose={onClose}>
      <p className="hint" style={{ margin: 0 }}>「{title}」 지원에 대한 문의입니다. 채용 담당자가 확인하는 대로 이곳에 답을 남깁니다.</p>
      {thread.data && (
        <div style={{ display: 'grid', gap: 8, maxHeight: 260, overflow: 'auto' }} aria-label="주고받은 쪽지">
          {thread.data.messages.map((m) => (
            <div key={m.id} style={{ justifySelf: m.mine ? 'end' : 'start', maxWidth: '85%', padding: '8px 12px', borderRadius: 10, background: m.mine ? 'var(--primary-soft, #e8efff)' : 'var(--bg)' }}>
              <div className="hint">{m.sender} · {stamp(m.created_at)}</div>
              <div style={{ whiteSpace: 'pre-wrap' }}>{m.body}</div>
            </div>))}
        </div>)}
      <form className="form" onSubmit={submit} noValidate aria-label="문의 쪽지">
        {error && <div className="notice bad" role="alert">{error}</div>}
        {!thread.data && (
          <div className="field"><label htmlFor="iq-subject">제목</label>
            <input id="iq-subject" value={subject} maxLength={100} placeholder={`${title} 문의`} onChange={(e) => setSubject(e.target.value)} /></div>)}
        <div className="field"><label htmlFor="iq-body">내용</label>
          <textarea id="iq-body" value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} /></div>
        <div className="btnrow"><button className="btn btn-primary" type="submit" disabled={send.isPending}>보내기</button></div>
      </form>
    </Modal>
  );
}
