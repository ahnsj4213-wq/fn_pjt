import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * Small window (screen spec "작은 창"): closes with the × button, a click outside or Esc, and gives focus back to the
 * element that opened it. A native <dialog> keeps focus inside and needs no style-injecting library (strict CSP).
 */
export function Modal({ title, onClose, children, wide = false, footer }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  footer?: ReactNode;
}) {
  return (
    <DialogHost label={title} onClose={onClose}>
      <div className={`mdialog${wide ? ' wide' : ''}`}>
        <div className="mhead">
          <h2>{title}</h2>
          <button type="button" className="x" onClick={onClose} aria-label="닫기">×</button>
        </div>
        {children}
        {footer && <div className="mfoot">{footer}</div>}
      </div>
    </DialogHost>
  );
}

/**
 * The native <dialog> behind every small window (wireframe .gmodal): modal focus, Esc, a click on the backdrop closes,
 * and focus returns to the opener. The visible box (.mdialog, .gdialog) is the child.
 */
export function DialogHost({ label, labelledBy, onClose, children }: {
  label?: string;
  labelledBy?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const d = ref.current!;
    if (!d.open) {
      if (typeof d.showModal === 'function') d.showModal();
      else d.setAttribute('open', ''); // test DOMs without <dialog> support
    }
    return () => {
      if (d.open && typeof d.close === 'function') d.close();
      opener?.focus?.();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className="wf-host"
      aria-label={labelledBy ? undefined : label}
      aria-labelledby={labelledBy}
      onCancel={(e) => { e.preventDefault(); close.current(); }}
      // The host fills the screen; a click that lands on it (not on the box inside) is a click outside.
      onMouseDown={(e) => { if (e.target === e.currentTarget) close.current(); }}
    >
      {children}
    </dialog>
  );
}

type ToastFn = (message: string) => void;
const ToastContext = createContext<ToastFn>(() => {});
const TOAST_MS = 2400; // screen spec 2: about 2.4 s

/** One-line notice at the bottom centre after save, delete, send ... (screen spec "알림 메시지"). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<{ text: string; id: number } | null>(null);
  const show = useCallback<ToastFn>((text) => setMessage({ text, id: Date.now() }), []);
  useEffect(() => {
    if (!message) return;
    const t = setTimeout(() => setMessage(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [message]);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div role="status" aria-live="polite">
        {message && <div key={message.id} className="toast" data-testid="toast">{message.text}</div>}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastFn {
  return useContext(ToastContext);
}
