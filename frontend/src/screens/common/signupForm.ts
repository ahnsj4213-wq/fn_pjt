import { z } from 'zod';

/** Same rules as backend SignupIn (app/auth/router.py); the server re-checks everything. */
export const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export const PHONE_RE = /^010-\d{4}-\d{4}$/;

export const SIGNUP_MESSAGES = {
  nameRequired: '이름을 입력해 주세요.',
  nameTooLong: '이름은 50자 이하로 입력해 주세요.',
  email: '이메일 형식이 올바르지 않습니다.',
  passwordLength: '비밀번호는 8자 이상 128자 이하로 입력해 주세요.',
  passwordEqualsEmail: '이메일과 같은 비밀번호는 사용할 수 없습니다.',
  passwordConfirm: '비밀번호가 일치하지 않습니다.',
  phone: '연락처는 010-0000-0000 형식으로 입력해 주세요.',
} as const;

// USR-02: exactly name, email, password, password_confirm, phone. No birth date or gender (AC-01).
export const signupSchema = z
  .object({
    name: z.string().trim().min(1, SIGNUP_MESSAGES.nameRequired).max(50, SIGNUP_MESSAGES.nameTooLong),
    email: z.string().trim().max(254, SIGNUP_MESSAGES.email).regex(EMAIL_RE, SIGNUP_MESSAGES.email),
    password: z.string().min(8, SIGNUP_MESSAGES.passwordLength).max(128, SIGNUP_MESSAGES.passwordLength),
    password_confirm: z.string(),
    phone: z.string().regex(PHONE_RE, SIGNUP_MESSAGES.phone),
  })
  .refine((v) => v.password === v.password_confirm, { path: ['password_confirm'], message: SIGNUP_MESSAGES.passwordConfirm })
  .refine((v) => v.password.toLowerCase() !== v.email.trim().toLowerCase(), {
    path: ['password'],
    message: SIGNUP_MESSAGES.passwordEqualsEmail,
  });

export type SignupForm = z.infer<typeof signupSchema>;
export const SIGNUP_FIELDS = ['name', 'email', 'password', 'password_confirm', 'phone'] as const;

/** Formats typed digits as 010-0000-0000 while typing. Non-digits are dropped, max 11 digits. */
export function formatPhone(raw: string): string {
  const d = raw.replace(/\D/g, '').slice(0, 11);
  if (d.length <= 3) return d;
  if (d.length <= 7) return `${d.slice(0, 3)}-${d.slice(3)}`;
  return `${d.slice(0, 3)}-${d.slice(3, 7)}-${d.slice(7)}`;
}
