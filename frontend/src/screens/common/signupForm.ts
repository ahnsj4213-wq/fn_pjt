import { z } from 'zod';

/** Input rules of screen spec section 2 (same as backend app/auth/router.py; the server re-checks everything). */
export const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export const PHONE_RE = /^01[016789]-\d{3,4}-\d{4}$/;
export const PASSWORD_RE = /^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d!@#$%^&*._-]{8,20}$/;

export const INPUT_MESSAGES = {
  email: '이메일 형식으로 입력해 주십시오 (예: name@example.com)',
  password: '영문과 숫자를 섞어 8~20자로 입력해 주십시오',
  phone: '010-0000-0000 형식으로 입력해 주십시오',
  passwordConfirm: '비밀번호가 서로 다릅니다',
} as const;

/** "○○을(를) 입력해 주십시오" with the right particle. */
export function required(label: string): string {
  const last = label.charCodeAt(label.length - 1);
  const hasFinal = last >= 0xac00 && last <= 0xd7a3 && (last - 0xac00) % 28 !== 0;
  return `${label}${hasFinal ? '을' : '를'} 입력해 주십시오`;
}

export const emailField = (label = '아이디') =>
  z.string().trim().min(1, required(label)).max(254, INPUT_MESSAGES.email).regex(EMAIL_RE, INPUT_MESSAGES.email);
export const passwordField = () => z.string().min(1, required('비밀번호')).regex(PASSWORD_RE, INPUT_MESSAGES.password);
export const phoneField = () => z.string().regex(PHONE_RE, INPUT_MESSAGES.phone);

export const SIGNUP_MESSAGES = {
  ...INPUT_MESSAGES,
  name: '이름을 입력해 주십시오',
  birth: '생년월일을 선택해 주십시오',
  gender: '성별을 선택해 주십시오',
  terms: '약관에 동의해야 가입할 수 있습니다',
} as const;

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export const signupSchema = z
  .object({
    name: z.string().trim().min(1, SIGNUP_MESSAGES.name).max(50, SIGNUP_MESSAGES.name),
    birth_date: z.string().min(1, SIGNUP_MESSAGES.birth).refine((v) => v <= todayIso(), SIGNUP_MESSAGES.birth),
    email: emailField(),
    password: passwordField(),
    password_confirm: z.string(),
    gender: z.enum(['M', 'F'], { message: SIGNUP_MESSAGES.gender }),
    phone: phoneField(),
    terms_agreed: z.boolean().refine((v) => v, SIGNUP_MESSAGES.terms),
  })
  // `when`: compare the two passwords even while other fields are still wrong, so every message shows at once.
  .refine((v) => v.password === v.password_confirm, {
    path: ['password_confirm'], message: SIGNUP_MESSAGES.passwordConfirm, when: () => true,
  });

export type SignupForm = z.input<typeof signupSchema>;
export const SIGNUP_FIELDS = ['name', 'birth_date', 'email', 'password', 'password_confirm', 'gender', 'phone',
  'terms_agreed'] as const;
export const maxBirthDate = todayIso;

/** Formats typed digits as 010-0000-0000 while typing. Non-digits are dropped, max 11 digits. */
export function formatPhone(raw: string): string {
  const d = raw.replace(/\D/g, '').slice(0, 11);
  if (d.length <= 3) return d;
  if (d.length <= 7) return `${d.slice(0, 3)}-${d.slice(3)}`;
  if (d.length === 10) return `${d.slice(0, 3)}-${d.slice(3, 6)}-${d.slice(6)}`;
  return `${d.slice(0, 3)}-${d.slice(3, 7)}-${d.slice(7)}`;
}
