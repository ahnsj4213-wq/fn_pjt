import { useId, useState, type KeyboardEvent, type ReactNode, type Ref, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { describeError } from '@/shared/http/errors';
import { Button, Loading, Notice } from './components';
import s from './ui.module.css';

type SelectFieldProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label: string;
  error?: string;
  options: { value: string; label: string }[];
  placeholder?: string;
  ref?: Ref<HTMLSelectElement>;
};

export function SelectField({ label, error, options, placeholder, id, ref, ...rest }: SelectFieldProps) {
  const auto = useId();
  const selectId = id ?? auto;
  const errorId = error ? `${selectId}-error` : undefined;
  return (
    <div className={s.field}>
      <label className={s.label} htmlFor={selectId}>{label}</label>
      <select {...rest} ref={ref} id={selectId} className={s.input} aria-invalid={error ? true : undefined} aria-describedby={errorId}>
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {error && <span id={errorId} className={s.fieldError}>{error}</span>}
    </div>
  );
}

type TextAreaFieldProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string;
  error?: string;
  hint?: string;
  ref?: Ref<HTMLTextAreaElement>;
};

export function TextAreaField({ label, error, hint, id, ref, ...rest }: TextAreaFieldProps) {
  const auto = useId();
  const areaId = id ?? auto;
  const hintId = hint ? `${areaId}-hint` : undefined;
  const errorId = error ? `${areaId}-error` : undefined;
  return (
    <div className={s.field}>
      <label className={s.label} htmlFor={areaId}>{label}</label>
      <textarea {...rest} ref={ref} id={areaId} className={`${s.input} ${s.textarea}`} aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errorId].filter(Boolean).join(' ') || undefined} />
      {hint && <span id={hintId} className={s.hint}>{hint}</span>}
      {error && <span id={errorId} className={s.fieldError}>{error}</span>}
    </div>
  );
}

/** WAI-ARIA tabs with arrow-key navigation. Only the selected panel is rendered (lazy data loading). */
export function Tabs<K extends string>({ tabs, selected, onSelect, label, children }: {
  tabs: { key: K; label: string }[];
  selected: K;
  onSelect: (key: K) => void;
  label: string;
  children: ReactNode;
}) {
  const base = useId();
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const next = (i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    onSelect(tabs[next].key);
    document.getElementById(`${base}-tab-${tabs[next].key}`)?.focus();
  };
  return (
    <>
      <div role="tablist" aria-label={label} className={s.tablist}>
        {tabs.map((t, i) => (
          <button key={t.key} id={`${base}-tab-${t.key}`} role="tab" type="button" className={s.tab}
            aria-selected={t.key === selected} aria-controls={`${base}-panel`} tabIndex={t.key === selected ? 0 : -1}
            onClick={() => onSelect(t.key)} onKeyDown={(e) => onKey(e, i)}>
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${base}-panel`} aria-labelledby={`${base}-tab-${selected}`} tabIndex={0}>{children}</div>
    </>
  );
}

/**
 * Two-step button for irreversible actions (publish, close, delete, decision). Inline instead of a modal
 * so it needs no style-injecting dialog library under the strict CSP (D-12).
 */
export function ConfirmButton({ label, confirmText, confirmLabel = '확인', onConfirm, busy, disabled, variant = 'secondary' }: {
  label: string;
  confirmText: string;
  confirmLabel?: string;
  onConfirm: () => void;
  busy?: boolean;
  disabled?: boolean;
  variant?: 'primary' | 'secondary';
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) return <Button variant={variant} onClick={() => setAsking(true)} busy={busy} disabled={disabled}>{label}</Button>;
  return (
    <span className={s.confirm} role="group" aria-label={label}>
      <span>{confirmText}</span>
      <Button variant="primary" onClick={() => { setAsking(false); onConfirm(); }}>{confirmLabel}</Button>
      <Button onClick={() => setAsking(false)}>취소</Button>
    </span>
  );
}

/** Loading / error rendering for a query (design D 3.12). */
export function QueryState({ isPending, error, onRetry, children }: {
  isPending: boolean;
  error: unknown;
  onRetry?: () => void;
  children: () => ReactNode;
}) {
  if (error) {
    return (
      <Notice kind="error">
        {describeError(error).message}
        {onRetry && <> <Button onClick={onRetry}>다시 시도</Button></>}
      </Notice>
    );
  }
  if (isPending) return <Loading />;
  return <>{children()}</>;
}

export function Badge({ kind, children }: { kind?: 'warn' | 'info'; children: ReactNode }) {
  const cls = kind === 'warn' ? s.badgeWarn : kind === 'info' ? s.badgeInfo : '';
  return <span className={`${s.badge} ${cls}`}>{children}</span>;
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className={s.muted}>{children}</p>;
}

/** User-supplied text (resume, answers, postings, notes): always plain text, line breaks kept (SC-31). */
export function PlainText({ text }: { text: string | null | undefined }) {
  if (!text) return <span className={s.muted}>-</span>;
  return <p className={s.plain}>{text}</p>;
}
