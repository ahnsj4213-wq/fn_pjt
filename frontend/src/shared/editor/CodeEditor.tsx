import { python } from '@codemirror/lang-python';
import { EditorState } from '@codemirror/state';
import { basicSetup, EditorView } from 'codemirror';
import { useEffect, useRef } from 'react';

/**
 * CodeMirror 6 Python editor, bundled locally (COD-02, AC-22). Used by the candidate coding screen
 * and, read-only, by the recruiter's submitted-code view.
 * TODO(CSP): confirm no securitypolicyviolation under the production CSP; otherwise wire EditorView.cspNonce.
 */
export function CodeEditor({ value, onChange, readOnly = false, label }: {
  value: string;
  onChange?: (code: string) => void;
  readOnly?: boolean;
  label: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const changeRef = useRef(onChange);
  changeRef.current = onChange;

  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          python(),
          EditorState.tabSize.of(4),
          EditorState.readOnly.of(readOnly),
          EditorView.editable.of(!readOnly),
          EditorView.contentAttributes.of({ 'aria-label': label }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) changeRef.current?.(u.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // The document is owned by CodeMirror after mount; external value changes go through the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readOnly, label]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div ref={host} />;
}
