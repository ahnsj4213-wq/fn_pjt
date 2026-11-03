import { useState, type FormEvent } from 'react';
import { findId, requestPasswordReset } from '@/api/common/auth';
import { describeError } from '@/shared/http/errors';
import { Modal } from '@/shared/ui/overlay';
import { Field } from '@/shared/ui/wf';
import { EMAIL_RE, formatPhone, INPUT_MESSAGES, PHONE_RE, required } from './signupForm';

type Tab = 'id' | 'password';

/** SC-A12 아이디·비밀번호 찾기 (wireframe openFind). */
export function FindAccountModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('id');
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [result, setResult] = useState<{ kind: 'id'; emails: string[] } | { kind: 'password'; email: string } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const switchTab = (t: Tab) => {
    setTab(t);
    setErrors({});
    setResult(null);
    setFailure(null);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const errs: Record<string, string> = {};
    if (tab === 'id') {
      if (!name.trim()) errs.name = required('이름');
      if (!PHONE_RE.test(phone)) errs.phone = INPUT_MESSAGES.phone;
    } else if (!email.trim()) {
      errs.email = required('아이디');
    } else if (!EMAIL_RE.test(email.trim())) {
      errs.email = INPUT_MESSAGES.email;
    }
    setErrors(errs);
    if (Object.keys(errs).length) {
      document.getElementById(`find-${Object.keys(errs)[0]}`)?.focus();
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      if (tab === 'id') setResult({ kind: 'id', emails: (await findId({ name: name.trim(), phone })).emails });
      else {
        await requestPasswordReset(email.trim());
        setResult({ kind: 'password', email: email.trim() });
      }
    } catch (err) {
      setFailure(describeError(err).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="아이디·비밀번호 찾기" onClose={onClose}>
      <div className="tabs" role="tablist" aria-label="찾을 항목">
        <button type="button" role="tab" aria-selected={tab === 'id'} onClick={() => switchTab('id')}>아이디 찾기</button>
        <button type="button" role="tab" aria-selected={tab === 'password'} onClick={() => switchTab('password')}>비밀번호 찾기</button>
      </div>
      {failure && <div className="notice bad" role="alert">{failure}</div>}
      <form className="form" onSubmit={submit} noValidate>
        {tab === 'id' ? (
          <>
            <Field id="find-name" label="이름" value={name} onChange={(e) => setName(e.target.value)} error={errors.name} />
            <Field id="find-phone" label="가입할 때 쓴 연락처" type="tel" inputMode="numeric" placeholder="010-0000-0000"
              value={phone} onChange={(e) => setPhone(formatPhone(e.target.value))} error={errors.phone} />
            <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>아이디 찾기</button>
          </>
        ) : (
          <>
            <Field id="find-email" label="아이디 (이메일)" type="email" placeholder="name@example.com" value={email}
              onChange={(e) => setEmail(e.target.value)} error={errors.email} />
            <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>비밀번호 재설정 메일 받기</button>
          </>
        )}
        {result?.kind === 'id' && (
          <div className="found" role="status">
            {result.emails.length === 0
              ? <span>입력한 정보로 가입한 아이디가 없습니다.</span>
              : <span>가입한 아이디는 {result.emails.map((m, i) => <b key={m}>{i > 0 ? ', ' : ''}{m}</b>)}입니다.</span>}
            <button type="button" className="link" onClick={onClose}>로그인하러 가기</button>
          </div>
        )}
        {result?.kind === 'password' && (
          <div className="found" role="status">
            <span><b>{result.email}</b>로 비밀번호 재설정 링크를 보냈습니다. 30분 안에 메일의 링크로 새 비밀번호를 정해 주십시오.</span>
          </div>
        )}
      </form>
    </Modal>
  );
}
