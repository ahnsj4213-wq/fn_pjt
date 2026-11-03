import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { sendApplicantMail, type Decision, type MailIn } from '@/api/recruiter';
import { describeError } from '@/shared/http/errors';
import { fromKst, kstParts } from '@/shared/kst';
import { Modal, useToast } from '@/shared/ui/overlay';

type Kind = MailIn['kind'];
const KINDS: [Kind, string][] = [['INTERVIEW', '2차 면접 일정 안내'], ['RESULT', '결과 안내'], ['FREE', '직접 작성']];

function defaults(kind: Kind, name: string, company: string, decision: Decision): { subject: string; body: string } {
  if (kind === 'INTERVIEW') {
    return { subject: '2차 면접 일정 안내', body: `${name}님, 안녕하십니까.\n${company} 채용팀입니다. AI 면접에 참여해 주셔서 감사합니다.\n2차 면접 일정을 아래와 같이 안내드립니다. 메일의 링크에서 일정을 수락하거나 조율을 요청해 주십시오.` };
  }
  if (kind === 'RESULT') {
    const line = decision === 'PASS' ? '심사 결과 합격하셨음을 알려드립니다. 이후 절차는 별도로 안내드리겠습니다.'
      : decision === 'FAIL' ? '아쉽게도 이번 채용에서는 함께하지 못하게 되었습니다. 지원해 주셔서 감사합니다.'
        : '현재 지원서를 검토하고 있습니다. 결과가 정해지면 다시 안내드리겠습니다.';
    return { subject: '지원 결과 안내', body: `${name}님, 안녕하십니까.\n${company} 채용팀입니다.\n${line}` };
  }
  return { subject: '', body: '' };
}

/** SC-R14 메일 보내기 (작은 창). The server adds [회사명] to the subject and, for a date, the answer link. */
export function MailModal({ applicationId, name, email, company, decision, onClose }: {
  applicationId: string; name: string; email: string; company: string; decision: Decision; onClose: () => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const nine = kstParts(new Date(Date.now() + 9 * 86400000).toISOString()).ymd;
  const [kind, setKind] = useState<Kind>('INTERVIEW');
  const [d, setD] = useState(nine);
  const [t, setT] = useState('14:00');
  const [place, setPlace] = useState('');
  const [v, setV] = useState(defaults('INTERVIEW', name, company, decision));
  const [error, setError] = useState<string | null>(null);

  const pickKind = (k: Kind) => { setKind(k); setV(defaults(k, name, company, decision)); setError(null); };
  const send = useMutation({
    mutationFn: () => sendApplicantMail(applicationId, { kind, subject: v.subject.trim(), body: v.body, place: place.trim() || null,
      ...(kind === 'INTERVIEW' ? { starts_at: fromKst(d, t) } : {}) }),
    onSuccess: (r) => {
      toast(r.schedule_id ? "메일을 보냈습니다. 일정 관리에 '수락 대기'로 들어갔습니다" : '메일을 보냈습니다');
      void qc.invalidateQueries({ queryKey: ['recruiter', 'schedule'] });
      onClose();
    },
    onError: (e) => setError(describeError(e).message),
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!v.subject.trim()) { setError('제목을 입력해 주십시오'); return; }
    if (kind === 'INTERVIEW' && !d) { setError('면접 날짜를 골라 주십시오'); return; }
    setError(null);
    send.mutate();
  };

  return (
    <Modal title="메일 보내기" onClose={onClose} wide>
      <form className="form" onSubmit={submit} noValidate aria-label="메일 보내기">
        {error && <div className="notice bad" role="alert">{error}</div>}
        <div className="field"><label>받는 사람</label><div>{name} &lt;{email}&gt;</div></div>
        <div className="field"><label htmlFor="ml-kind">메일 종류</label>
          <select id="ml-kind" value={kind} onChange={(e) => pickKind(e.target.value as Kind)}>
            {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
        {kind === 'INTERVIEW' && (
          <>
            <div className="row2">
              <div className="field"><label htmlFor="ml-d">면접 날짜</label><input id="ml-d" type="date" value={d} onChange={(e) => setD(e.target.value)} /></div>
              <div className="field"><label htmlFor="ml-t">시간</label><input id="ml-t" type="time" value={t} onChange={(e) => setT(e.target.value)} /></div>
            </div>
            <div className="field"><label htmlFor="ml-place">장소</label><input id="ml-place" value={place} onChange={(e) => setPlace(e.target.value)} placeholder="예: 본사 5층 회의실" /></div>
          </>
        )}
        <div className="field"><label htmlFor="ml-subject">제목</label>
          <div className="inline"><span className="hint">[{company}]</span><input id="ml-subject" value={v.subject} style={{ flex: 1 }} maxLength={180}
            onChange={(e) => setV({ ...v, subject: e.target.value })} /></div></div>
        <div className="field"><label htmlFor="ml-body">내용</label>
          <textarea id="ml-body" rows={6} value={v.body} onChange={(e) => setV({ ...v, body: e.target.value })} /></div>
        <div className="field"><label>메일 미리보기</label>
          <div className="card" aria-label="메일 미리보기" style={{ whiteSpace: 'pre-wrap' }}>
            <b>[{company}] {v.subject}</b>{'\n\n'}{v.body}
            {kind === 'INTERVIEW' && (
              <>{'\n\n'}면접 일시: {d} {t}{place ? `\n장소: ${place}` : ''}{'\n\n'}
                <span className="btn btn-primary btn-sm">일정 수락</span> <span className="btn btn-sm">일정 조율 요청</span></>
            )}
          </div></div>
        <div className="mfoot">
          <button className="btn" type="button" onClick={onClose}>취소</button>
          <button className="btn btn-primary" type="submit" disabled={send.isPending} aria-busy={send.isPending || undefined}>보내기</button>
        </div>
      </form>
    </Modal>
  );
}
