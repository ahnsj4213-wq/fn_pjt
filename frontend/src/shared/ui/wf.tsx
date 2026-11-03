import { useId, type InputHTMLAttributes, type ReactNode, type Ref } from 'react';

/**
 * Form pieces in the wireframe markup (docs/design/wireframe-v14.html: .field, .req, .hint, .err). The error sits
 * under the input and the input gets aria-invalid, so the red border comes from the shared stylesheet.
 */
type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  required?: boolean;
  hint?: ReactNode;
  error?: string;
  ref?: Ref<HTMLInputElement>;
};

export function Field({ label, required = false, hint, error, id, ref, ...rest }: FieldProps) {
  const auto = useId();
  const inputId = id ?? auto;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errId = error ? `${inputId}-err` : undefined;
  return (
    <div className="field">
      <label htmlFor={inputId}>{label}{required && <> <em className="req" aria-hidden="true">필수</em></>}</label>
      <input {...rest} ref={ref} id={inputId} aria-required={required || undefined} aria-invalid={error ? true : undefined}
        aria-describedby={[hintId, errId].filter(Boolean).join(' ') || undefined} />
      {hint && <small id={hintId} className="hint">{hint}</small>}
      {error && <small id={errId} className="err">{error}</small>}
    </div>
  );
}

/** Field error under a group (radios, checkbox) that is not a single input. */
export function FieldError({ id, children }: { id?: string; children?: string }) {
  return children ? <small id={id} className="err">{children}</small> : null;
}
