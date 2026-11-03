import { zodResolver } from '@hookform/resolvers/zod';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useNavigate, useSearchParams } from 'react-router';
import { z } from 'zod';
import { login, type Role, type SessionOut } from '@/api/common/auth';
import { applyStartPath, getPendingApply, roleHome, safeNext, setPendingApply, setSession } from '@/app/session';
import { MESSAGES } from '@/shared/copy/fixed';
import { describeError } from '@/shared/http/errors';
import { useToast } from '@/shared/ui/overlay';
import { Field } from '@/shared/ui/wf';
import { CompanySignupModal } from './CompanySignupModal';
import { FindAccountModal } from './FindAccountModal';
import { emailField, passwordField } from './signupForm';
import { SignupModal } from './SignupModal';

const loginSchema = z.object({ email: emailField('아이디'), password: passwordField() });
type LoginForm = z.infer<typeof loginSchema>;

type Tab = 'CANDIDATE' | 'RECRUITER';
const TABS: { key: Tab; label: string; title: string; sub: string }[] = [
  { key: 'CANDIDATE', label: '지원자', title: '지원자 로그인', sub: '아이디(이메일)와 비밀번호로 로그인합니다.' },
  { key: 'RECRUITER', label: '채용담당자', title: '채용담당자 로그인', sub: '기업회원 이메일과 비밀번호로 로그인합니다.' },
];

/** Where to go after a candidate logs in or signs up (SC-C01, SC-A01). */
export function afterCandidateEntry(navigate: ReturnType<typeof useNavigate>, fallback: string, next: string | null): void {
  const pending = getPendingApply();
  if (pending) {
    setPendingApply(null);
    navigate(applyStartPath(pending), { replace: true });
    return;
  }
  navigate(next ?? fallback, { replace: true });
}

/**
 * SC-C01 로그인 (wireframe renderLogin). Any failure shows one message so account existence stays hidden (USR-04).
 * The wireframe's Google button is left out: an on-premise install has no Google sign-in unless one is configured.
 */
export function Login() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>(params.get('tab') === 'recruiter' ? 'RECRUITER' : 'CANDIDATE');
  const [modal, setModal] = useState<'signup' | 'bizsignup' | 'find' | null>(
    params.get('signup') === '1' ? (params.get('tab') === 'recruiter' ? 'bizsignup' : 'signup') : null);
  const [formError, setFormError] = useState<string | null>(null);
  const { register, handleSubmit, setFocus, formState: { errors, isSubmitting } } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
    shouldFocusError: true,
  });
  const current = TABS.find((t) => t.key === tab)!;
  const continuing = tab === 'CANDIDATE' && (params.get('apply') === '1' || Boolean(getPendingApply()));

  const entered = (sess: SessionOut, role: Role) => {
    setSession(qc, sess);
    if (role === 'CANDIDATE') {
      toast('로그인했습니다');
      afterCandidateEntry(navigate, '/', safeNext(params.get('next')));
    } else {
      toast('기업회원으로 로그인했습니다');
      navigate(safeNext(params.get('next')) ?? roleHome(role), { replace: true });
    }
  };

  const onSubmit = async (values: LoginForm) => {
    setFormError(null);
    try {
      const sess = await login({ role: tab, email: values.email.trim(), password: values.password });
      entered(sess, sess.user.role);
    } catch (e) {
      setFormError(describeError(e).message);
      setFocus('password');
    }
  };

  return (
    <div className="wrap">
      <div className="stub">
        <div className="tabs" role="tablist" aria-label="회원 유형">
          {TABS.map((t) => (
            <button key={t.key} type="button" role="tab" aria-selected={t.key === tab}
              onClick={() => { setTab(t.key); setFormError(null); }}>
              {t.label}
            </button>
          ))}
        </div>
        {continuing && <div className="notice">지원하려면 로그인이 필요합니다. 로그인하면 보던 공고로 이어집니다.</div>}
        {params.get('reason') === 'expired' && <div className="notice">{MESSAGES.SESSION_EXPIRED}</div>}
        {params.get('withdrawn') === '1' && <div className="notice ok">탈퇴가 완료되었습니다. 그동안 이용해 주셔서 감사합니다.</div>}
        {params.get('reset') === '1' && <div className="notice ok">새 비밀번호로 로그인해 주십시오.</div>}
        <h1>{current.title}</h1>
        <p className="sub">{current.sub}</p>
        {formError && <div className="notice bad" role="alert">{formError}</div>}
        <form className="form" onSubmit={handleSubmit(onSubmit)} noValidate aria-label={current.title}>
          <Field label="아이디 (이메일)" type="email" autoComplete="username" {...register('email')} error={errors.email?.message} />
          <Field label="비밀번호" type="password" autoComplete="current-password" {...register('password')}
            error={errors.password?.message} />
          <button type="submit" className="btn btn-primary" disabled={isSubmitting} aria-busy={isSubmitting || undefined}>로그인</button>
        </form>
        <div className="alt links">
          {tab === 'CANDIDATE' && (
            <>
              <button type="button" className="link" onClick={() => setModal('find')}>아이디·비밀번호 찾기</button>
              <span aria-hidden="true">·</span>
            </>
          )}
          {tab === 'CANDIDATE'
            ? <button type="button" className="link" onClick={() => setModal('signup')}>회원가입</button>
            : <button type="button" className="link" onClick={() => setModal('bizsignup')}>기업회원 가입</button>}
        </div>
      </div>
      {modal === 'signup' && <SignupModal onClose={() => setModal(null)} onDone={(sess) => {
        setModal(null);
        setSession(qc, sess);
        toast('가입했습니다. 반갑습니다!');
        afterCandidateEntry(navigate, '/me/resume?welcome=1', null);
      }} />}
      {modal === 'bizsignup' && <CompanySignupModal onClose={() => setModal(null)} onDone={(sess, pending) => {
        setModal(null);
        setSession(qc, sess);
        toast(pending ? '가입했습니다. 운영자 승인 후 공고를 게시할 수 있습니다' : '가입과 기업 인증을 마쳤습니다');
        navigate(roleHome('RECRUITER'), { replace: true });
      }} />}
      {modal === 'find' && <FindAccountModal onClose={() => setModal(null)} />}
    </div>
  );
}
