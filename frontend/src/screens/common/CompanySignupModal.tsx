import { useState, type FormEvent } from 'react';
import { companySignup, sendCompanyCode, uploadCompanyEvidence, type SessionOut } from '@/api/common/auth';
import { describeError } from '@/shared/http/errors';
import { Modal, useToast } from '@/shared/ui/overlay';
import { Field } from '@/shared/ui/wf';
import { EMAIL_RE, INPUT_MESSAGES, PASSWORD_RE, required } from './signupForm';

/** Screen spec SC-R01: personal mail services are never approved automatically. */
export const FREE_MAIL = ['gmail.com', 'naver.com', 'daum.net', 'hanmail.net', 'kakao.com', 'nate.com', 'outlook.com', 'hotmail.com'];
const BIZ_RE = /^\d{3}-\d{2}-\d{5}$/;
const EVIDENCE_EXT = /\.(pdf|jpe?g|png)$/i;

type Errors = Partial<Record<'co' | 'biz' | 'name' | 'mail' | 'code' | 'pw' | 'pw2' | 'file', string>>;

export const isFreeMail = (email: string) => FREE_MAIL.includes((email.split('@')[1] ?? '').trim().toLowerCase());

/** Digits typed as 000-00-00000. */
export function formatBiz(raw: string): string {
  const d = raw.replace(/\D/g, '').slice(0, 10);
  if (d.length <= 3) return d;
  if (d.length <= 5) return `${d.slice(0, 3)}-${d.slice(3)}`;
  return `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;
}

/**
 * SC-R01 기업회원 가입 (wireframe openBizSignup). The mail code comes first; company-domain mail is approved at
 * once, personal mail waits for the operator and attaches a document (uploaded right after signup, with the new session).
 */
export function CompanySignupModal({ onClose, onDone }: { onClose: () => void; onDone: (s: SessionOut, pending: boolean) => void }) {
  const toast = useToast();
  const [v, setV] = useState({ co: '', biz: '', name: '', mail: '', code: '', pw: '', pw2: '' });
  const [file, setFile] = useState<File | null>(null);
  const [sent, setSent] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const free = isFreeMail(v.mail);
  const set = (k: keyof typeof v) => (e: { target: { value: string } }) => setV((s) => ({ ...s, [k]: e.target.value }));

  const mailError = () => (!v.mail.trim() ? required('회사 이메일') : !EMAIL_RE.test(v.mail.trim()) ? INPUT_MESSAGES.email : '');

  const sendCode = async () => {
    const m = mailError();
    if (m) { setErrors((e) => ({ ...e, mail: m })); return; }
    setErrors((e) => ({ ...e, mail: undefined }));
    setFormError(null);
    try {
      await sendCompanyCode(v.mail.trim());
      setSent(true);
      toast('인증번호를 보냈습니다');
    } catch (e) {
      setFormError(describeError(e).message);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const next: Errors = {};
    if (!v.co.trim()) next.co = required('회사명');
    if (!BIZ_RE.test(v.biz.trim())) next.biz = '000-00-00000 형식으로 입력해 주십시오';
    if (!v.name.trim()) next.name = required('담당자 이름');
    next.mail = mailError() || (sent ? '' : '인증번호를 먼저 받아 주십시오');
    if (sent && !/^\d{6}$/.test(v.code.trim())) next.code = '인증번호 6자리를 입력해 주십시오';
    if (!v.pw) next.pw = required('비밀번호');
    else if (!PASSWORD_RE.test(v.pw)) next.pw = INPUT_MESSAGES.password;
    if (v.pw2 !== v.pw) next.pw2 = INPUT_MESSAGES.passwordConfirm;
    if (file && !EVIDENCE_EXT.test(file.name)) next.file = 'PDF, JPG, PNG 파일만 올릴 수 있습니다';
    for (const k of Object.keys(next) as (keyof Errors)[]) if (!next[k]) delete next[k];
    setErrors(next);
    if (Object.keys(next).length) return;
    setBusy(true);
    setFormError(null);
    try {
      const sess = await companySignup({ company_name: v.co.trim(), biz_no: v.biz.trim(), name: v.name.trim(),
        email: v.mail.trim(), code: v.code.trim(), password: v.pw, password_confirm: v.pw2 });
      if (free && file) {
        try { await uploadCompanyEvidence(file); } catch { toast('서류를 올리지 못했습니다. 기업 관리에서 다시 올려 주십시오'); }
      }
      onDone(sess, free);
    } catch (err) {
      const d = describeError(err);
      if (d.code === 'EMAIL_UNAVAILABLE') setErrors({ mail: '사용할 수 없는 이메일입니다' });
      else if (d.code === 'SIGNUP_CODE_INVALID') setErrors({ code: d.message });
      else setFormError(d.message);
    } finally {
      setBusy(false);
    }
  };

  const hint = free ? '개인 메일은 자동 승인되지 않습니다. 증빙 서류를 올리면 운영자가 확인한 뒤 승인합니다.'
    : sent ? `${v.mail}로 인증번호를 보냈습니다.` : '회사 도메인 메일로 인증하면 가입하자마자 승인됩니다.';

  return (
    <Modal title="기업회원 가입" onClose={onClose}>
      <p className="sub" style={{ margin: 0 }}>채용담당자는 이메일과 비밀번호로 가입합니다. 회사 메일로 인증하면 바로 승인됩니다.</p>
      {formError && <div className="notice bad" role="alert">{formError}</div>}
      <form className="form" onSubmit={submit} noValidate aria-label="기업회원 가입">
        <div className="row2">
          <Field label="회사명" id="bs-co" value={v.co} onChange={set('co')} error={errors.co} />
          <Field label="사업자등록번호" id="bs-biz" inputMode="numeric" placeholder="000-00-00000" value={v.biz}
            onChange={(e) => setV((s) => ({ ...s, biz: formatBiz(e.target.value) }))} error={errors.biz} />
        </div>
        <Field label="담당자 이름" id="bs-name" autoComplete="name" value={v.name} onChange={set('name')} error={errors.name} />
        <div className="field"><label htmlFor="bs-mail">회사 이메일 (아이디)</label>
          <div className="inline">
            <input id="bs-mail" type="email" autoComplete="email" value={v.mail} aria-invalid={errors.mail ? true : undefined}
              onChange={(e) => { set('mail')(e); setSent(false); }} />
            <button className="btn" type="button" onClick={() => void sendCode()}>인증번호 받기</button>
          </div>
          <small className={`hint${free ? ' warn' : sent ? ' ok' : ''}`}>{hint}</small>
          {errors.mail && <small className="err">{errors.mail}</small>}
        </div>
        {sent && <Field label="인증번호" id="bs-code" inputMode="numeric" maxLength={6} autoComplete="one-time-code"
          value={v.code} onChange={set('code')} error={errors.code} />}
        {free && (
          <div className="field"><label htmlFor="bs-file">재직증명서 또는 사업자등록증</label>
            <input id="bs-file" type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            {errors.file && <small className="err">{errors.file}</small>}</div>
        )}
        <div className="row2">
          <Field label="비밀번호" id="bs-pw" type="password" autoComplete="new-password" placeholder="영문+숫자 8~20자"
            value={v.pw} onChange={set('pw')} error={errors.pw} />
          <Field label="비밀번호 확인" id="bs-pw2" type="password" autoComplete="new-password" value={v.pw2}
            onChange={set('pw2')} error={errors.pw2} />
        </div>
        <div className="mfoot">
          <button className="btn btn-primary" type="submit" disabled={busy} aria-busy={busy || undefined}>가입하기</button>
        </div>
      </form>
    </Modal>
  );
}
