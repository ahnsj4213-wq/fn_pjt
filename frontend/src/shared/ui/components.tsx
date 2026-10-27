import { useEffect, useId, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type Ref } from 'react';
import s from './ui.module.css';

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary'; busy?: boolean };

export function Button({ variant = 'secondary', busy = false, className, disabled, children, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      {...rest}
      type={type}
      className={[s.button, s[variant], className].filter(Boolean).join(' ')}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {children}
    </button>
  );
}

type TextFieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  error?: string;
  hint?: string;
  ref?: Ref<HTMLInputElement>;
};

/** Label + input + hint + error, wired with aria-describedby / aria-invalid. */
export function TextField({ label, error, hint, id, ref, ...rest }: TextFieldProps) {
  const auto = useId();
  const inputId = id ?? auto;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  return (
    <div className={s.field}>
      <label className={s.label} htmlFor={inputId}>{label}</label>
      <input
        {...rest}
        ref={ref}
        id={inputId}
        className={s.input}
        aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(' ') || undefined}
      />
      {hint && <span id={hintId} className={s.hint}>{hint}</span>}
      {error && <span id={errorId} className={s.fieldError}>{error}</span>}
    </div>
  );
}

export function Notice({ kind = 'info', children }: { kind?: 'info' | 'error' | 'success'; children: ReactNode }) {
  const cls = { info: s.noticeInfo, error: s.noticeError, success: s.noticeSuccess }[kind];
  return (
    <div className={`${s.notice} ${cls}`} role={kind === 'error' ? 'alert' : 'status'}>
      {children}
    </div>
  );
}

/** Page shell: sets document.title and moves focus to the h1 on mount (design D 3.12). */
export function Page({ title, narrow = false, children }: { title: string; narrow?: boolean; children?: ReactNode }) {
  const h1 = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    document.title = `${title} | AI 채용 플랫폼`;
    h1.current?.focus();
  }, [title]);
  return (
    <section className={`${s.page} ${narrow ? s.narrow : ''}`}>
      <h1 ref={h1} tabIndex={-1} className={s.pageTitle}>{title}</h1>
      {children}
    </section>
  );
}

/** Placeholder body for screens that wait on the API contract (docs/trd/04, 06). */
export function TodoPanel({ featureIds, children }: { featureIds: string[]; children?: ReactNode }) {
  return (
    <div className={s.todo} data-testid="todo-panel">
      <p className={s.ids}>관련 기능: {featureIds.join(', ')}</p>
      <p><strong>TODO</strong> 화면 구현 예정 (API 계약 확정 후)</p>
      {children}
    </div>
  );
}

/** Shows a loading line only after 300 ms so fast responses do not flash (design D 3.12). */
export function Loading({ label = '불러오는 중입니다' }: { label?: string }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setVisible(true), 300);
    return () => clearTimeout(t);
  }, []);
  return <p className={s.loading} role="status" aria-live="polite">{visible ? label : ''}</p>;
}

export const uiStyles = s;
