import { zodResolver } from '@hookform/resolvers/zod';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router';
import { signup } from '@/api/common/auth';
import { MESSAGES } from '@/shared/copy/fixed';
import { describeError } from '@/shared/http/errors';
import { Button, Notice, Page, TextField } from '@/shared/ui/components';
import { formatPhone, SIGNUP_FIELDS, signupSchema, type SignupForm } from './signupForm';

/** Candidate signup (USR-02). Recruiter accounts are issued by the operator (USR-03), so no role choice here. */
export function Signup() {
  const navigate = useNavigate();
  const [formError, setFormError] = useState<string | null>(null);
  const { register, handleSubmit, setError, setValue, formState: { errors, isSubmitting } } = useForm<SignupForm>({
    resolver: zodResolver(signupSchema),
    defaultValues: { name: '', email: '', password: '', password_confirm: '', phone: '' },
  });

  const onSubmit = async (values: SignupForm) => {
    setFormError(null);
    try {
      await signup({
        name: values.name.trim(),
        email: values.email.trim(),
        password: values.password,
        password_confirm: values.password_confirm,
        phone: values.phone,
      });
      navigate('/login?signedUp=1', { replace: true });
    } catch (e) {
      const d = describeError(e);
      if (d.code === 'EMAIL_UNAVAILABLE') {
        setError('email', { message: MESSAGES.EMAIL_UNAVAILABLE });
        return;
      }
      let mapped = false;
      for (const [field, message] of Object.entries(d.fieldErrors)) {
        if ((SIGNUP_FIELDS as readonly string[]).includes(field)) {
          setError(field as keyof SignupForm, { message });
          mapped = true;
        }
      }
      setFormError(mapped ? d.formErrors[0] ?? null : d.formErrors[0] ?? d.message);
    }
  };

  const phone = register('phone');

  return (
    <Page title="지원자 회원가입" narrow>
      {formError && <Notice kind="error">{formError}</Notice>}
      <form onSubmit={handleSubmit(onSubmit)} noValidate aria-label="지원자 회원가입">
        <TextField label="이름" autoComplete="name" {...register('name')} error={errors.name?.message} />
        <TextField label="이메일(아이디)" type="email" autoComplete="email" {...register('email')} error={errors.email?.message} />
        <TextField label="비밀번호" type="password" autoComplete="new-password" hint="8자 이상"
          {...register('password')} error={errors.password?.message} />
        <TextField label="비밀번호 확인" type="password" autoComplete="new-password"
          {...register('password_confirm')} error={errors.password_confirm?.message} />
        <TextField
          label="연락처"
          type="tel"
          inputMode="numeric"
          autoComplete="tel"
          placeholder="010-0000-0000"
          hint="010-0000-0000 형식"
          {...phone}
          onChange={(e) => {
            setValue('phone', formatPhone(e.target.value), { shouldValidate: Boolean(errors.phone) });
          }}
          error={errors.phone?.message}
        />
        <Button type="submit" variant="primary" busy={isSubmitting}>가입하기</Button>
      </form>
      <p>이미 계정이 있으면 <Link to="/login">로그인</Link></p>
    </Page>
  );
}
