import { zodResolver } from '@hookform/resolvers/zod';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { useNavigate, useSearchParams } from 'react-router';
import { z } from 'zod';
import { confirmPasswordReset } from '@/api/common/auth';
import { describeError } from '@/shared/http/errors';
import { Field } from '@/shared/ui/wf';
import { INPUT_MESSAGES, passwordField } from './signupForm';

const schema = z.object({ password: passwordField(), password_confirm: z.string() })
  .refine((v) => v.password === v.password_confirm, { path: ['password_confirm'], message: INPUT_MESSAGES.passwordConfirm });
type Form = z.infer<typeof schema>;

/** Target of the 30-minute link in the reset mail (SC-A12, SC-A11), in the wireframe login card (.stub). */
export function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') ?? '';
  const [failure, setFailure] = useState<string | null>(null);
  const { register, handleSubmit, formState: { errors, isSubmitting } } = useForm<Form>({
    resolver: zodResolver(schema), defaultValues: { password: '', password_confirm: '' },
  });

  const onSubmit = async (v: Form) => {
    setFailure(null);
    try {
      await confirmPasswordReset({ token, ...v });
      navigate('/login?reset=1', { replace: true });
    } catch (e) {
      setFailure(describeError(e).message);
    }
  };

  return (
    <div className="wrap">
      <div className="stub">
        <h1>새 비밀번호 정하기</h1>
        <p className="sub">메일의 링크는 30분 동안 쓸 수 있습니다.</p>
        {!token && <div className="notice bad" role="alert">링크가 올바르지 않습니다. 메일의 링크를 다시 눌러 주십시오.</div>}
        {failure && <div className="notice bad" role="alert">{failure}</div>}
        <form className="form" onSubmit={handleSubmit(onSubmit)} noValidate aria-label="새 비밀번호 정하기">
          <Field label="새 비밀번호" type="password" autoComplete="new-password" placeholder="영문+숫자 8~20자"
            {...register('password')} error={errors.password?.message} />
          <Field label="새 비밀번호 확인" type="password" autoComplete="new-password"
            {...register('password_confirm')} error={errors.password_confirm?.message} />
          <button type="submit" className="btn btn-primary" disabled={isSubmitting || !token}>비밀번호 바꾸기</button>
        </form>
      </div>
    </div>
  );
}
