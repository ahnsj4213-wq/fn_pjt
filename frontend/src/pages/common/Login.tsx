import { zodResolver } from '@hookform/resolvers/zod';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { z } from 'zod';
import { login } from '@/api/common/auth';
import { roleHome, safeNext, setSession } from '@/app/session';
import { MESSAGES } from '@/shared/copy/fixed';
import { describeError } from '@/shared/http/errors';
import { Button, Notice, Page, TextField, uiStyles } from '@/shared/ui/components';
import { EMAIL_RE } from './signupForm';

const loginSchema = z.object({
  role: z.enum(['CANDIDATE', 'RECRUITER']),
  email: z.string().trim().regex(EMAIL_RE, '이메일 형식이 올바르지 않습니다.'),
  password: z.string().min(1, '비밀번호를 입력해 주세요.'),
});
type LoginForm = z.infer<typeof loginSchema>;

/** Role-selecting login (USR-01). Any failure shows one message so account existence stays hidden (USR-04). */
export function Login() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { role: 'CANDIDATE', email: '', password: '' },
  });

  const onSubmit = async (values: LoginForm) => {
    setFormError(null);
    try {
      const s = await login({ ...values, email: values.email.trim() });
      setSession(qc, s);
      navigate(safeNext(params.get('next')) ?? roleHome(s.user.role), { replace: true });
    } catch (e) {
      setFormError(describeError(e).message);
    }
  };

  return (
    <Page title="로그인" narrow>
      {params.get('reason') === 'expired' && <Notice>{MESSAGES.SESSION_EXPIRED}</Notice>}
      {params.get('withdrawn') === '1' && <Notice kind="success">탈퇴가 완료되었습니다. 그동안 이용해 주셔서 감사합니다.</Notice>}
      {params.get('signedUp') === '1' && <Notice kind="success">가입이 완료되었습니다. 로그인해 주세요.</Notice>}
      {formError && <Notice kind="error">{formError}</Notice>}
      <form onSubmit={handleSubmit(onSubmit)} noValidate aria-label="로그인">
        <fieldset className={uiStyles.fieldset}>
          <legend className={uiStyles.label}>로그인 유형</legend>
          <div className={uiStyles.radios}>
            <label><input type="radio" value="CANDIDATE" {...register('role')} /> 지원자</label>
            <label><input type="radio" value="RECRUITER" {...register('role')} /> 채용 담당자</label>
          </div>
        </fieldset>
        <TextField label="이메일" type="email" autoComplete="username" {...register('email')} error={errors.email?.message} />
        <TextField label="비밀번호" type="password" autoComplete="current-password" {...register('password')}
          error={errors.password?.message} />
        <Button type="submit" variant="primary" busy={isSubmitting}>로그인</Button>
      </form>
      <p>지원자는 <Link to="/signup">회원가입</Link> 후 이용할 수 있습니다. 담당자 계정은 운영자가 발급합니다.</p>
    </Page>
  );
}
