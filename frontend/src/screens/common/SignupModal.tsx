import { zodResolver } from '@hookform/resolvers/zod';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { login, signup, type SessionOut } from '@/api/common/auth';
import { MESSAGES } from '@/shared/copy/fixed';
import { describeError } from '@/shared/http/errors';
import { Modal } from '@/shared/ui/overlay';
import { Field, FieldError } from '@/shared/ui/wf';
import { formatPhone, maxBirthDate, SIGNUP_FIELDS, signupSchema, type SignupForm } from './signupForm';

/** Values of the wireframe "예시 값 채우기" button (screen spec SC-A01). */
const EXAMPLE: SignupForm = {
  name: '김지원', birth_date: '1999-03-14', email: 'jiwon@gmail.com', password: 'jiwon1234', password_confirm: 'jiwon1234',
  gender: 'F', phone: '010-2345-6789', terms_agreed: true,
};

/**
 * SC-A01 지원자 회원가입 (wireframe openSeekerSignup). Signing up also signs in (the caller decides where to go next).
 * Closing the window drops what was typed.
 */
export function SignupModal({ onClose, onDone }: { onClose: () => void; onDone: (s: SessionOut) => void }) {
  const [formError, setFormError] = useState<string | null>(null);
  const { register, handleSubmit, setError, setValue, reset, formState: { errors, isSubmitting } } = useForm<SignupForm>({
    resolver: zodResolver(signupSchema),
    defaultValues: { name: '', birth_date: '', email: '', password: '', password_confirm: '', phone: '', terms_agreed: false },
    shouldFocusError: true,
  });

  const onSubmit = async (values: SignupForm) => {
    setFormError(null);
    const email = values.email.trim();
    try {
      await signup({
        name: values.name.trim(), birth_date: values.birth_date, email, password: values.password,
        password_confirm: values.password_confirm, gender: values.gender as 'M' | 'F', phone: values.phone,
        terms_agreed: values.terms_agreed,
      });
    } catch (e) {
      const d = describeError(e);
      if (d.code === 'EMAIL_UNAVAILABLE') {
        setError('email', { message: MESSAGES.EMAIL_UNAVAILABLE }, { shouldFocus: true });
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
      return;
    }
    try {
      onDone(await login({ role: 'CANDIDATE', email, password: values.password }));
    } catch (e) {
      setFormError(describeError(e).message);
    }
  };

  return (
    <Modal title="지원자 회원가입" onClose={onClose}>
      {formError && <div className="notice bad" role="alert">{formError}</div>}
      <form className="form" onSubmit={handleSubmit(onSubmit)} noValidate aria-label="지원자 회원가입">
        {/* register() runs in field order: the first wrong field gets the cursor (screen spec 2). */}
        <div className="row2">
          <Field label="이름" required autoComplete="name" {...register('name')} error={errors.name?.message} />
          <Field label="생년월일" required type="date" max={maxBirthDate()} {...register('birth_date')} error={errors.birth_date?.message} />
        </div>
        <Field label="아이디 (이메일)" required type="email" autoComplete="email" placeholder="name@example.com"
          hint="아이디로 쓰는 이메일로 면접 결과와 일정 안내를 보냅니다." {...register('email')} error={errors.email?.message} />
        <div className="row2">
          <Field label="비밀번호" required type="password" autoComplete="new-password" placeholder="영문+숫자 8~20자"
            {...register('password')} error={errors.password?.message} />
          <Field label="비밀번호 확인" required type="password" autoComplete="new-password"
            {...register('password_confirm')} error={errors.password_confirm?.message} />
        </div>
        <div className="row2">
          <div className="field">
            <span className="flabel" id="su-g-l">성별 <em className="req" aria-hidden="true">필수</em></span>
            <div className="radios" role="radiogroup" aria-labelledby="su-g-l"
              aria-describedby={errors.gender ? 'su-g-err' : undefined}>
              <label><input type="radio" value="M" {...register('gender')} /> 남</label>
              <label><input type="radio" value="F" {...register('gender')} /> 여</label>
            </div>
            <FieldError id="su-g-err">{errors.gender?.message}</FieldError>
          </div>
          <Field label="연락처" required type="tel" inputMode="numeric" autoComplete="tel" placeholder="010-0000-0000"
            {...register('phone')}
            onChange={(e) => setValue('phone', formatPhone(e.target.value), { shouldValidate: Boolean(errors.phone) })}
            error={errors.phone?.message} />
        </div>
        <div className="field">
          <label className="consent" style={{ margin: 0 }}>
            <input type="checkbox" {...register('terms_agreed')} aria-invalid={errors.terms_agreed ? true : undefined} />
            <span>이용약관과 개인정보 처리방침에 동의합니다 (필수)</span>
          </label>
          <FieldError>{errors.terms_agreed?.message}</FieldError>
        </div>
        <div className="mfoot">
          <button type="button" className="btn" onClick={() => reset(EXAMPLE)}>예시 값 채우기</button>
          <button type="submit" className="btn btn-primary" disabled={isSubmitting} aria-busy={isSubmitting || undefined}>가입하기</button>
        </div>
      </form>
    </Modal>
  );
}
